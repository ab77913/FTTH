"""
Pipeline Runner â€” 6-Agent Sequential Orchestrator
===================================================
Runs every address through all 6 agents in order.
Each agent receives the IDs of addresses belonging to the job; it loads
what it needs (including prior-agent results) from the DB itself.

Stage routing:
  â€¢ Agent 2 (unified geocoding) â†’ ALL addresses first (reverse coord-only, validate, forward)
  â€¢ Agent 1 (Smarty + Melissa) â†’ real postal addresses after geocoding (KMZ labels excluded)
  â€¢ Agents 3â€“6 â†’ ALL addresses that pass confidence gates

A2A messaging (optional):
  When RabbitMQ is reachable, each stage publishes start/complete events
  on the AgentBus (Peer-to-Peer + Broadcast).  If the broker is unavailable
  the pipeline runs identically without messaging.

Callable entry point: run_full_pipeline(job_id, progress_callback)
"""
from __future__ import annotations

import logging
import math
from dataclasses import replace
from typing import Any, Callable
from uuid import UUID

from data_ingestion.agents.agent0_house_discovery import run_agent0_for_job
from data_ingestion.agents.agent1_address_validation import run_agent1_for_job
from data_ingestion.agents.agent2_geocoding          import run_geocoding_for_job
from data_ingestion.agents.agent3_parcel             import run_agent3_for_job
from data_ingestion.agents.agent4_building           import run_agent4_for_job
from data_ingestion.agents.agent5_0_offline_ocr      import default_config as agent50_default_config
from data_ingestion.agents.agent5_0_offline_ocr      import run_agent50_for_job
from data_ingestion.agents.agent5_streetview         import run_agent5_for_job
from data_ingestion.agents.agent6_finalization       import run_agent6_for_job
from data_ingestion.agents.agent7_neighborhood_discovery import run_agent7_for_job
from data_ingestion.agents.reverse_geocoder          import is_kmz_placemark_label
from data_ingestion.utils.address_match            import resolve_upload_address_line
from data_ingestion.utils.res_com_addressing import (
    ENABLE_RES_COM_ADDRESSING,
    duplicate_key_for_address,
    resolve_best_address,
)
from data_ingestion.config.settings            import get_settings
from data_ingestion.database.db                import get_session_factory, session_scope
from data_ingestion.database.models            import (
    Address,
    AddressResult,
    Agent0HouseDiscoveryResult,
    Agent1Result,
    AgentResult,
    AgentTable,
    DispatchQueue,
    IngestionJob,
    UploadedSourceRecord,
)
from data_ingestion.utils.ai_metadata import ai_metadata_dict
from data_ingestion.utils.agent_logging import configure_agent_logger, log_payload
from data_ingestion.utils.rule_classification import is_sticky_duplicate, is_uploaded_geospatial_input
from data_ingestion.utils.strings import normalize_address_key, normalize_duplicate_address_key
from sqlalchemy.orm.attributes                 import flag_modified

logger = logging.getLogger(__name__)

_RULE_ENGINE_AGENT = "rule_engine"


def _rule_status_from_merge_status(status: str) -> str:
    status_key = str(status or "").strip().lower()
    if status_key == "verified":
        return "valid"
    if status_key in {"invalid", "duplicate", "new"}:
        return status_key
    return "invalid"


def _ensure_rule_engine_table(session) -> None:
    from sqlalchemy import select as _sel

    if session.execute(_sel(AgentTable).where(AgentTable.agent_name == _RULE_ENGINE_AGENT)).scalar_one_or_none():
        return
    session.add(AgentTable(
        agent_name=_RULE_ENGINE_AGENT,
        display_name="Rule Engine",
        owner="system",
        description="Final rule status emitted by the pipeline for map color coding.",
        color_rules=[
            {"field": "rule_status", "value": "valid", "color": "#006100", "label": "Valid / Verified"},
            {"field": "rule_status", "value": "duplicate", "color": "#ffffff", "label": "Duplicate Address"},
            {"field": "rule_status", "value": "invalid", "color": "#C00000", "label": "Invalid / Not Found"},
            {"field": "rule_status", "value": "new", "color": "#9C6500", "label": "New Address"},
        ],
    ))
    session.flush()


def _upsert_rule_engine_result(
    session,
    *,
    job_id,
    address_id: int,
    rule_status: str,
    merge_status: str,
    color: str,
    reason: str,
    confidence: int,
    raw_final_distance: float | None,
) -> None:
    from datetime import datetime
    from sqlalchemy.dialects.postgresql import insert as _pg_insert

    now = datetime.utcnow()
    data = {
        "rule_status": rule_status,
        "merge_status": merge_status,
        "color": color,
        "reason": reason,
        "confidence": confidence,
        "raw_final_distance_m": round(raw_final_distance, 2) if raw_final_distance is not None else None,
    }
    stmt = _pg_insert(AgentResult).values(
        agent_name=_RULE_ENGINE_AGENT,
        job_id=job_id,
        address_id=address_id,
        data=data,
        created_at=now,
        updated_at=now,
    ).on_conflict_do_update(
        constraint="uq_agent_results_agent_address",
        set_={"data": data, "updated_at": now, "job_id": job_id},
    )
    session.execute(stmt)

ProgressCallback = Callable[..., None] | None

_NOT_MAP_LAYER_ONLY_SQL = (
    "COALESCE(raw_metadata->>'map_layer_only','false') != 'true'"
    " AND COALESCE(raw_metadata->>'geometry_type','') NOT IN ('Polygon','LineString')"
)
_HOUSEHOLD_AGENT_SQL = (
    f"({_NOT_MAP_LAYER_ONLY_SQL})"
    " AND (raw_metadata->>'file_role' IS DISTINCT FROM 'geospatial'"
    " OR lower(raw_metadata->>'category') IN ('household', 'households'))"
)

# Fraction of overall progress budget each stage gets (must sum to 1.0)
_STAGE_WEIGHTS = [
    0.07,  # 0: Agent 0 (house discovery from polygons)
    0.06,  # 1: Classify & backfill
    0.16,  # 2: Agent 2 (reverse + forward geocoding)
    0.14,  # 3: Agent 1 (Smarty + Melissa)
    0.10,  # 4: Agent 3 (Parcel)
    0.15,  # 5: Agent 4 (Building enrichment before Street View / final synthesis)
    0.06,  # 6: Agent 5-0 (Offline OCR for low-confidence rows)
    0.18,  # 7: Agent 5 (Street View)
    0.04,  # 8: Agent 6 (Final synthesis)
    0.04,  # 9: Agent 7 (Neighborhood discovery)
]

_STAGE_NAMES = [
    "agent0_house_discovery",
    "classify",
    "agent2_geocoding",
    "agent1_address_validator",
    "agent3_parcel",
    "agent4_building",
    "agent5_0_offline_ocr",
    "agent5_streetview",
    "agent6_final",
    "agent7_neighborhood_discovery",
]


def _log_stage_result(stage: str, result: Any) -> None:
    logger.info("Pipeline STAGE END: %s", stage)
    log_payload(logger, f"Pipeline {stage} result", result)


def _stage_progress(
    overall_cb: ProgressCallback,
    stage_idx: int,
    done: int,
    total: int,
) -> None:
    """Convert per-stage progress to an overall 0-100 percentage."""
    if overall_cb is None:
        return
    offset = int(sum(_STAGE_WEIGHTS[:stage_idx]) * 100)
    budget = int(_STAGE_WEIGHTS[stage_idx] * 100)
    stage_pct = int((done / total) * budget) if total else budget
    overall_cb(min(offset + stage_pct, 99), 100, _STAGE_NAMES[stage_idx], done, total or 1)


def _classify_addresses(job_id: str) -> tuple[list[int], list[int], list[int]]:
    """
    Classify every address in the job into three buckets:
      coord_only_ids  â€” lat/lon and (no address or KMZ placemark label) â†’ Reverse Geocoder
      address_ids     â€” real raw_address (not KMZ label)                â†’ Agent 1
      all_ids         â€” every address                    â†’ Agents 2â€“6

    Also backfills canonical lat/lon/address from raw_metadata when present.
    """
    import re as _re
    from sqlalchemy import select as _sel
    from sqlalchemy.orm.attributes import flag_modified

    _LAT_PAT = _re.compile(r"\blat(?:itude)?\b", _re.IGNORECASE)
    _LON_PAT = _re.compile(r"\b(?:lon|lng|long)(?:itude)?\b", _re.IGNORECASE)
    _ADDR_PAT = _re.compile(
        r"\b(?:address|addr|street|location|premise|site|service\s*point)\b",
        _re.IGNORECASE,
    )

    def _scan_meta_coords(meta: dict):
        lat = lon = None
        for k, v in meta.items():
            if v is None:
                continue
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            if fv == 0:
                continue
            if _LAT_PAT.search(str(k)) and lat is None and -90 <= fv <= 90:
                lat = fv
            elif _LON_PAT.search(str(k)) and lon is None and -180 <= fv <= 180:
                lon = fv
        return lat, lon

    def _scan_meta_address(meta: dict) -> str | None:
        for k, v in meta.items():
            if v and isinstance(v, str) and v.strip() and _ADDR_PAT.search(str(k)):
                return v.strip()
        return None

    session = get_session_factory()()
    coord_only: list[int] = []
    addr_only:  list[int] = []
    all_ids:    list[int] = []

    from sqlalchemy import text as _txt_hh
    # Only load household records from the DB â€” tabular (CSV) rows are always
    # included; geospatial (KMZ) rows only when their category is 'household'.
    _HH_FILTER = _txt_hh(_HOUSEHOLD_AGENT_SQL)

    try:
        rows = session.scalars(
            _sel(Address)
            .where(Address.job_id == job_id, _HH_FILTER)
            .order_by(Address.id)
        ).all()

        for row in rows:
            meta = row.raw_metadata or {}
            all_ids.append(row.id)

            lat = row.latitude if (row.latitude and row.latitude != 0) else None
            lon = row.longitude if (row.longitude and row.longitude != 0) else None
            if not (lat and lon):
                lat_fb, lon_fb = _scan_meta_coords(meta)
                if lat_fb and lon_fb:
                    lat, lon = lat_fb, lon_fb
                    row.latitude  = lat
                    row.longitude = lon

            if ENABLE_RES_COM_ADDRESSING:
                address_text = resolve_best_address(
                    raw_address=row.raw_address,
                    meta=meta,
                    city=row.city,
                    state=row.state,
                    zip_code=row.zip_code,
                    validated_address=row.validated_street_line or row.validated_raw_address,
                ) or None
            else:
                address_text = row.raw_address if (row.raw_address and row.raw_address.strip()) else None
            if not address_text:
                addr_fb = _scan_meta_address(meta)
                if addr_fb:
                    address_text = addr_fb
                    row.raw_address = address_text

            # Write routing debug
            # Non-exclusive: coords â†’ reverse geocoder; real address â†’ Agent 1.
            # A row with both runs through both stages (upsert in agent1_results is safe).
            updated_meta = dict(meta)
            has_coords = bool(lat and lon)
            has_real_address = bool(resolve_upload_address_line(address_text, meta))

            if has_coords:
                coord_only.append(row.id)
            if has_real_address:
                addr_only.append(row.id)

            if has_coords and has_real_address:
                route = "agent2_geocoding+address_validator"
            elif has_coords:
                route = "agent2_geocoding"
            elif has_real_address:
                route = "address_validator"
            else:
                route = "skipped"

            updated_meta["_routing"] = {
                "routed_to": route,
                "resolved_lat": lat,
                "resolved_lon": lon,
                "resolved_address": address_text,
            }
            row.raw_metadata = updated_meta
            flag_modified(row, "raw_metadata")

        session.commit()
        logger.info(
            "Pipeline classify: coord_only=%d addr=%d total=%d for job %s",
            len(coord_only), len(addr_only), len(all_ids), job_id,
        )
    finally:
        session.close()

    return coord_only, addr_only, all_ids


def _ids_where_agent5_executed(job_id: str, candidate_ids: list[int]) -> list[int]:
    """Return only the subset of candidate_ids where Agent 5 actually ran (status != 'skipped')."""
    if not candidate_ids:
        return []
    from sqlalchemy import select as _sel
    session = get_session_factory()()
    try:
        a5_results = {
            r.address_id: (r.data or {})
            for r in session.scalars(
                _sel(AgentResult).where(
                    AgentResult.agent_name == "agent5_streetview",
                    AgentResult.address_id.in_(candidate_ids),
                )
            ).all()
        }
        return [
            aid for aid in candidate_ids
            if a5_results.get(aid, {}).get("status") != "skipped"
        ]
    except Exception as exc:
        logger.warning("Unable to read Agent 5 execution rows for job %s: %s", job_id, exc)
        return list(candidate_ids)
    finally:
        session.close()


def _coerce_percent_score(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(score):
        return None
    if 0 < score <= 1:
        score *= 100
    return max(0, min(100, int(round(score))))


def _agent_confidence_from_payload(data: dict[str, Any] | None) -> int | None:
    payload = data or {}
    for key in ("confidence", "confidence_score", "score"):
        score = _coerce_percent_score(payload.get(key))
        if score is not None:
            return score
    return None


def _ids_with_agent123_confidence_below(
    job_id: str,
    candidate_ids: list[int],
    threshold: int = 90,
) -> tuple[list[int], dict[str, Any]]:
    """Return rows where any available Agent 1/2/3 confidence is below threshold."""
    if not candidate_ids:
        return [], {
            "total": 0,
            "eligible": 0,
            "ignored_high_confidence": 0,
            "no_prior_confidence": 0,
            "threshold": threshold,
        }

    try:
        job_uuid = UUID(str(job_id))
    except (TypeError, ValueError):
        logger.debug("Agent 5-0 confidence gate using all ids for non-UUID test job %r", job_id)
        return list(candidate_ids), {
            "total": len(candidate_ids),
            "eligible": len(candidate_ids),
            "ignored_high_confidence": 0,
            "no_prior_confidence": 0,
            "threshold": threshold,
            "reason": "non-uuid test job",
        }

    from sqlalchemy import select as _sel

    session = get_session_factory()()
    by_id: dict[int, dict[str, int]] = {aid: {} for aid in candidate_ids}
    candidate_set = set(candidate_ids)
    try:
        for row in session.scalars(
            _sel(Agent1Result).where(
                Agent1Result.job_id == job_uuid,
                Agent1Result.address_id.in_(candidate_ids),
            )
        ).all():
            score = _coerce_percent_score(row.confidence_score)
            if score is not None and row.address_id in candidate_set:
                by_id[row.address_id]["agent1_address_validator"] = score

        for row in session.scalars(
            _sel(AgentResult).where(
                AgentResult.job_id == job_uuid,
                AgentResult.address_id.in_(candidate_ids),
                AgentResult.agent_name.in_(
                    ("agent2_geocoding", "agent1_address_validator", "agent3_parcel")
                ),
            )
        ).all():
            score = _agent_confidence_from_payload(row.data)
            if score is not None and row.address_id in candidate_set:
                by_id[row.address_id][row.agent_name] = score

        eligible: list[int] = []
        ignored_high = 0
        no_prior = 0
        for aid in candidate_ids:
            scores = by_id.get(aid, {})
            if not scores:
                no_prior += 1
                continue
            if any(score < threshold for score in scores.values()):
                eligible.append(aid)
            else:
                ignored_high += 1

        return eligible, {
            "total": len(candidate_ids),
            "eligible": len(eligible),
            "ignored_high_confidence": ignored_high,
            "no_prior_confidence": no_prior,
            "threshold": threshold,
            "agents_checked": ["agent2_geocoding", "agent1_address_validator", "agent3_parcel"],
        }
    except Exception as exc:
        logger.warning("Unable to apply Agent 5-0 confidence gate for job %s: %s", job_id, exc)
        return [], {
            "total": len(candidate_ids),
            "eligible": 0,
            "ignored_high_confidence": 0,
            "no_prior_confidence": len(candidate_ids),
            "threshold": threshold,
            "error": str(exc),
        }
    finally:
        session.close()


def _agent1_match_status(addr: Address) -> str:
    """Return the coordinate/address match status used by the A1 handoff gate."""
    if addr.coord_address_match_status not in (None, ""):
        return str(addr.coord_address_match_status).strip().upper()
    meta = addr.raw_metadata if isinstance(addr.raw_metadata, dict) else {}
    validation = (
        meta.get("address_validation")
        if isinstance(meta.get("address_validation"), dict)
        else {}
    )
    return str(validation.get("match_status") or "").strip().upper()


def _ids_with_real_address(job_id: str, candidate_ids: list[int]) -> list[int]:
    """Return usable-address IDs whose A1 match status is not already MATCH."""
    if not candidate_ids:
        return []

    from sqlalchemy import select as _sel, text as _txt_real

    session = get_session_factory()()
    try:
        rows = session.scalars(
            _sel(Address)
            .where(Address.job_id == job_id, Address.id.in_(candidate_ids), _txt_real(_NOT_MAP_LAYER_ONLY_SQL))
            .order_by(Address.id)
        ).all()
        eligible: list[int] = []
        candidate_order = {address_id: idx for idx, address_id in enumerate(candidate_ids)}
        for row in rows:
            # A coordinate/address MATCH is already resolved. Do not spend a
            # Smarty/Melissa request re-validating the same address.
            if _agent1_match_status(row) == "MATCH":
                continue
            meta = row.raw_metadata or {}
            final = meta.get("final_resolution") if isinstance(meta.get("final_resolution"), dict) else {}
            address_text = (
                final.get("address")
                or meta.get("final_address")
                or row.validated_raw_address
                or row.raw_address
                or meta.get("ADDRESS")
            )
            if address_text and not is_kmz_placemark_label(address_text, meta):
                eligible.append(row.id)
        return sorted(eligible, key=lambda address_id: candidate_order.get(address_id, 0))
    finally:
        session.close()


def _get_agent_bus():
    """Return a connected AgentBus, or None if RabbitMQ is unavailable.

    Never raises â€” the pipeline always continues regardless of bus status.
    """
    try:
        from data_ingestion.messaging.agent_bus import AgentBus
        bus = AgentBus()
        bus.connect()
        return bus if bus.connected else None
    except Exception as exc:
        logger.debug("A2A bus unavailable (%s) â€” pipeline runs without messaging", exc)
        return None


def _overall_pct(stage_idx: int, stage_done: int, stage_total: int) -> int:
    """Compute overall pipeline percentage from stage index and stage progress."""
    offset = int(sum(_STAGE_WEIGHTS[:stage_idx]) * 100)
    budget = int(_STAGE_WEIGHTS[stage_idx] * 100)
    stage_pct = int((stage_done / max(stage_total, 1)) * budget)
    return min(offset + stage_pct, 99)


def _score(value: Any) -> int:
    try:
        return int(round(float(value or 0)))
    except (TypeError, ValueError):
        return 0


def _valid_coord(lat: Any, lon: Any) -> tuple[float | None, float | None]:
    try:
        flat, flon = float(lat), float(lon)
    except (TypeError, ValueError):
        return None, None
    if flat == 0 or flon == 0 or not (-90 <= flat <= 90) or not (-180 <= flon <= 180):
        return None, None
    return flat, flon


def _final_meta(
    meta: dict[str, Any],
    *,
    source_agent: str,
    confidence: int,
    threshold: int,
    address: str | None,
    latitude: Any,
    longitude: Any,
    provider: str | None = None,
    status: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    updated = dict(meta or {})
    routing_history = list(updated.get("routing_history") or [])
    final_lat, final_lon = _valid_coord(latitude, longitude)
    final = {
        "source_agent": source_agent,
        "provider": provider or "",
        "confidence": confidence,
        "threshold": threshold,
        "address": address or "",
        "latitude": final_lat,
        "longitude": final_lon,
        "status": status or "",
        "reason": reason or "",
    }
    updated["final_resolution"] = final
    updated["final_address"] = final["address"]
    updated["final_latitude"] = final_lat
    updated["final_longitude"] = final_lon
    updated["final_confidence"] = confidence
    updated["final_source_agent"] = source_agent
    updated["final_provider"] = provider or ""
    routing_history.append({**final, "action": "accepted"})
    updated["routing_history"] = routing_history
    return updated


def _continue_meta(
    meta: dict[str, Any],
    *,
    source_agent: str,
    confidence: int,
    threshold: int,
    reason: str | None = None,
) -> dict[str, Any]:
    updated = dict(meta or {})
    routing_history = list(updated.get("routing_history") or [])
    routing_history.append({
        "source_agent": source_agent,
        "confidence": confidence,
        "threshold": threshold,
        "action": "continue",
        "reason": reason or f"confidence {confidence} below threshold {threshold}",
    })
    updated["routing_history"] = routing_history
    return updated


_MATCH_STATUSES = {"MATCH", "AUTO_ACCEPT", "COORDS_VALIDATED", "ANALYZED", "ACCEPT"}
_MISMATCH_STATUSES = {
    "MISMATCH",
    "ADDRESS_MISMATCH",
    "MISMATCH_WARN",
    "REJECT",
    "FAILED",
    "ERROR",
    "NO_COORDS",
    "NO_COORDINATES",
}


def _status_key(status: Any) -> str:
    return str(status or "").strip().upper().replace(" ", "_")


def _status_is_match(status: Any) -> bool:
    return _status_key(status) in _MATCH_STATUSES


def _status_is_mismatch(status: Any) -> bool:
    key = _status_key(status)
    return key in _MISMATCH_STATUSES or "MISMATCH" in key


def _status_required_for_gate(agent_name: str) -> bool:
    return agent_name in {
        "agent0_reverse_geocoder",
        "agent1_address_validator",
        "agent5_streetview",
    }


def _score_meets_threshold(confidence: Any, threshold: Any, default_threshold: int = 70) -> bool:
    try:
        score = int(float(confidence or 0))
    except (TypeError, ValueError):
        score = 0
    try:
        gate = int(float(threshold if threshold is not None else default_threshold))
    except (TypeError, ValueError):
        gate = default_threshold
    return score >= gate


def _apply_resolution_gate(job_id: str, candidate_ids: list[int], agent_name: str, threshold: int) -> tuple[list[int], dict[str, int]]:
    """Persist final resolution for rows at/above threshold, return below-threshold IDs."""
    if not candidate_ids:
        return [], {"total": 0, "accepted": 0, "continued": 0}

    try:
        job_uuid = UUID(str(job_id))
    except (TypeError, ValueError):
        logger.debug("Resolution gate %s continuing non-UUID test job id %r", agent_name, job_id)
        return list(candidate_ids), {"total": len(candidate_ids), "accepted": 0, "continued": len(candidate_ids)}

    from sqlalchemy import select as _sel, text as _txt_gate
    from sqlalchemy.orm.attributes import flag_modified

    session = get_session_factory()()
    accepted = 0
    continued: list[int] = []
    try:
        addresses = {
            a.id: a for a in session.scalars(
                _sel(Address).where(Address.job_id == job_uuid, Address.id.in_(candidate_ids), _txt_gate(_NOT_MAP_LAYER_ONLY_SQL))
            ).all()
        }
        if not addresses:
            return list(candidate_ids), {"total": len(candidate_ids), "accepted": 0, "continued": len(candidate_ids)}

        a1_map: dict[int, Agent1Result] = {}
        result_map: dict[int, dict[str, Any]] = {}
        if agent_name in {"agent0_reverse_geocoder", "agent1_address_validator"}:
            a1_map = {
                r.address_id: r for r in session.scalars(
                    _sel(Agent1Result).where(Agent1Result.address_id.in_(candidate_ids))
                ).all()
            }
        else:
            db_agent = {
                "agent2_geocoding": "agent2_geocoding",
                "agent3_parcel": "agent3_parcel",
                "agent5_streetview": "agent5_streetview",
                "agent6_final": "agent6_final",
            }[agent_name]
            result_map = {
                r.address_id: (r.data or {}) for r in session.scalars(
                    _sel(AgentResult).where(
                        AgentResult.agent_name == db_agent,
                        AgentResult.address_id.in_(candidate_ids),
                    )
                ).all()
            }

        for address_id in candidate_ids:
            addr = addresses.get(address_id)
            if not addr:
                continued.append(address_id)
                continue
            meta = addr.raw_metadata or {}

            if agent_name == "agent0_reverse_geocoder":
                score = _score(addr.reverse_geocode_confidence_score)
                a1 = a1_map.get(address_id)
                address = (
                    (a1.chosen_standardized_address if a1 else None)
                    or addr.validated_raw_address
                    or meta.get("ADDRESS")
                    or addr.raw_address
                )
                lat = addr.validated_latitude if addr.validated_latitude is not None else addr.latitude
                lon = addr.validated_longitude if addr.validated_longitude is not None else addr.longitude
                status = addr.coord_address_match_status
                reverse_block = meta.get("reverse_geocoding") if isinstance(meta.get("reverse_geocoding"), dict) else {}
                provider = reverse_block.get("source") or (a1.chosen_provider if a1 else None) or "reverse_geocoder"
            elif agent_name == "agent1_address_validator":
                a1 = a1_map.get(address_id)
                score = _score(a1.confidence_score if a1 else 0)
                address = (a1.chosen_standardized_address if a1 else None) or addr.raw_address
                lat = (a1.smarty_lat if a1 else None) or addr.validated_latitude or addr.latitude
                lon = (a1.smarty_lon if a1 else None) or addr.validated_longitude or addr.longitude
                status = a1.validation_status if a1 else None
                provider = a1.chosen_provider if a1 else None
            elif agent_name == "agent2_geocoding":
                data = result_map.get(address_id, {})
                status = data.get("status")
                score = _score(data.get("confidence"))
                if str(status or "").lower() in {"failed", "error", "skipped"}:
                    score = 0
                address = data.get("formatted_address") or addr.raw_address
                lat = data.get("latitude") or addr.latitude
                lon = data.get("longitude") or addr.longitude
                provider = data.get("source") or "geocoding"
            elif agent_name == "agent3_parcel":
                data = result_map.get(address_id, {})
                score = _score(data.get("confidence"))
                final = meta.get("final_resolution") or {}
                address = final.get("address") or addr.validated_raw_address or addr.raw_address
                lat = final.get("latitude") or addr.validated_latitude or addr.latitude
                lon = final.get("longitude") or addr.validated_longitude or addr.longitude
                status = data.get("status")
                provider = data.get("source") or "parcel"
            elif agent_name == "agent5_streetview":
                data = result_map.get(address_id, {})
                score = _score(data.get("house_number_conf", data.get("confidence")))
                final = meta.get("final_resolution") or {}
                address = final.get("address") or addr.raw_address
                lat = final.get("latitude") or addr.latitude
                lon = final.get("longitude") or addr.longitude
                status = data.get("status")
                provider = data.get("imagery_source") or data.get("winning_step") or "streetview"
            else:
                data = result_map.get(address_id, {})
                score = _score(data.get("final_confidence"))
                final = meta.get("final_resolution") or {}
                address = final.get("address") or addr.raw_address
                lat = final.get("latitude") or addr.latitude
                lon = final.get("longitude") or addr.longitude
                status = data.get("status")
                provider = data.get("imagery_source") or "agent6_final"

            status_blocks_acceptance = (
                _status_required_for_gate(agent_name)
                and status not in (None, "")
                and not _status_is_match(status)
            )

            if score >= threshold and not status_blocks_acceptance:
                addr.raw_metadata = _final_meta(
                    meta,
                    source_agent=agent_name,
                    confidence=score,
                    threshold=threshold,
                    address=address,
                    latitude=lat,
                    longitude=lon,
                    provider=provider,
                    status=status,
                )
                accepted += 1
            else:
                gate_reason = (
                    f"{agent_name} status {status} is not MATCH"
                    if status_blocks_acceptance
                    else f"{agent_name} score below threshold"
                )
                addr.raw_metadata = _continue_meta(
                    meta,
                    source_agent=agent_name,
                    confidence=score,
                    threshold=threshold,
                    reason=gate_reason,
                )
                continued.append(address_id)
            flag_modified(addr, "raw_metadata")

        session.commit()
        logger.info(
            "Resolution gate %s threshold=%d accepted=%d continued=%d",
            agent_name, threshold, accepted, len(continued),
        )
        return continued, {"total": len(candidate_ids), "accepted": accepted, "continued": len(continued)}
    finally:
        session.close()


def _delete_unconfirmed_agent0_discoveries(
    job_id: str,
    candidate_ids: list[int],
    *,
    reason: str = "Agent 0 discovery did not reach 90 confidence in Agent 2/Smarty or Agent 3",
) -> dict[str, Any]:
    """Delete Agent 0-created address rows that were not confirmed by downstream gates."""
    ids = [int(x) for x in (candidate_ids or []) if x is not None]
    if not ids:
        return {"checked": 0, "deleted": 0, "deleted_ids": []}

    try:
        job_uuid = UUID(str(job_id))
    except (TypeError, ValueError):
        return {"checked": len(ids), "deleted": 0, "deleted_ids": [], "reason": "non-UUID job id"}

    session = get_session_factory()()
    try:
        rows = session.query(Address).filter(Address.job_id == job_uuid, Address.id.in_(ids)).all()
        delete_ids: list[int] = []
        for row in rows:
            meta = row.raw_metadata if isinstance(row.raw_metadata, dict) else {}
            if not (meta.get("agent0_discovered") is True and meta.get("agent0_source") == "house_discovery"):
                continue
            final = meta.get("final_resolution") if isinstance(meta.get("final_resolution"), dict) else {}
            source_agent = str(final.get("source_agent") or meta.get("final_source_agent") or "")
            try:
                confidence = int(float(final.get("confidence") if final.get("confidence") is not None else meta.get("final_confidence") or 0))
            except (TypeError, ValueError):
                confidence = 0
            confirmed = (
                source_agent in {"agent2_geocoding", "agent1_address_validator", "agent3_parcel"}
                and confidence >= 90
            )
            if not confirmed:
                delete_ids.append(row.id)

        if not delete_ids:
            return {"checked": len(ids), "deleted": 0, "deleted_ids": []}

        discoveries = session.query(Agent0HouseDiscoveryResult).filter(
            Agent0HouseDiscoveryResult.job_id == job_uuid,
            Agent0HouseDiscoveryResult.address_id.in_(delete_ids),
        ).all()
        for discovery in discoveries:
            raw = dict(discovery.raw_data or {})
            raw["cleanup_reason"] = reason
            raw["deleted_address_id"] = discovery.address_id
            discovery.status = "rejected"
            discovery.dedupe_reason = reason
            discovery.address_id = None
            discovery.raw_data = raw
            flag_modified(discovery, "raw_data")
        session.flush()

        session.query(UploadedSourceRecord).filter(UploadedSourceRecord.address_id.in_(delete_ids)).update(
            {"address_id": None, "merge_status": "invalid", "merge_color": "red"},
            synchronize_session=False,
        )
        session.query(DispatchQueue).filter(DispatchQueue.address_id.in_(delete_ids)).delete(synchronize_session=False)
        session.query(AddressResult).filter(AddressResult.address_id.in_(delete_ids)).delete(synchronize_session=False)
        session.query(Agent1Result).filter(Agent1Result.address_id.in_(delete_ids)).delete(synchronize_session=False)
        session.query(AgentResult).filter(AgentResult.address_id.in_(delete_ids)).delete(synchronize_session=False)
        session.query(Address).filter(Address.id.in_(delete_ids)).delete(synchronize_session=False)

        job = session.get(IngestionJob, job_uuid)
        if job:
            job.row_count = int(session.query(Address).filter(Address.job_id == job_uuid).count())

        session.commit()
        logger.info("Agent0 cleanup deleted %d unconfirmed discovered row(s)", len(delete_ids))
        return {"checked": len(ids), "deleted": len(delete_ids), "deleted_ids": delete_ids, "reason": reason}
    except Exception:
        session.rollback()
        logger.exception("Agent0 cleanup failed for job %s", job_id)
        raise
    finally:
        session.close()


def _delete_agent0_discovery_rows(
    job_id: str,
    delete_ids: list[int],
    *,
    reason: str,
) -> dict[str, Any]:
    ids = [int(x) for x in (delete_ids or []) if x is not None]
    if not ids:
        return {"checked": 0, "deleted": 0, "deleted_ids": []}

    try:
        job_uuid = UUID(str(job_id))
    except (TypeError, ValueError):
        return {"checked": len(ids), "deleted": 0, "deleted_ids": [], "reason": "non-UUID job id"}

    session = get_session_factory()()
    try:
        rows = session.query(Address).filter(Address.job_id == job_uuid, Address.id.in_(ids)).all()
        delete_ids = []
        for row in rows:
            meta = row.raw_metadata if isinstance(row.raw_metadata, dict) else {}
            if meta.get("agent0_discovered") is True and meta.get("agent0_source") == "house_discovery":
                delete_ids.append(row.id)

        if not delete_ids:
            return {"checked": len(ids), "deleted": 0, "deleted_ids": []}

        discoveries = session.query(Agent0HouseDiscoveryResult).filter(
            Agent0HouseDiscoveryResult.job_id == job_uuid,
            Agent0HouseDiscoveryResult.address_id.in_(delete_ids),
        ).all()
        for discovery in discoveries:
            raw = dict(discovery.raw_data or {})
            raw["cleanup_reason"] = reason
            raw["deleted_address_id"] = discovery.address_id
            discovery.status = "rejected"
            discovery.dedupe_reason = reason
            discovery.address_id = None
            discovery.raw_data = raw
            flag_modified(discovery, "raw_data")
        session.flush()

        session.query(UploadedSourceRecord).filter(UploadedSourceRecord.address_id.in_(delete_ids)).update(
            {"address_id": None, "merge_status": "invalid", "merge_color": "red"},
            synchronize_session=False,
        )
        session.query(DispatchQueue).filter(DispatchQueue.address_id.in_(delete_ids)).delete(synchronize_session=False)
        session.query(AddressResult).filter(AddressResult.address_id.in_(delete_ids)).delete(synchronize_session=False)
        session.query(Agent1Result).filter(Agent1Result.address_id.in_(delete_ids)).delete(synchronize_session=False)
        session.query(AgentResult).filter(AgentResult.address_id.in_(delete_ids)).delete(synchronize_session=False)
        session.query(Address).filter(Address.id.in_(delete_ids)).delete(synchronize_session=False)

        job = session.get(IngestionJob, job_uuid)
        if job:
            job.row_count = int(session.query(Address).filter(Address.job_id == job_uuid).count())

        session.commit()
        logger.info("Agent0 validation deleted %d discovered row(s): %s", len(delete_ids), reason)
        return {"checked": len(ids), "deleted": len(delete_ids), "deleted_ids": delete_ids, "reason": reason}
    except Exception:
        session.rollback()
        logger.exception("Agent0 validation cleanup failed for job %s", job_id)
        raise
    finally:
        session.close()


def _agent0_discovered_ids(job_id: str, candidate_ids: list[int] | None = None) -> list[int]:
    try:
        job_uuid = UUID(str(job_id))
    except (TypeError, ValueError):
        return []
    session = get_session_factory()()
    try:
        from sqlalchemy import text as _txt_agent0

        query = session.query(Address.id).filter(
            Address.job_id == job_uuid,
            _txt_agent0("raw_metadata->>'agent0_discovered' = 'true'"),
            _txt_agent0("raw_metadata->>'agent0_source' = 'house_discovery'"),
        )
        ids = [int(x) for x in (candidate_ids or []) if x is not None]
        if ids:
            query = query.filter(Address.id.in_(ids))
        return [int(row[0]) for row in query.all()]
    finally:
        session.close()


def _scores_for_agent0_validation(job_id: str, address_ids: list[int]) -> dict[int, dict[str, float]]:
    if not address_ids:
        return {}
    try:
        job_uuid = UUID(str(job_id))
    except (TypeError, ValueError):
        return {}
    session = get_session_factory()()
    try:
        scores: dict[int, dict[str, float]] = {int(address_id): {} for address_id in address_ids}
        for row in session.query(Agent1Result).filter(
            Agent1Result.job_id == job_uuid,
            Agent1Result.address_id.in_(address_ids),
        ).all():
            try:
                scores.setdefault(int(row.address_id), {})["smarty"] = float(row.confidence_score or 0)
            except (TypeError, ValueError):
                scores.setdefault(int(row.address_id), {})["smarty"] = 0.0
        for row in session.query(AgentResult).filter(
            AgentResult.job_id == job_uuid,
            AgentResult.address_id.in_(address_ids),
            AgentResult.agent_name == "agent3_parcel",
        ).all():
            data = row.data or {}
            try:
                scores.setdefault(int(row.address_id), {})["regrid"] = float(data.get("confidence") or 0)
            except (TypeError, ValueError):
                scores.setdefault(int(row.address_id), {})["regrid"] = 0.0
        return scores
    finally:
        session.close()


def _validate_agent0_discoveries(
    job_id: str,
    inserted_ids: list[int],
    *,
    agent0_options: dict[str, Any],
) -> dict[str, Any]:
    ids = [int(x) for x in (inserted_ids or []) if x is not None]
    if not ids:
        return {"skipped": True, "reason": "no Agent 0 inserted rows", "checked": 0, "deleted": 0}
    if not agent0_options.get("validate_discovered", False):
        return {"skipped": True, "reason": "Agent 0 validation disabled", "checked": len(ids), "deleted": 0}

    use_smarty = bool(agent0_options.get("validate_with_smarty", True))
    use_regrid = bool(agent0_options.get("validate_with_regrid", False))
    if not use_smarty and not use_regrid:
        return {"skipped": True, "reason": "no Agent 0 validation providers enabled", "checked": len(ids), "deleted": 0}

    threshold = int(agent0_options.get("validation_threshold") or 90)
    validators: dict[str, Any] = {}
    if use_smarty:
        validators["smarty"] = run_agent1_for_job(
            job_id,
            address_ids=ids,
            agent_options={
                "enabled": True,
                "smarty": True,
                "melissa": False,
                "use_cache": bool(agent0_options.get("use_validation_cache", False)),
            },
        )
    if use_regrid:
        validators["regrid"] = run_agent3_for_job(
            job_id,
            address_ids=ids,
            agent_options={
                "enabled": True,
                "regrid": True,
                "tigerline": False,
                "nominatim": False,
            },
        )

    scores = _scores_for_agent0_validation(job_id, ids)
    rejected = []
    accepted = []
    for address_id in ids:
        provider_scores = scores.get(address_id, {})
        best = max(provider_scores.values(), default=0.0)
        if best >= threshold:
            accepted.append(address_id)
        else:
            rejected.append(address_id)

    cleanup = _delete_agent0_discovery_rows(
        job_id,
        rejected,
        reason=f"Agent 0 validation confidence below {threshold} from selected validators",
    )
    return {
        "checked": len(ids),
        "accepted": len(accepted),
        "deleted": cleanup.get("deleted", 0),
        "deleted_ids": cleanup.get("deleted_ids", []),
        "threshold": threshold,
        "validators": list(validators.keys()),
        "validator_results": validators,
        "scores": scores,
    }


def _distance_m(lat1: float | None, lon1: float | None, lat2: float | None, lon2: float | None) -> float | None:
    if lat1 is None or lon1 is None or lat2 is None or lon2 is None:
        return None
    try:
        lat1f, lon1f, lat2f, lon2f = map(float, [lat1, lon1, lat2, lon2])
    except (TypeError, ValueError):
        return None
    rlat1, rlon1, rlat2, rlon2 = map(math.radians, [lat1f, lon1f, lat2f, lon2f])
    dlat = rlat2 - rlat1
    dlon = rlon2 - rlon1
    h = math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    return 6371000 * 2 * math.asin(math.sqrt(h))


def _write_new_bundle_from_agent_results(job_id: str) -> None:
    """Write final resolved address data into raw_metadata['new'] for every address in the job.

    Called at the end of the pipeline so raw_metadata carries a 'new' snapshot that mirrors
    the 'old' snapshot created at ingestion time, enabling before/after comparison.
    """
    from sqlalchemy import select as _sel, text as _txt
    _HH = _txt(
        _HOUSEHOLD_AGENT_SQL
    )
    session = get_session_factory()()
    try:
        try:
            job_uuid = UUID(str(job_id))
        except (TypeError, ValueError):
            logger.debug("Skipping new bundle for non-UUID job id %r", job_id)
            return
        addresses = {
            a.id: a for a in session.scalars(
                _sel(Address).where(Address.job_id == job_uuid, _HH)
            ).all()
        }
        if not addresses:
            return

        addr_ids = list(addresses.keys())

        a1_map: dict[int, Agent1Result] = {
            r.address_id: r for r in session.scalars(
                _sel(Agent1Result).where(Agent1Result.address_id.in_(addr_ids))
            ).all()
        }

        ar_map: dict[str, dict[int, dict]] = {}
        for ar in session.scalars(
            _sel(AgentResult).where(
                AgentResult.agent_name.in_([
                    "agent1_address_validator",
                    "agent2_geocoding",
                    "agent3_parcel",
                    "agent4_building",
                    "agent5_streetview",
                    "agent6_final",
                ]),
                AgentResult.address_id.in_(addr_ids),
            )
        ).all():
            ar_map.setdefault(ar.agent_name, {})[ar.address_id] = ar.data or {}

        for addr_id, addr in addresses.items():
            meta = dict(addr.raw_metadata or {})
            final = meta.get("final_resolution") or {}

            final_lat = final.get("latitude") if final.get("latitude") is not None else (
                addr.validated_latitude if addr.validated_latitude is not None else addr.latitude
            )
            final_lon = final.get("longitude") if final.get("longitude") is not None else (
                addr.validated_longitude if addr.validated_longitude is not None else addr.longitude
            )
            a1 = a1_map.get(addr_id)
            final_street = (
                final.get("address")
                or (a1.chosen_standardized_address if a1 else None)
                or addr.validated_street_line
                or addr.validated_raw_address
                or addr.raw_address
                or ""
            )
            final_zip = addr.validated_postcode or addr.zip_code or ""
            city_state = addr.validated_city_state or ""
            if not city_state and addr.city and addr.state:
                city_state = f"{addr.city}, {addr.state}"
            city_only = city_state.split(",")[0].strip() if "," in city_state else city_state.strip()

            a6_data = ar_map.get("agent6_final", {}).get(addr_id, {})
            a4_data = ar_map.get("agent4_building", {}).get(addr_id, {})
            a1_ar = ar_map.get("agent1_address_validator", {}).get(addr_id, {})

            ai_type = (
                a6_data.get("final_structure_type")
                or a4_data.get("structure_type")
                or ""
            )
            ai_remarks = (
                a6_data.get("validation_summary")
                or a1_ar.get("comparison_reason")
                or final.get("reason")
                or ""
            )

            # zip+4 from Agent 1 (Smarty preferred, Melissa fallback)
            zip_plus4 = (a1.smarty_zip_plus_4 if a1 else None) or (a1.melissa_zip_plus_4 if a1 else None) or ""

            meta["new"] = {
                "street_number_name": final_street,
                "zip_postal_code": final_zip,
                "zip_plus4": zip_plus4,
                "latitude": final_lat,
                "longitude": final_lon,
                "city_state": city_state,
                "city": city_only,
                "country_code": addr.validated_country_code or "",
                "confidence": final.get("confidence") or meta.get("final_confidence"),
                "source_agent": final.get("source_agent") or meta.get("final_source_agent") or "",
                "ai_type": ai_type,
                "ai_remarks": ai_remarks,
            }
            _ai_zip_code = (
                f"{final_zip[:5]}-{zip_plus4}" if zip_plus4 and final_zip
                else zip_plus4 or final_zip
            )
            meta["ai"] = ai_metadata_dict(
                address=final_street,
                street=final_street,
                latitude=final_lat,
                longitude=final_lon,
                city=city_only,
                country=addr.validated_country_code or "",
                zip=final_zip[:5] if final_zip else "",
                zip_code=_ai_zip_code,
                confidence=meta["new"].get("confidence"),
                remarks=ai_remarks,
                ai_type=ai_type,
                agent_name=meta["new"].get("source_agent") or "",
            )
            addr.raw_metadata = meta
            flag_modified(addr, "raw_metadata")

        session.commit()
        logger.info("Wrote new bundle for %d addresses in job %s", len(addresses), job_id)
    except Exception:
        session.rollback()
        logger.exception("Failed to write new bundle for job %s", job_id)
    finally:
        session.close()


def _flow_rule_status(meta: dict[str, Any], default_confidence: int) -> tuple[str, str, str]:
    """Classify verified/invalid from flow match statuses and thresholds."""
    if is_sticky_duplicate(meta):
        return "duplicate", "white", "Duplicate address in uploaded raw data"
    final = meta.get("final_resolution") if isinstance(meta.get("final_resolution"), dict) else {}
    history = meta.get("routing_history") if isinstance(meta.get("routing_history"), list) else []
    first = history[0] if history and isinstance(history[0], dict) else None

    if first and _status_is_match(first.get("status")) and _score_meets_threshold(
        first.get("confidence"),
        first.get("threshold"),
    ):
        return (
            "verified",
            "green",
            f"{first.get('source_agent') or 'First agent'} returned MATCH above threshold",
        )

    final_status = final.get("status")
    final_confidence = final.get("confidence") if final.get("confidence") is not None else default_confidence
    final_threshold = final.get("threshold")
    final_agent = final.get("source_agent") or meta.get("final_source_agent") or "Final selected agent"

    if final_status not in (None, ""):
        if _status_is_mismatch(final_status):
            return (
                "invalid",
                "red",
                f"{final_agent} returned {final_status}; final flow result is invalid",
            )
        if _status_is_match(final_status) and _score_meets_threshold(final_confidence, final_threshold):
            return (
                "verified",
                "green",
                f"{final_agent} returned MATCH above threshold",
            )

    if final and _score_meets_threshold(final_confidence, final_threshold):
        return (
            "verified",
            "green",
            f"{final_agent} met confidence threshold",
        )

    return (
        "invalid",
        "red",
        "No selected flow agent returned MATCH above threshold",
    )


def _refresh_rule_classification_after_processing(job_id: str) -> dict[str, int]:
    """Refresh raw-vs-final map/export rule colors after all agents complete."""
    session = get_session_factory()()
    counts = {"verified": 0, "duplicate": 0, "invalid": 0, "new": 0}
    try:
        try:
            job_uuid = UUID(str(job_id))
        except (TypeError, ValueError):
            logger.debug("Skipping rule classification for non-UUID job id %r", job_id)
            return counts
        from sqlalchemy import text as _txt2
        _HH2 = _txt2(
            _HOUSEHOLD_AGENT_SQL
        )
        rows = (
            session.query(Address)
            .filter(Address.job_id == job_uuid, _HH2)
            .order_by(Address.id.asc())
            .all()
        )
        _ensure_rule_engine_table(session)
        row_ids = [row.id for row in rows]
        a1_map = {
            r.address_id: r for r in session.query(Agent1Result)
            .filter(Agent1Result.address_id.in_(row_ids))
            .all()
        } if row_ids else {}

        def _row_duplicate_key(row: Address, meta: dict[str, Any], final: dict[str, Any]) -> str | None:
            return normalize_duplicate_address_key(row.raw_address)

        tabular_keys = {
            key for row in rows
            if ((row.raw_metadata or {}).get("file_role") == "tabular")
            for key in [_row_duplicate_key(row, dict(row.raw_metadata or {}), (row.raw_metadata or {}).get("final_resolution") or {})]
            if key
        }
        seen_address_keys: set[str] = set()

        for row in rows:
            meta = dict(row.raw_metadata or {})
            role = meta.get("file_role")
            final = meta.get("final_resolution") or {}
            a1 = a1_map.get(row.id)
            raw_key = _row_duplicate_key(row, meta, final)
            if ENABLE_RES_COM_ADDRESSING:
                final_address = resolve_best_address(
                    raw_address=row.raw_address,
                    meta=meta,
                    city=row.city,
                    state=row.state,
                    zip_code=row.zip_code,
                    validated_address=row.validated_street_line or row.validated_raw_address,
                    chosen_address=a1.chosen_standardized_address if a1 else None,
                    final_address=final.get("address") if isinstance(final, dict) else None,
                )
            else:
                final_address = final.get("address") or meta.get("final_address") or row.raw_address
            final_lat = final.get("latitude") or row.validated_latitude or row.latitude
            final_lon = final.get("longitude") or row.validated_longitude or row.longitude
            confidence = final.get("confidence") or meta.get("final_confidence") or 0
            if confidence <= 0 and str(final.get("status") or "").lower() not in {
                "failed", "error", "skipped", "",
            }:
                confidence = row.reverse_geocode_confidence_score or 0
            av = meta.get("address_validation") if isinstance(meta.get("address_validation"), dict) else {}
            if confidence <= 0 and av.get("confidence_score") is not None:
                confidence = av.get("confidence_score")
            try:
                confidence = int(float(confidence))
            except (TypeError, ValueError):
                confidence = 0

            raw_final_distance = _distance_m(row.latitude, row.longitude, final_lat, final_lon)
            source_key = raw_key
            if is_sticky_duplicate(meta):
                status, color, reason = "duplicate", "white", "Duplicate address in uploaded raw data"
            elif raw_key and source_key in seen_address_keys:
                status, color, reason = "duplicate", "white", "Duplicate address in uploaded raw data"
            elif role == "geospatial" and raw_key and raw_key not in tabular_keys:
                if is_uploaded_geospatial_input(meta):
                    status, color, reason = "verified", "green", "Uploaded KMZ address present in source data"
                else:
                    status, color, reason = "new", "yellow", "New address identified from discovery agents but not present in uploaded data"
            elif final_address and final_lat is not None and final_lon is not None:
                status, color, reason = _flow_rule_status(meta, confidence)
            else:
                status, color, reason = "invalid", "red", "Address not found or failed final verification"
            rule_status = _rule_status_from_merge_status(status)

            if raw_key:
                seen_address_keys.add(source_key)

            meta.update({
                "rule_status": rule_status,
                "rule_color": color,
                "merge_status": status,
                "merge_color": color,
                "merge_reason": reason,
                "merge_post_process": True,
                "merge_raw_final_distance_m": round(raw_final_distance, 2) if raw_final_distance is not None else None,
                "merge_classification": {
                    "status": status,
                    "rule_status": rule_status,
                    "rule_color": color,
                    "color": color,
                    "reason": reason,
                    "raw_final_distance_m": round(raw_final_distance, 2) if raw_final_distance is not None else None,
                    "final_confidence": confidence,
                },
            })
            row.raw_metadata = meta
            flag_modified(row, "raw_metadata")

            _upsert_rule_engine_result(
                session,
                job_id=job_uuid,
                address_id=row.id,
                rule_status=rule_status,
                merge_status=status,
                color=color,
                reason=reason,
                confidence=confidence,
                raw_final_distance=raw_final_distance,
            )

            session.query(UploadedSourceRecord).filter(UploadedSourceRecord.address_id == row.id).update(
                {"raw_data": meta, "merge_status": status, "merge_color": color},
                synchronize_session=False,
            )
            counts[status] += 1

        session.commit()
        logger.info("Rule classification refreshed for job %s: %s", job_id, counts)
        return counts
    except Exception:
        session.rollback()
        logger.exception("Rule classification refresh failed for job %s", job_id)
        raise
    finally:
        session.close()


def run_full_pipeline(
    job_id: str,
    progress_callback: ProgressCallback = None,
    geocode_options: dict[str, bool] | None = None,
    pipeline_options: dict[str, dict[str, bool]] | None = None,
    flow_template_id: str | None = None,
) -> dict:
    """
    Execute the full 6-agent pipeline for every address in job_id.
    progress_callback(done, total) is called after each meaningful step.
    Returns a summary dict keyed by stage name.

    RabbitMQ A2A events are published when the broker is reachable;
    the pipeline is fully functional without it.
    
    Args:
        job_id: Job UUID or string
        progress_callback: Optional callback for progress updates
        geocode_options: Optional geocoding configuration (for Agent 2)
        pipeline_options: Optional pipeline configuration per agent
        flow_template_id: Optional flow template ID to use; if not provided, uses default or job's flow
    """
    from data_ingestion.database.models import JobPipelineFlow, PipelineFlowTemplate
    from data_ingestion.utils.flow_routing import get_agents_in_execution_order, build_flow_routing_map
    from data_ingestion.utils.pipeline_options import (
        agent_enabled,
        apply_flow_config_to_pipeline_options,
        normalize_pipeline_options,
    )
    
    log_path = configure_agent_logger(logger, "pipeline_runner")
    logger.info("Pipeline start for job %s", job_id)
    logger.info("=" * 72)
    logger.info(
        "Pipeline START: job_id=%r geocode_options=%s pipeline_options=%s flow_template_id=%r log=%s",
        job_id, geocode_options, pipeline_options, flow_template_id, log_path,
    )
    results: dict[str, dict] = {}
    
    # Fetch flow configuration for this job
    flow_config = None
    try:
        with session_scope() as session:
            # Try to get job-specific flow
            try:
                job_uuid = UUID(str(job_id))
                job_flow = session.query(JobPipelineFlow).filter(
                    JobPipelineFlow.job_id == job_uuid
                ).order_by(JobPipelineFlow.created_at.desc()).first()

                if job_flow:
                    flow_config = job_flow.flow_config
                    logger.info(f"Using flow for job {job_id}: {job_flow.template_id}")
            except (ValueError, TypeError):
                pass

            # Fallback to default template if no job-specific flow
            if not flow_config:
                default_template = session.query(PipelineFlowTemplate).filter(
                    PipelineFlowTemplate.name == "Default FTTH Pipeline"
                ).first()
                if default_template:
                    flow_config = default_template.config
                    logger.info(f"Using default flow for job {job_id}")
    except Exception as exc:
        logger.warning("Flow config lookup failed for job %s; using legacy pipeline options: %s", job_id, exc)
    
    if not flow_config:
        logger.warning(f"No flow config found for job {job_id}; using legacy pipeline options")
        flow_config = None
    
    # Store flow config in results for auditing
    if flow_config:
        results["flow_config"] = flow_config
        results["agents_in_order"] = get_agents_in_execution_order(flow_config)
        flow_routing_map = build_flow_routing_map(flow_config)
        results["flow_routing_map"] = flow_routing_map
        logger.info(f"Pipeline agents (in order): {results['agents_in_order']}")
        log_payload(logger, "Pipeline flow_config", flow_config)
    
    raw_opts = pipeline_options
    if raw_opts is None and geocode_options is not None:
        raw_opts = {"agent2": geocode_options}
    opts = normalize_pipeline_options(raw_opts)
    opts = apply_flow_config_to_pipeline_options(opts, flow_config)
    results["pipeline_options"] = opts
    log_payload(logger, "Pipeline normalized_options", opts)
    settings = get_settings()
    thresholds = {
        "agent1_address_validator": settings.agent1_confidence_threshold,
        "agent2_geocoding": max(
            settings.agent0_confidence_threshold,
            settings.agent2_confidence_threshold,
        ),
        "agent3_parcel": settings.agent3_confidence_threshold,
        "agent5_streetview": settings.agent5_confidence_threshold,
        "agent6_final": settings.agent6_confidence_threshold,
    }
    flow_routing_map = results.get("flow_routing_map") if flow_config else None
    if isinstance(flow_routing_map, dict):
        for threshold_key, flow_agent_name in (
            ("agent2_geocoding", "agent2_geocoding"),
            ("agent1_address_validator", "agent1_address_validator"),
            ("agent3_parcel", "agent3_parcel"),
            ("agent5_streetview", "agent5_streetview"),
            ("agent6_final", "agent6_finalization"),
        ):
            flow_threshold = (flow_routing_map.get(flow_agent_name) or {}).get("threshold_1")
            if flow_threshold is not None:
                try:
                    thresholds[threshold_key] = int(flow_threshold)
                except (TypeError, ValueError):
                    logger.warning("Ignoring invalid flow threshold for %s: %r", flow_agent_name, flow_threshold)

    # â”€â”€ Stage 0: Classify â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    bus = _get_agent_bus()
    a0_logger = logging.getLogger("data_ingestion.agents.agent0_house_discovery")
    a0_log_path = configure_agent_logger(a0_logger, "agent0_house_discovery")
    a0_logger.info(
        "Pipeline handoff: job_id=%r enabled=%s options=%s log=%s",
        job_id,
        agent_enabled(opts, "agent0"),
        opts.get("agent0"),
        a0_log_path,
    )

    if agent_enabled(opts, "agent0"):
        logger.info("Pipeline STAGE START: agent0_house_discovery")
        a0_logger.info("Agent0 STAGE START from pipeline_runner")
        if bus:
            bus.notify_agent_start(job_id, "agent0_house_discovery", 0)
        def _a0_cb(done, total):
            _stage_progress(progress_callback, 0, done, total)
        a0 = run_agent0_for_job(
            job_id,
            progress_callback=_a0_cb,
            agent_options=opts.get("agent0"),
        )
        results["agent0_house_discovery"] = a0
        results["agent0_internal_validation"] = _validate_agent0_discoveries(
            job_id,
            list(a0.get("inserted_address_ids") or []),
            agent0_options=opts.get("agent0") or {},
        )
        _log_stage_result("agent0_internal_validation", results["agent0_internal_validation"])
        a0_logger.info("Agent0 STAGE COMPLETE from pipeline_runner: %s", a0)
        _log_stage_result("agent0_house_discovery", a0)
        if bus:
            bus.notify_agent_complete(job_id, "agent0_house_discovery", a0)
            bus.broadcast_progress(job_id, "agent0_house_discovery", _overall_pct(0, 1, 1))
    else:
        results["agent0_house_discovery"] = {"skipped": True, "reason": "agent disabled"}
        results["agent0_internal_validation"] = {"skipped": True, "reason": "agent disabled"}
        a0_logger.info("Agent0 STAGE SKIP from pipeline_runner: %s", results["agent0_house_discovery"])
        _log_stage_result("agent0_house_discovery", results["agent0_house_discovery"])
    _stage_progress(progress_callback, 0, 1, 1)

    _stage_progress(progress_callback, 1, 0, 1)
    coord_only_ids, address_ids, all_ids = _classify_addresses(job_id)
    _stage_progress(progress_callback, 1, 1, 1)
    results["classify"] = {
        "coord_only": len(coord_only_ids),
        "address": len(address_ids),
        "total": len(all_ids),
    }
    _log_stage_result("classify", results["classify"])
    active_ids = list(all_ids)

    # â”€â”€ Connect to A2A bus (non-blocking, optional) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if bus:
        bus.notify_pipeline_start(job_id, len(all_ids))

    # â”€â”€ Stage 1: Agent 2 (unified reverse + forward geocoding) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if active_ids and agent_enabled(opts, "agent2"):
        logger.info("Pipeline STAGE START: agent2_geocoding active_ids=%d", len(active_ids))
        if bus:
            bus.notify_agent_start(job_id, "agent2_geocoding", len(active_ids))
        def _geo_cb(done, total):
            _stage_progress(progress_callback, 2, done, total)
        geo = run_geocoding_for_job(
            job_id,
            coord_only_ids=coord_only_ids,
            all_ids=all_ids,
            address_ids=address_ids,
            forward_ids=active_ids,
            progress_callback=_geo_cb,
            geocode_options=opts.get("agent2"),
        )
        results["agent2_geocoding"] = geo
        results["reverse_geocoder"] = geo.get("reverse_geocoder", geo.get("reverse", {}))
        results["coord_address_validation"] = geo.get("coord_address_validation", {})
        _log_stage_result("agent2_geocoding", geo)
        if bus:
            bus.notify_agent_complete(job_id, "agent2_geocoding", geo)
            bus.broadcast_progress(job_id, "agent2_geocoding", _overall_pct(2, 1, 1))
    else:
        results["agent2_geocoding"] = {"skipped": True, "reason": "agent disabled or no rows"}
        results["reverse_geocoder"] = {"skipped": True}
        _log_stage_result("agent2_geocoding", results["agent2_geocoding"])
    _stage_progress(progress_callback, 2, 1, 1)
    if agent_enabled(opts, "agent2"):
        active_ids, results["agent2_gate"] = _apply_resolution_gate(
            job_id,
            active_ids,
            "agent2_geocoding",
            thresholds["agent2_geocoding"],
        )
        _log_stage_result("agent2_gate", results["agent2_gate"])
    else:
        results["agent2_gate"] = {"total": len(active_ids), "accepted": 0, "continued": len(active_ids)}

    # â”€â”€ Stage 2: Agent 1 (Smarty + Melissa) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    a1_ids = _ids_with_real_address(job_id, active_ids)
    if opts.get("agent0", {}).get("exclude_discovered_from_agent1", True):
        agent0_ids_for_agent1 = set(_agent0_discovered_ids(job_id, a1_ids))
        if agent0_ids_for_agent1:
            a1_ids = [address_id for address_id in a1_ids if address_id not in agent0_ids_for_agent1]
            results["agent0_excluded_from_agent1"] = {
                "enabled": True,
                "excluded": len(agent0_ids_for_agent1),
                "excluded_ids": sorted(agent0_ids_for_agent1),
            }
            _log_stage_result("agent0_excluded_from_agent1", results["agent0_excluded_from_agent1"])
    if a1_ids and agent_enabled(opts, "agent1"):
        logger.info("Pipeline STAGE START: agent1_address_validator ids=%d", len(a1_ids))
        if bus:
            bus.notify_agent_start(job_id, "agent1_address_validator", len(a1_ids))
        def _a1_cb(done, total):
            _stage_progress(progress_callback, 3, done, total)
        a1 = run_agent1_for_job(
            job_id,
            address_ids=a1_ids,
            progress_callback=_a1_cb,
            agent_options=opts.get("agent1"),
        )
        results["agent1_address_validator"] = a1
        _log_stage_result("agent1_address_validator", a1)
        if bus:
            bus.notify_agent_complete(job_id, "agent1_address_validator", a1)
            bus.broadcast_progress(job_id, "agent1_address_validator", _overall_pct(3, 1, 1))
    else:
        results["agent1_address_validator"] = {"skipped": True}
        _log_stage_result("agent1_address_validator", results["agent1_address_validator"])
    _stage_progress(progress_callback, 3, 1, 1)
    if agent_enabled(opts, "agent1") and a1_ids:
        non_a1_active = [address_id for address_id in active_ids if address_id not in set(a1_ids)]
        a1_continued, results["agent1_gate"] = _apply_resolution_gate(
            job_id,
            a1_ids,
            "agent1_address_validator",
            thresholds["agent1_address_validator"],
        )
        # Coord-only rows skip Agent 1; low-confidence Agent 1 rows continue downstream.
        active_ids = non_a1_active + a1_continued
        _log_stage_result("agent1_gate", results["agent1_gate"])
    else:
        results["agent1_gate"] = {
            "total": len(a1_ids),
            "accepted": 0,
            "continued": len(active_ids),
        }
        # Agent 1 disabled or no eligible rows â€” keep current active set for later agents.

    # â”€â”€ Stage 3: Agent 3 (Parcel) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if active_ids and agent_enabled(opts, "agent3"):
        logger.info("Pipeline STAGE START: agent3_parcel active_ids=%d", len(active_ids))
        if bus:
            bus.notify_agent_start(job_id, "agent3_parcel", len(active_ids))
        def _a3_cb(done, total):
            _stage_progress(progress_callback, 4, done, total)
        a3 = run_agent3_for_job(
            job_id,
            address_ids=active_ids,
            progress_callback=_a3_cb,
            agent_options=opts.get("agent3"),
        )
        results["agent3_parcel"] = a3
        _log_stage_result("agent3_parcel", a3)
        if bus:
            bus.notify_agent_complete(job_id, "agent3_parcel", a3)
            bus.broadcast_progress(job_id, "agent3_parcel", _overall_pct(4, 1, 1))
    else:
        results["agent3_parcel"] = {"skipped": True}
        _log_stage_result("agent3_parcel", results["agent3_parcel"])
    _stage_progress(progress_callback, 4, 1, 1)
    if agent_enabled(opts, "agent3"):
        active_ids, results["agent3_gate"] = _apply_resolution_gate(
            job_id,
            active_ids,
            "agent3_parcel",
            thresholds["agent3_parcel"],
        )
        _log_stage_result("agent3_gate", results["agent3_gate"])
    else:
        results["agent3_gate"] = {"total": len(active_ids), "accepted": 0, "continued": len(active_ids)}

    # â”€â”€ Agent 0 cleanup before building / Street View enrichment â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    agent0_cleanup = _delete_unconfirmed_agent0_discoveries(
        job_id,
        all_ids,
        reason=(
            "Agent 0 discovered address did not reach the required 90 confidence "
            "in Agent 1 geocoding, Agent 2 address validation, or Agent 3 parcel"
        ),
    )
    results["agent0_cleanup"] = agent0_cleanup
    if agent0_cleanup.get("deleted_ids"):
        deleted_set = set(agent0_cleanup["deleted_ids"])
        active_ids = [address_id for address_id in active_ids if address_id not in deleted_set]
        all_ids = [address_id for address_id in all_ids if address_id not in deleted_set]
        address_ids = [address_id for address_id in address_ids if address_id not in deleted_set]
        coord_only_ids = [address_id for address_id in coord_only_ids if address_id not in deleted_set]
    _log_stage_result("agent0_cleanup", agent0_cleanup)

    # â”€â”€ Stage 5: Agent 4 (Building) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    # Runs before Agent 5 so building classification is available first. Enriches
    # every household row, including rows that already met earlier confidence gates.
    if agent_enabled(opts, "agent4"):
        logger.info("Pipeline STAGE START: agent4_building all_ids=%d", len(all_ids))
        if bus:
            bus.notify_agent_start(job_id, "agent4_building", len(all_ids))
        def _a4_cb(done, total):
            _stage_progress(progress_callback, 5, done, total)
        a4 = run_agent4_for_job(
            job_id,
            address_ids=all_ids,
            progress_callback=_a4_cb,
            agent_options=opts.get("agent4"),
        )
        results["agent4_building"] = a4
        _log_stage_result("agent4_building", a4)
        if bus:
            bus.notify_agent_complete(job_id, "agent4_building", a4)
            bus.broadcast_progress(job_id, "agent4_building", _overall_pct(5, 1, 1))
    else:
        results["agent4_building"] = {"skipped": True, "reason": "agent disabled"}
        _log_stage_result("agent4_building", results["agent4_building"])
    _stage_progress(progress_callback, 5, 1, 1)

    # â”€â”€ Stage 6: Agent 5 (Street View) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    # Agent 5 is an enrichment/verification stage selected by Flow Builder.  Do
    # not feed it only the unresolved active_ids, because prior high-confidence
    # Agent 1/2 rows still need Street View columns in the UI when Agent 5 is
    # explicitly enabled.
    # Agent 5-0 is intentionally narrow: only rows with an available Agent 1,
    # Agent 2, or Agent 3 score below the gate are sent to offline OCR.
    agent50_options = opts.get("agent5_0") or {}
    agent50_gate = int(agent50_options.get("confidence_gate") or 90)
    agent50_ids, agent50_gate_summary = _ids_with_agent123_confidence_below(
        job_id,
        list(all_ids),
        agent50_gate,
    )
    results["agent5_0_confidence_gate"] = agent50_gate_summary
    _log_stage_result("agent5_0_confidence_gate", agent50_gate_summary)
    if agent_enabled(opts, "agent5_0") and not agent50_options.get("street_view", True):
        results["agent5_0_offline_ocr"] = {
            "skipped": True,
            "reason": "Street View option disabled",
            "input_rows": 0,
            **agent50_gate_summary,
        }
        _log_stage_result("agent5_0_offline_ocr", results["agent5_0_offline_ocr"])
    elif agent50_ids and agent_enabled(opts, "agent5_0"):
        logger.info(
            "Pipeline STAGE START: agent5_0_offline_ocr ids=%d gate=%d",
            len(agent50_ids),
            agent50_gate,
        )
        if bus:
            bus.notify_agent_start(job_id, "agent5_0_offline_ocr", len(agent50_ids))
        _stage_progress(progress_callback, 6, 0, max(len(agent50_ids), 1))

        base50 = agent50_default_config()
        # Resolve ocr_engine: respect explicit selection, default to vision_primary.
        # If user also unchecked paddle_ocr, force vision_only.
        raw_engine = str(agent50_options.get("ocr_engine") or base50.ocr_engine).strip().lower()
        if raw_engine not in ("vision_primary", "paddle_primary", "vision_only"):
            raw_engine = "vision_primary"
        if not agent50_options.get("paddle_ocr", True) and raw_engine == "paddle_primary":
            raw_engine = "vision_only"
        use_ollama = bool(agent50_options.get("ollama_guidance", base50.use_ollama))
        if raw_engine in ("vision_primary", "vision_only"):
            use_ollama = True
        cfg50 = replace(
            base50,
            max_workers=max(1, min(16, int(agent50_options.get("max_workers") or base50.max_workers))),
            max_iterations=max(1, min(10, int(agent50_options.get("max_iterations") or base50.max_iterations))),
            use_ollama=use_ollama,
            ocr_engine=raw_engine,
        )

        def _a50_cb(done, total):
            _stage_progress(progress_callback, 6, done, total)

        a50 = run_agent50_for_job(
            job_id,
            address_ids=agent50_ids,
            progress_callback=_a50_cb,
            config=cfg50,
        )
        a50["input_rows"] = len(agent50_ids)
        a50["confidence_gate"] = agent50_gate_summary
        results["agent5_0_offline_ocr"] = a50
        _log_stage_result("agent5_0_offline_ocr", a50)
        if bus:
            bus.notify_agent_complete(job_id, "agent5_0_offline_ocr", a50)
            bus.broadcast_progress(job_id, "agent5_0_offline_ocr", _overall_pct(6, 1, 1))
    else:
        if not agent_enabled(opts, "agent5_0"):
            a50_skip_reason = "agent disabled"
        elif not agent50_ids:
            a50_skip_reason = "no Agent 1/2/3 confidence below gate"
        else:
            a50_skip_reason = "skipped"
        results["agent5_0_offline_ocr"] = {
            "skipped": True,
            "reason": a50_skip_reason,
            "input_rows": 0,
            **agent50_gate_summary,
        }
        _log_stage_result("agent5_0_offline_ocr", results["agent5_0_offline_ocr"])
    _stage_progress(progress_callback, 6, 1, 1)

    agent5_ids = list(all_ids)
    if agent5_ids and agent_enabled(opts, "agent5"):
        logger.info("Pipeline STAGE START: agent5_streetview ids=%d", len(agent5_ids))
        if bus:
            bus.notify_agent_start(job_id, "agent5_streetview", len(agent5_ids))
        def _a5_cb(done, total):
            _stage_progress(progress_callback, 7, done, total)
        a5 = run_agent5_for_job(
            job_id,
            address_ids=agent5_ids,
            progress_callback=_a5_cb,
            agent_options=opts.get("agent5"),
        )
        a5["input_rows"] = len(agent5_ids)
        a5["low_confidence_rows"] = len(active_ids)
        results["agent5_streetview"] = a5
        _log_stage_result("agent5_streetview", a5)
        if bus:
            bus.notify_agent_complete(job_id, "agent5_streetview", a5)
            bus.broadcast_progress(job_id, "agent5_streetview", _overall_pct(7, 1, 1))
    else:
        if not agent_enabled(opts, "agent5"):
            a5_skip_reason = "agent disabled"
        elif not agent5_ids:
            a5_skip_reason = (
                "no address rows available for Agent 5"
            )
        else:
            a5_skip_reason = "skipped"
        results["agent5_streetview"] = {
            "skipped": True,
            "reason": a5_skip_reason,
            "input_rows": 0,
            "low_confidence_rows": 0,
        }
        _log_stage_result("agent5_streetview", results["agent5_streetview"])
    if agent_enabled(opts, "agent5"):
        active_ids, results["agent5_gate"] = _apply_resolution_gate(
            job_id,
            agent5_ids,
            "agent5_streetview",
            thresholds["agent5_streetview"],
        )
        _log_stage_result("agent5_gate", results["agent5_gate"])
    else:
        results["agent5_gate"] = {"total": len(active_ids), "accepted": 0, "continued": len(active_ids)}
    _stage_progress(progress_callback, 7, 1, 1)

    # â”€â”€ Stage 7: Agent 6 (Final) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    a6_ids = _ids_where_agent5_executed(job_id, active_ids) if agent_enabled(opts, "agent5") else []
    if a6_ids and agent_enabled(opts, "agent6"):
        logger.info("Pipeline STAGE START: agent6_final ids=%d", len(a6_ids))
        if bus:
            bus.notify_agent_start(job_id, "agent6_final", len(a6_ids))
        a6 = run_agent6_for_job(
            job_id,
            address_ids=a6_ids,
            agent_options=opts.get("agent6"),
        )
        results["agent6_final"] = a6
        _log_stage_result("agent6_final", a6)
        if bus:
            bus.notify_agent_complete(job_id, "agent6_final", a6)
    else:
        results["agent6_final"] = {"skipped": True}
        _log_stage_result("agent6_final", results["agent6_final"])
    _stage_progress(progress_callback, 8, 1, 1)
    if agent_enabled(opts, "agent6") and a6_ids:
        active_ids, results["agent6_gate"] = _apply_resolution_gate(
            job_id,
            a6_ids,
            "agent6_final",
            thresholds["agent6_final"],
        )
    else:
        results["agent6_gate"] = {"total": len(a6_ids), "accepted": 0, "continued": 0}

    # â”€â”€ Stage 8: Agent 7 (post-Final neighborhood discovery) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if agent_enabled(opts, "agent7"):
        logger.info("Pipeline STAGE START: agent7_neighborhood_discovery")
        if bus:
            bus.notify_agent_start(job_id, "agent7_neighborhood_discovery", 0)
        a7 = run_agent7_for_job(
            job_id,
            progress_callback=lambda done, total: _stage_progress(
                progress_callback, 9, done, total
            ),
            agent_options=opts.get("agent7"),
        )
        results["agent7_neighborhood_discovery"] = a7
        _log_stage_result("agent7_neighborhood_discovery", a7)
        if bus:
            bus.notify_agent_complete(job_id, "agent7_neighborhood_discovery", a7)
    else:
        results["agent7_neighborhood_discovery"] = {"skipped": True, "reason": "agent disabled"}
        _log_stage_result("agent7_neighborhood_discovery", results["agent7_neighborhood_discovery"])
    _stage_progress(progress_callback, 9, 1, 1)

    results["rule_engine"] = _refresh_rule_classification_after_processing(job_id)
    _log_stage_result("rule_engine", results["rule_engine"])
    _write_new_bundle_from_agent_results(job_id)

    if progress_callback:
        progress_callback(100, 100)

    # â”€â”€ Finalize A2A bus â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if bus:
        bus.notify_pipeline_complete(job_id, results)
        bus.disconnect()

    logger.info("Pipeline complete for job %s: %s", job_id, results)
    log_payload(logger, "Pipeline COMPLETE results", results)
    logger.info("=" * 72)
    return results


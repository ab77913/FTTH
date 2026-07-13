"""Agent 7: neighborhood discovery around finalized addresses.

The agent treats Final columns as the authoritative known-address set. It
samples a bounded number of nearby points, keeps every sample inside an
uploaded polygon, reverse geocodes in parallel, and removes any result already
present in Final addresses (by normalized text or coordinate proximity).
"""
from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Callable

from sqlalchemy import func, select, text
from sqlalchemy.orm.attributes import flag_modified

from data_ingestion.agents.agent0_house_discovery import (
    _google_api_key,
    _reverse_geocode,
    parse_polygon_coordinates,
)
from data_ingestion.database.db import get_session_factory
from data_ingestion.database.geo import set_address_geom
from data_ingestion.database.models import (
    Address,
    AddressResult,
    Agent1Result,
    AgentResult,
    AgentTable,
    DispatchQueue,
    IngestionJob,
    UploadedSourceRecord,
)
from data_ingestion.utils.agent_logging import configure_agent_logger
from data_ingestion.utils.discovery_source import build_new_address_discovery_metadata
from data_ingestion.utils.strings import normalize_address_key

try:
    from shapely.geometry import Point
except Exception:  # pragma: no cover - optional dependency guard
    Point = None

import logging

logger = logging.getLogger(__name__)

AGENT_NAME = "agent7_neighborhood_discovery"

DEFAULT_AGENT7_OPTIONS: dict[str, Any] = {
    "enabled": False,
    "search_distance_m": 60,
    "samples_per_address": 4,
    "max_candidates_per_job": 500,
    "concurrency": 8,
    "dedup_distance_m": 25,
    "validation_provider": "reverse_rooftop",
    "validation_threshold": 90,
    "use_validation_cache": False,
    "microsoft_building_enrichment": True,
}

ProgressCallback = Callable[..., None] | None


def _merge_options(raw: dict[str, Any] | None) -> dict[str, Any]:
    opts = dict(DEFAULT_AGENT7_OPTIONS)
    if not isinstance(raw, dict):
        return opts
    for key, default in DEFAULT_AGENT7_OPTIONS.items():
        if key not in raw:
            continue
        value = raw[key]
        try:
            if isinstance(default, bool):
                opts[key] = str(value).strip().lower() not in {"0", "false", "no", "off"} if isinstance(value, str) else bool(value)
            elif isinstance(default, str):
                opts[key] = str(value).strip().lower()
            else:
                opts[key] = max(1, int(value))
        except (TypeError, ValueError):
            logger.warning("Ignoring invalid Agent 7 option %s=%r", key, value)
    opts["validation_provider"] = _agent7_validation_provider(opts)
    opts["samples_per_address"] = min(8, int(opts["samples_per_address"]))
    opts["concurrency"] = min(16, int(opts["concurrency"]))
    return opts


def _distance_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
    return 2 * radius * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1 - a)))


def _near_any(coords: list[tuple[float, float]], lat: float, lon: float, threshold_m: float) -> bool:
    return any(_distance_meters(lat, lon, other_lat, other_lon) <= threshold_m for other_lat, other_lon in coords)


def _score(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def generate_neighborhood_candidates(
    seeds: list[dict[str, Any]],
    polygons: dict[int, Any],
    *,
    distance_m: float,
    samples_per_address: int,
    max_candidates: int,
) -> list[dict[str, Any]]:
    """Generate nearby candidate points in round-robin seed order.

    Two probes are placed per direction: one at distance_m and one at
    1.5 × distance_m.  The far probe ensures that when the next house is
    beyond distance_m, the reverse geocoder snaps forward to that house
    rather than back to the already-known seed address.
    """
    if Point is None:
        return []
    directions = (
        (1.0, 0.0), (-1.0, 0.0), (0.0, 1.0), (0.0, -1.0),
        (0.7071, 0.7071), (0.7071, -0.7071), (-0.7071, 0.7071), (-0.7071, -0.7071),
    )
    # Near probe (1×) catches houses within search_distance_m.
    # Far probe (1.5×) catches the next house when it sits just beyond that ring
    # and would otherwise cause the geocoder to snap back to the seed address.
    probe_radii = (distance_m, distance_m * 1.5)
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[float, float]] = set()
    limit = max(1, int(max_candidates))
    for direction_index in range(max(1, min(8, int(samples_per_address)))):
        north, east = directions[direction_index]
        for radius in probe_radii:
            for seed in seeds:
                polygon = polygons.get(int(seed["polygon_id"]))
                if polygon is None:
                    continue
                lat = float(seed["latitude"])
                lon = float(seed["longitude"])
                lat_offset = (radius * north) / 111_320.0
                lon_scale = max(0.1, math.cos(math.radians(lat)))
                lon_offset = (radius * east) / (111_320.0 * lon_scale)
                cand_lat = lat + lat_offset
                cand_lon = lon + lon_offset
                key = (round(cand_lat, 6), round(cand_lon, 6))
                if key in seen or not polygon.contains(Point(cand_lon, cand_lat)):
                    continue
                seen.add(key)
                candidates.append({
                    "seed_address_id": int(seed["address_id"]),
                    "polygon_id": int(seed["polygon_id"]),
                    "lat": cand_lat,
                    "lon": cand_lon,
                })
                if len(candidates) >= limit:
                    return candidates
    return candidates


def _neighborhood_from_geocode(geocode: dict[str, Any]) -> str:
    raw = geocode.get("raw") if isinstance(geocode.get("raw"), dict) else {}
    components = raw.get("address_components") if isinstance(raw, dict) else []
    priorities = ("neighborhood", "sublocality", "sublocality_level_1", "locality", "postal_code")
    for wanted in priorities:
        for component in components or []:
            if wanted in (component.get("types") or []):
                return str(component.get("long_name") or component.get("short_name") or "").strip()
    return ""


def _component_from_geocode(geocode: dict[str, Any], *wanted_types: str) -> str:
    raw = geocode.get("raw") if isinstance(geocode.get("raw"), dict) else {}
    components = raw.get("address_components") if isinstance(raw, dict) else []
    for wanted in wanted_types:
        for component in components or []:
            if wanted in (component.get("types") or []):
                return str(component.get("short_name") or component.get("long_name") or "").strip()
    return ""


def _create_address_from_discovery(
    session,
    *,
    job_id: str,
    discovery: dict[str, Any],
    seed_row: Address,
    polygon_row: Address,
) -> Address:
    """Persist one Agent 7 discovery as a Final-ready Address record."""
    address = str(discovery["address"]).strip()
    lat = float(discovery["latitude"])
    lon = float(discovery["longitude"])
    confidence = discovery.get("confidence")
    provider = str(discovery.get("provider") or "google_reverse_geocode")
    location_type = str(discovery.get("location_type") or "").strip().upper()
    final = {
        "source_agent": AGENT_NAME,
        "provider": provider,
        "confidence": confidence,
        "address": address,
        "latitude": lat,
        "longitude": lon,
        "location_type": location_type,
        "status": "accepted",
        "reason": "Polygon-constrained neighborhood address discovered by Agent 7",
    }
    meta = {
        "agent7_discovered": True,
        "agent7_seed_address_id": seed_row.id,
        "agent7_polygon_address_id": polygon_row.id,
        "agent7_neighborhood": discovery.get("neighborhood") or "",
        "agent7_reverse_geocode_location_type": location_type,
        "geometry_type": "Point",
        "coordinates": f"{lon},{lat},0",
        "placemark_name": address,
        "category": "household",
        "folder_path": "Agent 7",
        "map_layer_only": False,
        "file_role": "geospatial",
        "source_format": "agent7",
        "original_source_file": polygon_row.source_file or seed_row.source_file,
        "merge_status": "new",
        "merge_color": "yellow",
        "rule_status": "new",
        "rule_color": "yellow",
        "final_resolution": final,
        "final_address": address,
        "final_latitude": lat,
        "final_longitude": lon,
        "final_confidence": confidence,
        "final_source_agent": AGENT_NAME,
        "final_provider": provider,
        "final_location_type": location_type,
        "final_structure_type": "UNKNOWN",
        "final_structure_confidence": 0,
        "final_status": "accepted",
        "_uploaded_data": {},
        "_uploaded_columns": [],
    }
    meta.update(
        build_new_address_discovery_metadata(
            agent="agent7",
            provider=provider,
            location_type=location_type,
            base_reason="New address record discovered by Agent 7 inside polygon",
        )
    )
    row = Address(
        job_id=job_id,
        customer_id=seed_row.customer_id,
        raw_address=address,
        city=str(discovery.get("city") or ""),
        state=str(discovery.get("state") or ""),
        zip_code=str(discovery.get("zip_code") or ""),
        latitude=lat,
        longitude=lon,
        source_raw_address=address,
        source_latitude=lat,
        source_longitude=lon,
        validated_raw_address=address,
        validated_latitude=lat,
        validated_longitude=lon,
        coord_address_match_status="REVERSE_GEOCODED",
        reverse_geocode_confidence_score=confidence,
        normalized_key=normalize_address_key(address),
        source_file=polygon_row.source_file or seed_row.source_file or AGENT_NAME,
        source_sheet=None,
        source_layer="Agent 7 Neighborhood Discovery",
        source_row_number=None,
        raw_metadata=meta,
    )
    set_address_geom(row, lon, lat)
    session.add(row)
    session.flush()
    return row


def enrich_agent7_records_with_microsoft(job_id: str, address_ids: list[int]) -> dict[str, Any]:
    """Run Agent 4 Microsoft footprints for new rows and copy results to Final metadata."""
    if not address_ids:
        return {"processed": 0, "updated": 0, "matched": 0, "skipped": True}

    from data_ingestion.agents.agent4_building import run_agent4_for_job

    agent4_summary = run_agent4_for_job(
        job_id,
        address_ids=address_ids,
        agent_options={
            "enabled": True,
            "building_footprint": True,
            "building_provider": "microsoft",
        },
    )
    session = get_session_factory()()
    try:
        addresses = {
            row.id: row
            for row in session.scalars(select(Address).where(Address.id.in_(address_ids))).all()
        }
        a4_results = session.scalars(select(AgentResult).where(
            AgentResult.agent_name == "agent4_building",
            AgentResult.address_id.in_(address_ids),
        )).all()
        updated = 0
        matched = 0
        for result in a4_results:
            address = addresses.get(result.address_id)
            if address is None:
                continue
            data = dict(result.data or {})
            structure_type = str(data.get("structure_type") or "UNKNOWN")
            confidence = data.get("confidence")
            confidence = 0 if confidence in (None, "") else confidence
            structure_provider = (
                data.get("footprint_source")
                or data.get("source_agent")
                or "microsoft_footprint"
            )
            meta = dict(address.raw_metadata or {})
            final = dict(meta.get("final_resolution") or {})
            final.update({
                "structure_type": structure_type,
                "structure_confidence": confidence,
                "structure_provider": structure_provider,
            })
            meta.update({
                "final_resolution": final,
                "final_structure_type": structure_type,
                "final_structure_confidence": confidence,
                "final_structure_provider": structure_provider,
                "agent4_structure_type": structure_type,
                "agent4_confidence": confidence,
                "agent4_building_provider": structure_provider,
            })
            address.raw_metadata = meta

            a7_result = session.scalar(select(AgentResult).where(
                AgentResult.agent_name == AGENT_NAME,
                AgentResult.address_id == address.id,
            ))
            if a7_result is not None:
                a7_data = dict(a7_result.data or {})
                a7_data.update({
                    "structure_type": structure_type,
                    "structure_confidence": confidence,
                    "structure_provider": structure_provider,
                })
                a7_result.data = a7_data
            updated += 1
            if data.get("building_matched"):
                matched += 1
        session.commit()
        return {
            "processed": len(address_ids),
            "updated": updated,
            "matched": matched,
            "provider": "microsoft",
            "agent4": agent4_summary,
        }
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _agent7_validation_scores(job_id: str, address_ids: list[int]) -> dict[int, dict[str, Any]]:
    """Read validation signals for Agent 7-created rows."""
    ids = [int(address_id) for address_id in address_ids if address_id is not None]
    if not ids:
        return {}
    session = get_session_factory()()
    try:
        scores: dict[int, dict[str, Any]] = {address_id: {} for address_id in ids}
        for row in session.scalars(select(Address).where(
            Address.job_id == job_id,
            Address.id.in_(ids),
        )).all():
            meta = row.raw_metadata if isinstance(row.raw_metadata, dict) else {}
            final = meta.get("final_resolution") if isinstance(meta.get("final_resolution"), dict) else {}
            location_type = str(
                meta.get("agent7_reverse_geocode_location_type")
                or meta.get("final_location_type")
                or final.get("location_type")
                or ""
            ).strip().upper()
            reverse_confidence = _score(
                meta.get("final_confidence")
                if meta.get("final_confidence") is not None
                else row.reverse_geocode_confidence_score
            )
            scores.setdefault(int(row.id), {})["reverse_location_type"] = location_type
            scores.setdefault(int(row.id), {})["reverse_rooftop"] = 100 if location_type == "ROOFTOP" else 0
            scores.setdefault(int(row.id), {})["reverse_confidence"] = reverse_confidence

        for row in session.scalars(select(Agent1Result).where(
            Agent1Result.job_id == job_id,
            Agent1Result.address_id.in_(ids),
        )).all():
            scores.setdefault(int(row.address_id), {})["smarty"] = _score(row.confidence_score)
            scores.setdefault(int(row.address_id), {})["smarty_status"] = row.validation_status or ""
            scores.setdefault(int(row.address_id), {})["smarty_address"] = (
                row.smarty_standardized_address
                or row.chosen_standardized_address
                or ""
            )
            scores.setdefault(int(row.address_id), {})["smarty_latitude"] = row.smarty_lat
            scores.setdefault(int(row.address_id), {})["smarty_longitude"] = row.smarty_lon
            scores.setdefault(int(row.address_id), {})["smarty_provider"] = row.chosen_provider or "smarty"

        for row in session.scalars(select(AgentResult).where(
            AgentResult.job_id == job_id,
            AgentResult.address_id.in_(ids),
            AgentResult.agent_name == "agent3_parcel",
        )).all():
            data = row.data or {}
            if str(data.get("source") or "").strip().lower() != "regrid":
                continue
            scores.setdefault(int(row.address_id), {})["regrid"] = _score(data.get("confidence"))
            scores.setdefault(int(row.address_id), {})["regrid_status"] = data.get("status") or ""
            scores.setdefault(int(row.address_id), {})["regrid_source"] = data.get("source") or ""
        return scores
    finally:
        session.close()


def _agent7_validation_provider(opts: dict[str, Any]) -> str:
    """Return selected Agent 7 validation provider, with legacy option fallback."""
    provider = str(opts.get("validation_provider") or "").strip().lower()
    if provider in {"reverse_rooftop", "rooftop", "reverse_geocode", "reverse_geocoding"}:
        return "reverse_rooftop"
    if provider in {"smarty", "regrid"}:
        return provider
    if opts.get("validate_with_regrid") and not opts.get("validate_with_smarty", True):
        return "regrid"
    return "smarty"


def _passes_agent7_validation(provider_scores: dict[str, Any], threshold: int, provider: str = "smarty") -> bool:
    """Accept a new Agent 7 row only when the selected provider is above threshold."""
    if provider == "reverse_rooftop":
        return str(provider_scores.get("reverse_location_type") or "").strip().upper() == "ROOFTOP"
    return _score(provider_scores.get(provider)) > threshold


def _delete_agent7_records(job_id: str, address_ids: list[int], *, reason: str) -> dict[str, Any]:
    """Delete Agent 7-created address rows that failed validation."""
    ids = [int(address_id) for address_id in address_ids if address_id is not None]
    if not ids:
        return {"checked": 0, "deleted": 0, "deleted_ids": []}

    session = get_session_factory()()
    try:
        rows = session.scalars(select(Address).where(
            Address.job_id == job_id,
            Address.id.in_(ids),
        )).all()
        delete_ids: list[int] = []
        for row in rows:
            meta = row.raw_metadata if isinstance(row.raw_metadata, dict) else {}
            if meta.get("agent7_discovered") is True:
                delete_ids.append(int(row.id))

        if not delete_ids:
            return {"checked": len(ids), "deleted": 0, "deleted_ids": []}

        session.query(UploadedSourceRecord).filter(UploadedSourceRecord.address_id.in_(delete_ids)).update(
            {"address_id": None, "merge_status": "invalid", "merge_color": "red"},
            synchronize_session=False,
        )
        session.query(DispatchQueue).filter(DispatchQueue.address_id.in_(delete_ids)).delete(synchronize_session=False)
        session.query(AddressResult).filter(AddressResult.address_id.in_(delete_ids)).delete(synchronize_session=False)
        session.query(Agent1Result).filter(Agent1Result.address_id.in_(delete_ids)).delete(synchronize_session=False)
        session.query(AgentResult).filter(AgentResult.address_id.in_(delete_ids)).delete(synchronize_session=False)
        session.query(Address).filter(Address.id.in_(delete_ids)).delete(synchronize_session=False)

        job = session.get(IngestionJob, job_id)
        if job is not None:
            job.row_count = int(session.scalar(
                select(func.count()).select_from(Address).where(Address.job_id == job_id)
            ) or 0)

        session.commit()
        logger.info("Agent7 deleted %d unvalidated new row(s): %s", len(delete_ids), reason)
        return {"checked": len(ids), "deleted": len(delete_ids), "deleted_ids": delete_ids, "reason": reason}
    except Exception:
        session.rollback()
        logger.exception("Agent7 validation cleanup failed job_id=%s", job_id)
        raise
    finally:
        session.close()


def _apply_agent7_validation_acceptance(
    job_id: str,
    accepted_ids: list[int],
    scores: dict[int, dict[str, Any]],
    *,
    threshold: int,
    provider: str,
) -> dict[str, Any]:
    """Persist validation metadata and selected-provider final confidence."""
    ids = [int(address_id) for address_id in accepted_ids if address_id is not None]
    if not ids:
        return {"updated": 0, "smarty_final_updates": 0}

    session = get_session_factory()()
    try:
        rows = session.scalars(select(Address).where(
            Address.job_id == job_id,
            Address.id.in_(ids),
        )).all()
        updated = 0
        smarty_final_updates = 0
        for row in rows:
            provider_scores = scores.get(int(row.id), {})
            meta = dict(row.raw_metadata or {})
            final = dict(meta.get("final_resolution") or {})
            meta["agent7_validation"] = {
                "provider": provider,
                "threshold": threshold,
                "accepted": True,
                "smarty_confidence": provider_scores.get("smarty", 0),
                "smarty_status": provider_scores.get("smarty_status", ""),
                "regrid_confidence": provider_scores.get("regrid", 0),
                "regrid_status": provider_scores.get("regrid_status", ""),
                "regrid_source": provider_scores.get("regrid_source", ""),
                "reverse_location_type": provider_scores.get("reverse_location_type", ""),
                "reverse_confidence": provider_scores.get("reverse_confidence", 0),
            }

            provider_confidence = int(round(_score(provider_scores.get(provider))))
            if provider == "smarty":
                final_address = str(provider_scores.get("smarty_address") or "").strip() or str(
                    final.get("address") or meta.get("final_address") or row.raw_address or ""
                ).strip()
                final_lat = provider_scores.get("smarty_latitude")
                final_lon = provider_scores.get("smarty_longitude")
                if final_lat in (None, ""):
                    final_lat = final.get("latitude") if final.get("latitude") is not None else meta.get("final_latitude")
                if final_lon in (None, ""):
                    final_lon = final.get("longitude") if final.get("longitude") is not None else meta.get("final_longitude")
                final.update({
                    "source_agent": "agent1_address_validator",
                    "provider": provider_scores.get("smarty_provider") or "smarty",
                    "confidence": provider_confidence,
                    "address": final_address,
                    "latitude": final_lat,
                    "longitude": final_lon,
                    "status": provider_scores.get("smarty_status") or "AUTO_ACCEPT",
                    "reason": "Agent 7 discovery validated by Smarty Streets",
                })
                meta.update({
                    "final_resolution": final,
                    "final_address": final_address,
                    "final_latitude": final_lat,
                    "final_longitude": final_lon,
                    "final_confidence": provider_confidence,
                    "final_source_agent": "agent1_address_validator",
                    "final_provider": provider_scores.get("smarty_provider") or "smarty",
                    "final_status": provider_scores.get("smarty_status") or "AUTO_ACCEPT",
                })
                row.validated_raw_address = final_address
                row.validated_latitude = final_lat
                row.validated_longitude = final_lon
                row.reverse_geocode_confidence_score = provider_confidence
                row.normalized_key = normalize_address_key(final_address)
                smarty_final_updates += 1
            elif provider == "regrid":
                final.update({
                    "source_agent": AGENT_NAME,
                    "provider": "regrid",
                    "confidence": provider_confidence,
                    "status": provider_scores.get("regrid_status") or "found",
                    "reason": "Agent 7 discovery validated by Regrid",
                })
                meta["final_resolution"] = final
                meta["final_confidence"] = provider_confidence
                meta["final_provider"] = "regrid"
                meta["final_status"] = provider_scores.get("regrid_status") or "found"
            else:
                location_type = str(provider_scores.get("reverse_location_type") or "").strip().upper()
                final.update({
                    "source_agent": AGENT_NAME,
                    "provider": "google_reverse_geocode",
                    "confidence": provider_confidence,
                    "location_type": location_type,
                    "status": "ROOFTOP",
                    "reason": "Agent 7 discovery validated by reverse geocode ROOFTOP location_type",
                })
                meta["final_resolution"] = final
                meta["final_confidence"] = provider_confidence
                meta["final_provider"] = "google_reverse_geocode"
                meta["final_location_type"] = location_type
                meta["final_status"] = "ROOFTOP"

            row.raw_metadata = meta
            flag_modified(row, "raw_metadata")

            a7_result = session.scalar(select(AgentResult).where(
                AgentResult.agent_name == AGENT_NAME,
                AgentResult.address_id == row.id,
            ))
            if a7_result is not None:
                data = dict(a7_result.data or {})
                data.update({
                    "status": "validated_new_record",
                    "validation_provider": provider,
                    "validation_threshold": threshold,
                    "smarty_confidence": provider_scores.get("smarty", 0),
                    "regrid_confidence": provider_scores.get("regrid", 0),
                    "reverse_location_type": provider_scores.get("reverse_location_type", ""),
                    "reverse_confidence": provider_scores.get("reverse_confidence", 0),
                    "validated_by": provider,
                })
                a7_result.data = data
            updated += 1

        session.commit()
        return {"updated": updated, "smarty_final_updates": smarty_final_updates}
    except Exception:
        session.rollback()
        logger.exception("Agent7 acceptance metadata update failed job_id=%s", job_id)
        raise
    finally:
        session.close()


def validate_agent7_records(job_id: str, address_ids: list[int], opts: dict[str, Any]) -> dict[str, Any]:
    """Validate Agent 7 rows with the selected provider and delete rows below threshold."""
    ids = [int(address_id) for address_id in address_ids if address_id is not None]
    if not ids:
        return {"checked": 0, "accepted": 0, "deleted": 0, "deleted_ids": []}

    provider = _agent7_validation_provider(opts)
    threshold = int(opts.get("validation_threshold") or 90)
    validators: dict[str, Any] = {}

    if provider == "smarty":
        from data_ingestion.agents.agent1_address_validation import run_agent1_for_job

        validators["smarty"] = run_agent1_for_job(
            job_id,
            address_ids=ids,
            agent_options={
                "enabled": True,
                "smarty": True,
                "melissa": False,
                "use_cache": bool(opts.get("use_validation_cache", False)),
            },
        )
    elif provider == "regrid":
        from data_ingestion.agents.agent3_parcel import run_agent3_for_job

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
    else:
        validators["reverse_rooftop"] = {
            "skipped_external_provider": True,
            "rule": "keep only reverse geocode results where location_type == ROOFTOP",
        }

    scores = _agent7_validation_scores(job_id, ids)
    accepted_ids: list[int] = []
    rejected_ids: list[int] = []
    for address_id in ids:
        provider_scores = scores.get(address_id, {})
        if _passes_agent7_validation(provider_scores, threshold, provider):
            accepted_ids.append(address_id)
        else:
            rejected_ids.append(address_id)

    acceptance = _apply_agent7_validation_acceptance(
        job_id,
        accepted_ids,
        scores,
        threshold=threshold,
        provider=provider,
    )
    cleanup = _delete_agent7_records(
        job_id,
        rejected_ids,
        reason=(
            "Agent 7 reverse geocode location_type was not ROOFTOP"
            if provider == "reverse_rooftop"
            else f"Agent 7 {provider} validation confidence not greater than {threshold}"
        ),
    )
    return {
        "checked": len(ids),
        "accepted": len(accepted_ids),
        "accepted_ids": accepted_ids,
        "deleted": cleanup.get("deleted", 0),
        "deleted_ids": cleanup.get("deleted_ids", []),
        "provider": provider,
        "threshold": threshold,
        "validators": list(validators.keys()),
        "validator_results": validators,
        "scores": scores,
        **acceptance,
    }


def filter_new_geocodes(
    geocodes: list[dict[str, Any]],
    *,
    final_address_keys: set[str],
    final_coords: list[tuple[float, float]],
    polygons: dict[int, Any],
    dedup_distance_m: float,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Remove Final-address matches, nearby matches, and duplicate discoveries."""
    accepted: list[dict[str, Any]] = []
    discovered_keys: set[str] = set()
    discovered_coords: list[tuple[float, float]] = []
    counts = {"duplicate_final": 0, "duplicate_discovery": 0, "outside_polygon": 0, "failed": 0}
    for item in geocodes:
        geocode = item.get("geocode") or {}
        address = str(geocode.get("address") or "").strip()
        if not address:
            counts["failed"] += 1
            continue
        try:
            lat = float(geocode.get("latitude"))
            lon = float(geocode.get("longitude"))
        except (TypeError, ValueError):
            counts["failed"] += 1
            continue
        polygon = polygons.get(int(item["polygon_id"]))
        if polygon is None or Point is None or not polygon.contains(Point(lon, lat)):
            counts["outside_polygon"] += 1
            continue
        key = normalize_address_key(address)
        if (key and key in final_address_keys) or _near_any(final_coords, lat, lon, dedup_distance_m):
            counts["duplicate_final"] += 1
            continue
        if (key and key in discovered_keys) or _near_any(discovered_coords, lat, lon, dedup_distance_m):
            counts["duplicate_discovery"] += 1
            continue
        if key:
            discovered_keys.add(key)
        discovered_coords.append((lat, lon))
        accepted.append({
            "seed_address_id": int(item["seed_address_id"]),
            "address": address,
            "latitude": lat,
            "longitude": lon,
            "confidence": geocode.get("confidence"),
            "provider": geocode.get("provider") or "google_reverse_geocode",
            "location_type": str(geocode.get("location_type") or "").strip().upper(),
            "neighborhood": _neighborhood_from_geocode(geocode),
            "city": _component_from_geocode(geocode, "locality", "postal_town"),
            "state": _component_from_geocode(geocode, "administrative_area_level_1"),
            "zip_code": _component_from_geocode(geocode, "postal_code"),
            "polygon_id": int(item["polygon_id"]),
        })
    return accepted, counts


def _ensure_agent_table(session) -> None:
    table = session.scalar(select(AgentTable).where(AgentTable.agent_name == AGENT_NAME))
    if table:
        return
    session.add(AgentTable(
        agent_name=AGENT_NAME,
        display_name="Agent 7: Neighborhood Discovery",
        owner="system",
        description="Finds polygon-constrained neighborhood addresses missing from Final columns.",
        color_rules=[
            {"field": "new_address_count", "operator": ">", "value": 0, "color": "#06b6d4", "label": "New addresses"},
        ],
    ))
    session.flush()


def _upsert_result(session, *, job_id: str, address_id: int, data: dict[str, Any]) -> None:
    row = session.scalar(select(AgentResult).where(
        AgentResult.agent_name == AGENT_NAME,
        AgentResult.address_id == address_id,
    ))
    if row:
        row.job_id = job_id
        row.data = data
        row.updated_at = datetime.utcnow()
    else:
        session.add(AgentResult(
            agent_name=AGENT_NAME,
            job_id=job_id,
            address_id=address_id,
            data=data,
        ))


def run_agent7_for_job(
    job_id: str,
    *,
    progress_callback: ProgressCallback = None,
    agent_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Find addresses near Final rows that are absent from every Final address."""
    log_path = configure_agent_logger(logger, AGENT_NAME)
    opts = _merge_options(agent_options)
    logger.info("Agent7 START job_id=%s options=%s log=%s", job_id, opts, log_path)
    if not opts.get("enabled"):
        return {"skipped": True, "reason": "agent disabled"}
    if Point is None:
        return {"skipped": True, "reason": "shapely is not installed"}
    api_key = _google_api_key()
    if not api_key:
        return {"skipped": True, "reason": "Google geocoding API key is not set"}

    session = get_session_factory()()
    try:
        _ensure_agent_table(session)
        polygon_rows = session.scalars(select(Address).where(
            Address.job_id == job_id,
            text("raw_metadata->>'geometry_type' = 'Polygon'"),
            text("COALESCE(raw_metadata->>'map_layer_only','false') = 'true'"),
        ).order_by(Address.id)).all()
        polygons = {
            row.id: parse_polygon_coordinates((row.raw_metadata or {}).get("coordinates"))
            for row in polygon_rows
        }
        polygons = {key: value for key, value in polygons.items() if value is not None}
        if not polygons:
            session.commit()
            return {"skipped": True, "reason": "no polygon map layers", "polygons": 0}

        rows = session.scalars(select(Address).where(
            Address.job_id == job_id,
            text("COALESCE(raw_metadata->>'map_layer_only','false') != 'true'"),
        ).order_by(Address.id)).all()
        final_address_keys: set[str] = set()
        final_coords: list[tuple[float, float]] = []
        seeds: list[dict[str, Any]] = []
        for row in rows:
            meta = row.raw_metadata if isinstance(row.raw_metadata, dict) else {}
            final = meta.get("final_resolution") if isinstance(meta.get("final_resolution"), dict) else {}
            address = str(final.get("address") or meta.get("final_address") or "").strip()
            lat = final.get("latitude") if final.get("latitude") is not None else meta.get("final_latitude")
            lon = final.get("longitude") if final.get("longitude") is not None else meta.get("final_longitude")
            key = normalize_address_key(address)
            if key:
                final_address_keys.add(key)
            raw_key = row.normalized_key or normalize_address_key(row.raw_address)
            if raw_key:
                final_address_keys.add(raw_key)
            try:
                flat, flon = float(lat), float(lon)
            except (TypeError, ValueError):
                continue
            final_coords.append((flat, flon))
            point = Point(flon, flat)
            polygon_id = next((pid for pid, polygon in polygons.items() if polygon.contains(point)), None)
            if address and polygon_id is not None:
                seeds.append({
                    "address_id": row.id,
                    "final_address": address,
                    "latitude": flat,
                    "longitude": flon,
                    "polygon_id": polygon_id,
                })

        if not seeds:
            session.commit()
            return {"skipped": True, "reason": "no Final addresses inside polygons", "polygons": len(polygons)}

        candidates = generate_neighborhood_candidates(
            seeds,
            polygons,
            distance_m=float(opts["search_distance_m"]),
            samples_per_address=int(opts["samples_per_address"]),
            max_candidates=int(opts["max_candidates_per_job"]),
        )

        geocoded: list[dict[str, Any]] = []
        completed = 0
        with ThreadPoolExecutor(max_workers=int(opts["concurrency"])) as executor:
            futures = {
                executor.submit(_reverse_geocode, candidate["lat"], candidate["lon"], api_key): candidate
                for candidate in candidates
            }
            for future in as_completed(futures):
                candidate = futures[future]
                try:
                    geocode = future.result()
                except Exception as exc:
                    logger.warning("Agent7 reverse geocode failed candidate=%s error=%s", candidate, exc)
                    geocode = None
                geocoded.append({**candidate, "geocode": geocode})
                completed += 1
                if progress_callback:
                    progress_callback(completed, len(candidates))

        accepted, filter_counts = filter_new_geocodes(
            geocoded,
            final_address_keys=final_address_keys,
            final_coords=final_coords,
            polygons=polygons,
            dedup_distance_m=float(opts["dedup_distance_m"]),
        )
        seed_rows = {int(row.id): row for row in rows}
        polygon_row_map = {int(row.id): row for row in polygon_rows}
        created_ids: list[int] = []
        for discovery in accepted:
            new_row = _create_address_from_discovery(
                session,
                job_id=job_id,
                discovery=discovery,
                seed_row=seed_rows[int(discovery["seed_address_id"])],
                polygon_row=polygon_row_map[int(discovery["polygon_id"])],
            )
            discovery["created_address_id"] = new_row.id
            created_ids.append(new_row.id)
            _upsert_result(session, job_id=job_id, address_id=new_row.id, data={
                "status": "accepted_new_record",
                "is_new_record": True,
                "final_address": discovery["address"],
                "new_addresses": [discovery["address"]],
                "new_address_count": 1,
                "new_address_details": [discovery],
                "candidates_checked": 1,
                "created_address_id": new_row.id,
            })

        session.commit()
        validation_summary = validate_agent7_records(job_id, created_ids, opts)
        validated_id_set = {
            int(address_id)
            for address_id in validation_summary.get("accepted_ids", [])
            if address_id is not None
        }
        accepted = [
            discovery
            for discovery in accepted
            if int(discovery.get("created_address_id") or 0) in validated_id_set
        ]
        validation_scores = validation_summary.get("scores", {})
        for discovery in accepted:
            created_address_id = int(discovery.get("created_address_id") or 0)
            discovery["validation"] = validation_scores.get(
                created_address_id,
                validation_scores.get(str(created_address_id), {}),
            )
        created_ids = [
            address_id
            for address_id in created_ids
            if int(address_id) in validated_id_set
        ]

        by_seed: dict[int, list[dict[str, Any]]] = {int(seed["address_id"]): [] for seed in seeds}
        for discovery in accepted:
            by_seed.setdefault(int(discovery["seed_address_id"]), []).append(discovery)

        seed_map = {int(seed["address_id"]): seed for seed in seeds}
        for address_id, discoveries in by_seed.items():
            seed_is_new_record = bool(
                (seed_rows[address_id].raw_metadata or {}).get("agent7_discovered")
            )
            seed_final_address = seed_map[address_id]["final_address"]
            _upsert_result(session, job_id=job_id, address_id=address_id, data={
                "status": "accepted_new_record" if seed_is_new_record else "completed",
                "is_new_record": seed_is_new_record,
                "final_address": seed_final_address,
                "new_addresses": [seed_final_address] if seed_is_new_record else [],
                "new_address_count": 1 if seed_is_new_record else len(discoveries),
                "discovered_addresses": [item["address"] for item in discoveries],
                "created_address_ids": [item["created_address_id"] for item in discoveries],
                "new_address_details": discoveries,
                "candidates_checked": sum(1 for item in candidates if item["seed_address_id"] == address_id),
            })

        job = session.get(IngestionJob, job_id)
        if job is not None:
            job.row_count = int(session.scalar(
                select(func.count()).select_from(Address).where(Address.job_id == job_id)
            ) or 0)

        session.commit()
        structure_summary: dict[str, Any]
        if opts.get("microsoft_building_enrichment", True) and created_ids:
            try:
                structure_summary = enrich_agent7_records_with_microsoft(job_id, created_ids)
            except Exception as exc:
                logger.exception("Agent7 Microsoft structure enrichment failed job_id=%s", job_id)
                structure_summary = {"processed": len(created_ids), "updated": 0, "error": str(exc)}
        else:
            structure_summary = {
                "processed": 0,
                "updated": 0,
                "skipped": True,
                "reason": "disabled or no new records",
            }
        summary = {
            "polygons": len(polygons),
            "final_addresses": len(final_address_keys),
            "seed_addresses": len(seeds),
            "candidates_checked": len(candidates),
            "new_addresses": len(accepted),
            "new_records_created": len(created_ids),
            "created_address_ids": created_ids,
            "validation": validation_summary,
            "microsoft_building_enrichment": structure_summary,
            **filter_counts,
        }
        logger.info("Agent7 COMPLETE %s", summary)
        return summary
    except Exception:
        session.rollback()
        logger.exception("Agent7 failed job_id=%s", job_id)
        raise
    finally:
        session.close()

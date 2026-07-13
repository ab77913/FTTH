"""
Agent 6 — Final FTTH Classification
======================================
Synthesizes outputs from Agents 1–5 into a final FTTH suitability assessment:
  - final_structure_type  (SFH / MDU_SMALL / MDU_LARGE / Commercial / Unknown)
  - final_is_mdu          (bool)
  - final_unit_count      (estimated unit count for MDU)
  - final_confidence      (0–100)
  - ftth_priority         (HIGH / MEDIUM / LOW / SKIP)
  - validation_summary    (plain-text explanation)

Logic:
  1. Start from Agent 2 validation_status and confidence_score
  2. Refine coordinates from Agent 2 if Agent 1 coordinates were missing
  3. Incorporate Agent 3 land_use classification
  4. Weight Agent 4 (address-level) and Agent 5 (image-level) structure_type
  5. Emit priority: HIGH → validated MDU ≥8 units, MEDIUM → 2-7 units MDU or SFH,
     LOW → Commercial, SKIP → rejected / no data

Results stored in agent_results with agent_name="agent6_final".
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select as _sel, text as _txt

from data_ingestion.database.db import get_session_factory
from data_ingestion.database.models import Address, Agent1Result, AgentResult, AgentTable
from data_ingestion.utils.agent_logging import configure_agent_logger, log_payload

logger = logging.getLogger(__name__)

_AGENT_NAME = "agent6_final"
_DISPLAY_NAME = "Agent 6: Final FTTH Classification"

_A2 = "agent2_geocoding"
_A3 = "agent3_parcel"
_A4 = "agent4_building"
_A5 = "agent5_streetview"


def _ensure_table(session) -> None:
    if not session.execute(_sel(AgentTable).where(AgentTable.agent_name == _AGENT_NAME)).scalar_one_or_none():
        session.add(AgentTable(
            agent_name=_AGENT_NAME,
            display_name=_DISPLAY_NAME,
            owner="system",
            description="Final FTTH suitability classification synthesised from all prior agents",
            color_rules=[
                {"field": "ftth_priority", "value": "HIGH",   "color": "#16a34a", "label": "High Priority"},
                {"field": "ftth_priority", "value": "MEDIUM", "color": "#2563eb", "label": "Medium Priority"},
                {"field": "ftth_priority", "value": "LOW",    "color": "#d97706", "label": "Low Priority"},
                {"field": "ftth_priority", "value": "SKIP",   "color": "#6b7280", "label": "Skip"},
            ],
        ))
        session.commit()


def _upsert(session, job_id: str, address_id: int, data: dict) -> None:
    from sqlalchemy.dialects.postgresql import insert as _pg_insert
    now = datetime.utcnow()
    stmt = _pg_insert(AgentResult).values(
        agent_name=_AGENT_NAME, job_id=job_id, address_id=address_id,
        data=data, created_at=now, updated_at=now,
    ).on_conflict_do_update(
        constraint="uq_agent_results_agent_address",
        set_={"data": data, "updated_at": now, "job_id": job_id},
    )
    session.execute(stmt)


def _majority_vote(types: list[str]) -> str:
    """Return the most frequent structure type; prefer more specific over 'Unknown'."""
    counts: dict[str, int] = {}
    for t in types:
        if t and t != "Unknown":
            counts[t] = counts.get(t, 0) + 1
    if not counts:
        return "Unknown"
    return max(counts, key=lambda k: counts[k])


def _address_is_vacant_flagged(addr: Address | None, a1: Agent1Result | None) -> bool:
    if a1 is not None and (a1.smarty_vacant is True or a1.melissa_vacant is True):
        return True
    if addr is None:
        return False
    meta = addr.raw_metadata if isinstance(addr.raw_metadata, dict) else {}
    av = meta.get("address_validation") if isinstance(meta.get("address_validation"), dict) else {}
    for key in ("vacant", "dpv_vacant", "smarty_vacant", "melissa_vacant"):
        value = av.get(key)
        if value is True or str(value or "").strip().upper() in {"Y", "YES", "TRUE", "1"}:
            return True
    return False


def _normalize_structure_type(value: Any) -> str:
    """Map detailed vision labels and legacy agent labels to A6 categories."""
    raw = str(value or "").strip()
    key = raw.lower().replace("-", "_").replace(" ", "_")
    mapping = {
        "sfh": "SFH",
        "single_family": "SFH",
        "single_family_residential": "SFH",
        "detached_house": "SFH",
        "house": "SFH",
        "home": "SFH",
        "mdu": "MDU",
        "multi_family": "MDU",
        "multifamily": "MDU",
        "multi_family_residential": "MDU",
        "low_rise_apt": "MDU",
        "high_rise_apt": "MDU",
        "apartment": "MDU",
        "apartment_building": "MDU",
        "townhouse": "MDU",
        "commercial": "Commercial",
        "industrial": "Commercial",
        "mixed_use": "Commercial",
        "vacant": "Vacant",
        "vacant_lot": "Vacant",
        "empty_lot": "Vacant",
        "no_structure": "Vacant",
    }
    return mapping.get(key, raw if raw in {"SFH", "MDU", "Commercial", "Vacant"} else "Unknown")


def _a5_structure(a5: dict | None) -> str:
    data = a5 or {}
    detail = data.get("structure_type_detail")
    normalized_detail = _normalize_structure_type(detail)
    if normalized_detail != "Unknown":
        return normalized_detail
    return _normalize_structure_type(data.get("structure_type"))


def _synthesize(
    addr: Address,
    a1: Agent1Result | None,
    a2: dict | None,
    a3: dict | None,
    a4: dict | None,
    a5: dict | None,
) -> dict[str, Any]:
    """Combine all agent outputs into a final classification record."""

    # ── Confidence base from Agent 1 ────────────────────────────────────────
    a1_status = (a1.validation_status or "UNKNOWN") if a1 else "UNKNOWN"
    a1_score  = (a1.confidence_score or 0) if a1 else 0

    if a1_status == "REJECT":
        return {
            "status": "rejected",
            "final_structure_type": "Unknown",
            "final_is_mdu": None,
            "final_unit_count": None,
            "final_confidence": 0,
            "ftth_priority": "SKIP",
            "validation_summary": f"Address rejected by Agent 1 (score={a1_score}). "
                                  f"Reason: {(a1.exception_reason or '') if a1 else 'n/a'}",
            "a1_status": a1_status,
        }

    if _address_is_vacant_flagged(addr, a1):
        return {
            "status": "classified",
            "final_structure_type": "Vacant",
            "final_is_mdu": False,
            "final_unit_count": 0,
            "final_confidence": max(80, int(a1_score)),
            "ftth_priority": "SKIP",
            "validation_summary": (
                f"Agent1: {a1_status} (score={a1_score}). "
                "Structure: Vacant (address validation vacant flag)."
            ),
            "a1_status": a1_status,
            "a1_score": a1_score,
            "vote_pool": ["Vacant"],
            "a2_geocoded": bool((a2 or {}).get("status") == "geocoded"),
            "a2_location_type": (a2 or {}).get("location_type", ""),
        }

    # ── Collect structure-type votes from Agents 4 & 5 ──────────────────────
    a4_type = _normalize_structure_type((a4 or {}).get("structure_type"))
    a5_type = _a5_structure(a5)
    votes: list[str] = []
    if a4_type and a4_type != "Unknown":
        votes.append(a4_type)
    if a5_type and a5_type != "Unknown":
        votes.append(a5_type)

    # Land-use from Agent 3 as tiebreaker — do not override occupied A4/A5 evidence.
    a3_land = (a3 or {}).get("land_use", "")
    occupied_types = {t for t in (a4_type, a5_type) if t in {"SFH", "MDU", "Commercial"}}
    if "vacant" in a3_land.lower():
        if not occupied_types:
            votes.append("Vacant")
    elif "multi" in a3_land.lower() or "apartment" in a3_land.lower():
        votes.append("MDU")
    elif "single" in a3_land.lower() or "sfh" in a3_land.lower():
        votes.append("SFH")
    elif "commercial" in a3_land.lower():
        votes.append("Commercial")

    structure_type = _majority_vote(votes) if votes else "Unknown"

    # ── Unit count ───────────────────────────────────────────────────────────
    units_min = (a5 or {}).get("visible_units_min", 1) or 1
    units_max = (a5 or {}).get("visible_units_max", 1) or 1
    unit_count = 0 if structure_type == "Vacant" else max(units_min, units_max)

    is_mdu = structure_type == "MDU"

    # Refine MDU category
    if is_mdu:
        if unit_count >= 8:
            final_type = "MDU_LARGE"
        else:
            final_type = "MDU_SMALL"
    else:
        final_type = structure_type

    # ── Confidence ───────────────────────────────────────────────────────────
    a4_conf = (a4 or {}).get("confidence", 0) or 0
    a5_conf = (a5 or {}).get("confidence", 0) or 0
    a5_high_conf_house_number = bool((a5 or {}).get("high_conf_house_number"))
    if a5_high_conf_house_number:
        a5_conf = max(a5_conf, 90)
    imagery_conf = max(a4_conf, a5_conf)

    # Weight: Agent 1 40%, imagery 40%, vote agreement 20%.
    # Use a 0-100 agreement score so the 20% term contributes the intended weight.
    vote_agreement = 100 if len(set(votes)) == 1 and votes else (50 if votes else 0)
    final_confidence = min(100, int(
        0.40 * a1_score + 0.40 * imagery_conf + 0.20 * vote_agreement
    ))
    if a5_high_conf_house_number and final_type != "Unknown":
        final_confidence = max(final_confidence, 90)

    # ── FTTH priority ─────────────────────────────────────────────────────────
    if final_type == "Vacant":
        priority = "SKIP"
    elif final_type == "Commercial":
        priority = "LOW"
    elif final_type == "MDU_LARGE":
        priority = "HIGH"
    elif final_type in ("MDU_SMALL", "MDU"):
        priority = "MEDIUM"
    elif final_type == "SFH":
        priority = "MEDIUM" if a1_status == "AUTO_ACCEPT" else "LOW"
    else:
        priority = "LOW"

    # ── Summary text ─────────────────────────────────────────────────────────
    imagery_source = (a5 or {}).get("imagery_source") or (a4 or {}).get("imagery_source") or "none"
    summary_parts = [
        f"Agent1: {a1_status} (score={a1_score})",
        f"Structure: {final_type}",
    ]
    if imagery_source != "none":
        summary_parts.append(f"Imagery: {imagery_source}")
    if a3_land:
        summary_parts.append(f"Land use: {a3_land}")
    if is_mdu:
        summary_parts.append(f"Est. units: {unit_count}")
    if a5_high_conf_house_number:
        summary_parts.append("A5 OCR: house number matched")

    return {
        "status": "classified",
        "final_structure_type": final_type,
        "final_is_mdu": is_mdu,
        "final_unit_count": unit_count if (is_mdu or final_type == "Vacant") else 1,
        "final_confidence": final_confidence,
        "ftth_priority": priority,
        "validation_summary": " | ".join(summary_parts),
        "a1_status": a1_status,
        "a1_score": a1_score,
        "a2_geocoded": (a2 or {}).get("status") == "geocoded",
        "a2_location_type": (a2 or {}).get("location_type", ""),
        "a3_land_use": a3_land,
        "a4_structure": a4_type,
        "a4_confidence": a4_conf,
        "a5_structure": a5_type,
        "a5_structure_detail": (a5 or {}).get("structure_type_detail", ""),
        "a5_confidence": a5_conf,
        "a5_high_conf_house_number": a5_high_conf_house_number,
        "a5_house_number_conf": (a5 or {}).get("house_number_conf", 0),
        "a5_ocr_score": (a5 or {}).get("ocr_score", 0),
        "a5_paddle_ocr_used": bool((a5 or {}).get("paddle_ocr_used")),
        "a5_paddle_ocr_match_found": bool((a5 or {}).get("paddle_ocr_match_found")),
        "a5_paddle_ocr_text": (a5 or {}).get("paddle_ocr_text", ""),
        "a5_tesseract_ocr_used": bool((a5 or {}).get("tesseract_ocr_used")),
        "a5_tesseract_ocr_match_found": bool((a5 or {}).get("tesseract_ocr_match_found")),
        "a5_tesseract_ocr_text": (a5 or {}).get("tesseract_ocr_text", ""),
        "a5_confidence_breakdown": (a5 or {}).get("confidence_breakdown", {}),
        "imagery_source": imagery_source,
        "vote_pool": votes,
    }


def run_agent6_for_job(
    job_id: str,
    address_ids: list[int] | None = None,
    progress_callback=None,
    agent_options: dict[str, bool] | None = None,
) -> dict:
    """Run final FTTH classification for all addresses in the job."""
    from data_ingestion.utils.pipeline_options import DEFAULT_AGENT6_OPTIONS

    log_path = configure_agent_logger(logger, _AGENT_NAME)
    opts = {**DEFAULT_AGENT6_OPTIONS, **(agent_options or {})}
    logger.info("=" * 72)
    logger.info(
        "Agent6 START: job_id=%r address_filter=%s options=%s log=%s",
        job_id, address_ids or "all", opts, log_path,
    )
    if not opts.get("ftth_synthesis", True):
        logger.info("Agent6 SKIP: ftth_synthesis disabled")
        return {"total": 0, "skipped": True, "reason": "ftth_synthesis disabled"}
    session = get_session_factory()()
    try:
        _ensure_table(session)

        from sqlalchemy import select as _s
        stmt = _s(Address).where(
            Address.job_id == job_id,
            _txt(
                "COALESCE(raw_metadata->>'map_layer_only','false') != 'true'"
                " AND COALESCE(raw_metadata->>'geometry_type','') NOT IN ('Polygon','LineString')"
            ),
        ).order_by(Address.id)
        if address_ids:
            stmt = stmt.where(Address.id.in_(address_ids))
        addresses = session.scalars(stmt).all()

        addr_ids = [a.id for a in addresses]

        # Load all previous agent results in bulk
        a1_map: dict[int, Agent1Result] = {}
        agent_map: dict[str, dict[int, dict]] = {_A2: {}, _A3: {}, _A4: {}, _A5: {}}

        if addr_ids:
            a1_map = {r.address_id: r for r in session.scalars(
                _s(Agent1Result).where(Agent1Result.address_id.in_(addr_ids))
            ).all()}
            for agent_name in agent_map:
                for r in session.scalars(
                    _s(AgentResult).where(
                        AgentResult.agent_name == agent_name,
                        AgentResult.address_id.in_(addr_ids),
                    )
                ).all():
                    agent_map[agent_name][r.address_id] = r.data or {}

        total = len(addresses)
        summary = {"total": total, "high": 0, "medium": 0, "low": 0, "skip": 0, "failed": 0}
        logger.info(
            "Agent6 INPUT: addresses=%d agent1=%d agent2=%d agent3=%d agent4=%d agent5=%d",
            total,
            len(a1_map),
            len(agent_map[_A2]),
            len(agent_map[_A3]),
            len(agent_map[_A4]),
            len(agent_map[_A5]),
        )

        for idx, addr in enumerate(addresses, 1):
            try:
                inputs = {
                    "address_id": addr.id,
                    "raw_address": addr.raw_address,
                    "agent1": {
                        "validation_status": getattr(a1_map.get(addr.id), "validation_status", None),
                        "confidence_score": getattr(a1_map.get(addr.id), "confidence_score", None),
                        "chosen_provider": getattr(a1_map.get(addr.id), "chosen_provider", None),
                        "chosen_standardized_address": getattr(a1_map.get(addr.id), "chosen_standardized_address", None),
                    } if a1_map.get(addr.id) else None,
                    "agent2": agent_map[_A2].get(addr.id),
                    "agent3": agent_map[_A3].get(addr.id),
                    "agent4": agent_map[_A4].get(addr.id),
                    "agent5": agent_map[_A5].get(addr.id),
                }
                log_payload(logger, f"Agent6 INPUT address_id={addr.id}", inputs)
                result = _synthesize(
                    addr=addr,
                    a1=a1_map.get(addr.id),
                    a2=agent_map[_A2].get(addr.id),
                    a3=agent_map[_A3].get(addr.id),
                    a4=agent_map[_A4].get(addr.id),
                    a5=agent_map[_A5].get(addr.id),
                )
                _upsert(session, job_id, addr.id, result)
                log_payload(logger, f"Agent6 OUTPUT address_id={addr.id}", result)
                priority = result.get("ftth_priority", "LOW")
                if priority == "HIGH":
                    summary["high"] += 1
                elif priority == "MEDIUM":
                    summary["medium"] += 1
                elif priority == "SKIP":
                    summary["skip"] += 1
                else:
                    summary["low"] += 1
            except Exception as exc:
                logger.warning("Agent6 error on address_id=%s: %s", addr.id, exc, exc_info=True)
                summary["failed"] += 1
                _upsert(session, job_id, addr.id, {
                    "status": "error", "error": str(exc)[:200],
                    "final_structure_type": "Unknown", "ftth_priority": "LOW",
                    "final_confidence": 0,
                })

            if progress_callback:
                progress_callback(idx, total)

        session.commit()
        logger.info("Agent6 complete: %s", summary)
        logger.info("=" * 72)
        return summary
    except Exception as exc:
        session.rollback()
        logger.error("Agent6 failed for job %s: %s", job_id, exc)
        raise
    finally:
        session.close()

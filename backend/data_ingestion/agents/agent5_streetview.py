"""
Agent 5 — Street View + Azure Vision Detailed Analysis
========================================================
Uses the reference google_street_view_analysis pipeline:
  - Multi-variant Street View imagery + satellite/ESRI fallback
  - Azure Vision OCR house-number validation (90% threshold)
  - Hybrid confidence scoring with a floor of 90 on high-confidence OCR match

Results stored in agent_results with agent_name="agent5_streetview".
"""
from __future__ import annotations
from data_ingestion.config.paths import PROJECT_ROOT

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any

from sqlalchemy import select as _sel, text as _txt

from data_ingestion.config.settings import get_settings
from data_ingestion.database.db import get_session_factory
from data_ingestion.database.models import Address, Agent1Result, AgentResult, AgentTable
from data_ingestion.utils.agent5_house_numbers_output import (
    build_house_number_entry,
    default_output_paths,
    write_house_numbers_json,
)
from data_ingestion.utils.agent5_input import build_agent5_record, resolve_agent5_coordinates
from data_ingestion.utils.agent5_logging import agent5_log_file, configure_agent5_file_logging
from data_ingestion.utils.agent5_metadata import sync_agent5_streetview_in_raw_metadata
from data_ingestion.utils.agent5_paddle_ocr import paddleocr_runtime_status
from data_ingestion.utils.agent5_vision import analyze_address, bearing, streetview_metadata
from data_ingestion.utils.pipeline_options import DEFAULT_AGENT5_OPTIONS

logger = logging.getLogger(__name__)

_PROJECT_ROOT = PROJECT_ROOT
_env_file = _PROJECT_ROOT / ".env"
if _env_file.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(_env_file, override=True)
    except ImportError:
        pass

_AGENT_NAME = "agent5_streetview"
_DISPLAY_NAME = "Agent 5: Street View Analysis"

# Re-export for unit tests
_bearing = bearing
_sv_meta = streetview_metadata


def _ensure_table(session) -> None:
    if not session.execute(_sel(AgentTable).where(AgentTable.agent_name == _AGENT_NAME)).scalar_one_or_none():
        session.add(AgentTable(
            agent_name=_AGENT_NAME,
            display_name=_DISPLAY_NAME,
            owner="system",
            description="Detailed property analysis via Street View imagery + Azure AI Vision",
            color_rules=[
                {"field": "structure_type", "value": "SFH",        "color": "#16a34a", "label": "SFH"},
                {"field": "structure_type", "value": "MDU",        "color": "#2563eb", "label": "MDU"},
                {"field": "structure_type", "value": "Commercial", "color": "#d97706", "label": "Commercial"},
                {"field": "confidence", "value": "90", "color": "#059669", "label": "High Confidence (90+)"},
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


def _resolve_coords(addr: Address, a1: Agent1Result | None) -> tuple[float | None, float | None]:
    lat, lon, _source = resolve_agent5_coordinates(addr, a1)
    return lat, lon


def _skipped_result() -> dict[str, Any]:
    return {
        "status": "skipped",
        "reason": "no coordinates",
        "structure_type": "Unknown",
        "structure_type_detail": "unclear",
        "confidence": 0,
        "imagery_source": "none",
    }


def _error_result(exc: Exception) -> dict[str, Any]:
    return {
        "status": "error",
        "error": str(exc)[:200],
        "structure_type": "Unknown",
        "structure_type_detail": "unclear",
        "confidence": 0,
        "imagery_source": "error",
    }


def _collect_house_number_entry(
    entries: list[dict[str, Any]],
    result: dict[str, Any],
    record: dict[str, Any] | None,
) -> None:
    entry = build_house_number_entry(result, record)
    if entry is not None:
        entries.append(entry)


def _update_summary(summary: dict[str, Any], result: dict[str, Any]) -> None:
    status = result.get("status", "")
    imagery_source = result.get("imagery_source", "none")
    if status in {"analyzed", "ACCEPT", "REVIEW"}:
        summary["analyzed"] += 1
        if imagery_source == "streetview":
            summary["streetview"] += 1
        elif imagery_source == "satellite":
            summary["satellite"] += 1
        if (result.get("confidence") or 0) >= 90:
            summary["high_confidence"] += 1
    elif status in {"no_imagery", "no_vision", "skipped"}:
        summary["no_imagery"] += 1


def _log_address_result(
    *,
    idx: int,
    total: int,
    address_id: int,
    record: dict[str, Any] | None,
    result: dict[str, Any],
) -> None:
    logger.info(
        "Agent5 [%d/%d] id=%s status=%s structure=%s confidence=%s imagery=%s "
        "house_number=%r ocr_match=%s winning_step=%s images=%s",
        idx,
        total,
        address_id,
        result.get("status"),
        result.get("structure_type"),
        result.get("confidence"),
        result.get("imagery_source"),
        (record or {}).get("house_number") or (record or {}).get("raw_address"),
        result.get("ocr_match_found"),
        result.get("winning_step"),
        result.get("images_fetched"),
    )
    if result.get("status") in {"error", "no_vision", "no_imagery"}:
        logger.info(
            "Agent5 [%d/%d] id=%s detail: %s",
            idx,
            total,
            address_id,
            result.get("reason") or result.get("error") or "",
        )


def run_agent5_for_job(
    job_id: str,
    address_ids: list[int] | None = None,
    progress_callback=None,
    agent_options: dict[str, bool] | None = None,
) -> dict:
    """Run Street View + Azure Vision analysis for all addresses in the job."""
    configure_agent5_file_logging()
    settings = get_settings()
    opts = {**DEFAULT_AGENT5_OPTIONS, **(agent_options or {})}
    fast_mode = bool(opts.get("fast_mode", settings.agent5_fast_mode))
    ocr_status = paddleocr_runtime_status()
    max_workers = max(1, min(settings.agent5_max_workers, 8))
    # PaddleOCR is CPU-heavy; parallel workers fight over the same model and slow down.
    if ocr_status.get("available"):
        max_workers = 1
    house_number_paths = default_output_paths()

    logger.info("=" * 72)
    logger.info(
        "Agent5 START: job_id=%r addresses=%s fast_mode=%s workers=%d options=%s "
        "ocr_backend=%s paddleocr_available=%s paddleocr_reason=%s house_numbers_json=%s log=%s",
        job_id,
        len(address_ids) if address_ids else "all",
        fast_mode,
        max_workers,
        opts,
        ocr_status.get("backend"),
        ocr_status.get("available"),
        ocr_status.get("reason", ""),
        ", ".join(str(p.resolve()) for p in house_number_paths),
        agent5_log_file().resolve(),
    )

    session = get_session_factory()()
    try:
        _ensure_table(session)

        stmt = _sel(Address).where(
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
        a1_map: dict[int, Agent1Result] = {}
        if addr_ids:
            a1_map = {r.address_id: r for r in session.scalars(
                _sel(Agent1Result).where(Agent1Result.address_id.in_(addr_ids))
            ).all()}

        total = len(addresses)
        summary = {
            "total": total,
            "analyzed": 0,
            "streetview": 0,
            "satellite": 0,
            "no_imagery": 0,
            "failed": 0,
            "high_confidence": 0,
            "fast_mode": fast_mode,
        }

        work_items: list[tuple[Address, Agent1Result | None, dict[str, Any] | None]] = []
        for addr in addresses:
            a1 = a1_map.get(addr.id)
            lat, lon = _resolve_coords(addr, a1)
            if lat is None or lon is None:
                work_items.append((addr, a1, _skipped_result()))
            else:
                work_items.append((
                    addr,
                    a1,
                    None,
                ))

        def _analyze_item(item: tuple[Address, Agent1Result | None, dict[str, Any] | None]):
            addr, a1, preset = item
            if preset is not None:
                return addr.id, addr, a1, preset, None
            try:
                lat, lon = _resolve_coords(addr, a1)
                record = build_agent5_record(addr, a1, lat=float(lat), lon=float(lon))  # type: ignore[arg-type]
                result = analyze_address(record, agent_options=opts, addr=addr)
                return addr.id, addr, a1, result, record
            except Exception as exc:
                logger.warning("Agent5 analyze error on address_id=%s: %s", addr.id, exc)
                return addr.id, addr, a1, _error_result(exc), None

        completed = 0
        house_number_entries: list[dict[str, Any]] = []
        use_parallel = fast_mode and total > 1 and max_workers > 1
        if use_parallel:
            # Detach all ORM objects from the session before handing them to
            # worker threads.  Every attribute was already loaded by the
            # preceding scalars() calls, so accessing them on detached objects
            # does not trigger lazy-load SQL.  This prevents
            # "concurrent operations are not permitted" / "session in prepared
            # state" errors that occur when threads try to lazy-load through a
            # session that the main thread is simultaneously using.
            for addr, a1, _preset in work_items:
                try:
                    session.expunge(addr)
                except Exception:
                    pass
                if a1 is not None:
                    try:
                        session.expunge(a1)
                    except Exception:
                        pass

            with ThreadPoolExecutor(max_workers=min(max_workers, total)) as pool:
                futures = [pool.submit(_analyze_item, item) for item in work_items]
                for future in as_completed(futures):
                    addr_id, addr, a1, result, record = future.result()
                    completed += 1
                    try:
                        _upsert(session, job_id, addr_id, result)
                        if record is not None:
                            # Re-attach the detached addr object so that
                            # flag_modified() and session.commit() work.
                            try:
                                session.add(addr)
                            except Exception:
                                pass
                            sync_agent5_streetview_in_raw_metadata(addr, record, result, a1=a1)
                        session.commit()
                        _update_summary(summary, result)
                        _collect_house_number_entry(house_number_entries, result, record)
                        write_house_numbers_json(house_number_entries, house_number_paths)
                        _log_address_result(
                            idx=completed,
                            total=total,
                            address_id=addr_id,
                            record=record,
                            result=result,
                        )
                    except Exception as exc:
                        session.rollback()
                        logger.warning("Agent5 persist error on address_id=%s: %s", addr_id, exc)
                        summary["failed"] += 1
                        _upsert(session, job_id, addr_id, _error_result(exc))
                        session.commit()
                    if progress_callback:
                        progress_callback(completed, total)
        else:
            for idx, item in enumerate(work_items, 1):
                addr, a1, preset = item
                try:
                    if preset is not None:
                        result = preset
                        record = None
                    else:
                        lat, lon = _resolve_coords(addr, a1)
                        record = build_agent5_record(addr, a1, lat=float(lat), lon=float(lon))  # type: ignore[arg-type]
                        result = analyze_address(record, agent_options=opts, addr=addr)

                    _upsert(session, job_id, addr.id, result)
                    if record is not None:
                        sync_agent5_streetview_in_raw_metadata(addr, record, result, a1=a1)
                    session.commit()
                    _update_summary(summary, result)
                    _collect_house_number_entry(house_number_entries, result, record)
                    write_house_numbers_json(house_number_entries, house_number_paths)
                    _log_address_result(
                        idx=idx,
                        total=total,
                        address_id=addr.id,
                        record=record,
                        result=result,
                    )
                except Exception as exc:
                    session.rollback()
                    logger.warning("Agent5 error on address_id=%s: %s", addr.id, exc)
                    summary["failed"] += 1
                    _upsert(session, job_id, addr.id, _error_result(exc))
                    session.commit()

                if progress_callback:
                    progress_callback(idx, total)

        write_house_numbers_json(house_number_entries, house_number_paths)
        logger.info("Agent5 complete: %s", summary)
        return summary
    except Exception as exc:
        session.rollback()
        logger.error("Agent5 failed for job %s: %s", job_id, exc)
        raise
    finally:
        session.close()

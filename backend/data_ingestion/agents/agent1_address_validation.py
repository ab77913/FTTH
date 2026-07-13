"""
Agent 1 — Address Validation (Smarty + Melissa)
================================================
Called from api_server.py when the user presses "Process" for a job.

Flow:
  1. Read addresses from the `addresses` table for the given job_id
  2. Build a RawAddressRecord for each row
  3. Canonicalize → check cache
       CACHE HIT  → restore result from cache, skip API calls
       CACHE MISS → run Smarty + Melissa → arbitrate → score → write cache
  4. Upsert results into `agent1_results`
  5. Back-fill lat/lon on the `addresses` row if it was missing
  6. Save cache once at the end of the run
"""
from __future__ import annotations
from data_ingestion.config.paths import ADDRESS_VALIDATION_VENDOR, PROJECT_ROOT
from data_ingestion.config.log_paths import agent_log_path as _default_agent_log_path

import json
import os
import sys
import logging
import threading
import math
import re
from datetime import datetime
from pathlib import Path

from data_ingestion.utils.address_match import extract_house_number as _leading_house_number

_PROJECT_ROOT = PROJECT_ROOT

# ── add agent's src/ to path so we can import its modules ──────────────────────
AGENT_SRC = ADDRESS_VALIDATION_VENDOR
if str(AGENT_SRC) not in sys.path:
    sys.path.insert(0, str(AGENT_SRC))

# Load the agent's .env so SMARTY_AUTH_ID / MELISSA_LICENSE_KEY are available.
# override=True ensures edits to reference/.env take effect (override=False kept stale keys
# in long-running api_server processes).
_agent_env = AGENT_SRC / ".env"
_root_env = _PROJECT_ROOT / ".env"


def _load_agent_env(*, override: bool = True) -> None:
    try:
        from dotenv import load_dotenv as _ld
    except ImportError:
        return
    if _agent_env.exists():
        _ld(_agent_env, override=False)
    if _root_env.exists():
        _ld(_root_env, override=override)


_load_agent_env(override=True)

from src.models.schemas import ProviderResult, RawAddressRecord          # noqa: E402
from src.core.address_parser import canonicalize         # noqa: E402
from src.providers.smarty_adapter import SmartyProvider  # noqa: E402
from src.providers.melissa_adapter import MelissaProvider  # noqa: E402
from src.core.compare import compare_results             # noqa: E402
from src.core.scoring import score_provider_result, structure_hint, validation_status  # noqa: E402
from src.core.provider_arbitration import (  # noqa: E402
    provider_arbitration,
    provider_confidence_audit,
)
from src.providers.cache_lookup import AddressCache, generate_cache_key  # noqa: E402

_CACHE_FILE = AGENT_SRC / "cache" / "cache.json"

from sqlalchemy.orm.attributes import flag_modified

from data_ingestion.database.db import get_session_factory
from data_ingestion.database.models import Address, Agent1Result, AgentResult, AgentTable
from data_ingestion.utils.agent1_input import (
    build_melissa_payload,
    build_smarty_payload,
    resolve_agent1_input,
)
from data_ingestion.utils.ai_metadata import persist_ai_metadata
from data_ingestion.utils.address_metadata import persist_agent1_address_validator_in_raw_metadata

_AGENT_NAME    = "agent1_address_validator"
_DISPLAY_NAME  = "Agent 2: Address Validation (Smarty + Melissa)"


def _ensure_agent1_table(session) -> None:
    """Self-register Agent 1 in the agent_tables registry (idempotent)."""
    from sqlalchemy import select as _sel
    if not session.execute(_sel(AgentTable).where(AgentTable.agent_name == _AGENT_NAME)).scalar_one_or_none():
        session.add(AgentTable(
            agent_name=_AGENT_NAME,
            display_name=_DISPLAY_NAME,
            owner="system",
            description="Address validation via Smarty Streets + Melissa Data.",
            color_rules=[
                {"field": "validation_status", "value": "AUTO_ACCEPT",   "color": "#16a34a", "label": "Auto Accept"},
                {"field": "validation_status", "value": "MANUAL_REVIEW", "color": "#d97706", "label": "Manual Review"},
                {"field": "validation_status", "value": "REJECT",        "color": "#dc2626", "label": "Reject"},
            ],
        ))
        session.commit()


def _upsert_agent_results(session, job_id: str, address_id: int, data: dict) -> None:
    """Write Agent 1 output to the shared agent_results table (mirrors Agents 2-6 pattern)."""
    from sqlalchemy.dialects.postgresql import insert as _pg_insert
    from datetime import datetime
    now = datetime.utcnow()
    stmt = _pg_insert(AgentResult).values(
        agent_name=_AGENT_NAME,
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

logger = logging.getLogger(__name__)

_LOG_FILE = Path(
    os.environ.get(
        "FTTH_AGENT1_LOG_FILE",
        str(_default_agent_log_path("agent1_address_validator")),
    )
)
_LOG_CONFIGURED = False
_LOG_CONFIGURED_PID: int = -1
_LOG_LOCK = threading.Lock()


def _logging_enabled() -> bool:
    return os.environ.get("FTTH_AGENT1_LOG", "1").lower() not in ("0", "false", "no", "off")


def _cache_enabled() -> bool:
    """
    When False, Agent1 always calls Smarty/Melissa (no cache.json lookup or writes).

    Set FTTH_AGENT1_SKIP_CACHE=0 to re-enable caching for production runs.
  Default is skip (disabled) so local testing is not affected by stale cache entries.
    """
    return os.environ.get("FTTH_AGENT1_SKIP_CACHE", "1").lower() not in (
        "1", "true", "yes", "on",
    )


def _provider_mode() -> str:
    """Agent 1 provider mode.

    smarty_only fetches the first canonical address strictly from Smarty.
    dual_provider keeps the older Smarty + Melissa arbitration path.
    """
    mode = os.environ.get("FTTH_AGENT1_PROVIDER_MODE", "smarty_only").strip().lower()
    return mode if mode in {"smarty_only", "dual_provider"} else "smarty_only"


def _configure_file_logging() -> None:
    """Attach a file handler once per process.

    Plain append logging avoids Windows file-lock rollover errors when API and
    Celery processes write to the same agent log.
    """
    global _LOG_CONFIGURED, _LOG_CONFIGURED_PID
    if not _logging_enabled():
        return
    current_pid = os.getpid()
    with _LOG_LOCK:
        if _LOG_CONFIGURED and _LOG_CONFIGURED_PID == current_pid:
            return

        _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        logger.setLevel(logging.DEBUG)
        logger.propagate = False

        fmt = logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler = logging.FileHandler(
            _LOG_FILE,
            encoding="utf-8",
        )
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(fmt)

        # Avoid duplicate handlers when the logger is reconfigured in the same process.
        if not any(isinstance(h, logging.FileHandler) for h in logger.handlers):
            logger.addHandler(handler)

        _LOG_CONFIGURED = True
        _LOG_CONFIGURED_PID = current_pid


def _json_for_log(obj: object) -> str:
    try:
        return json.dumps(obj, default=str, ensure_ascii=False)
    except TypeError:
        return repr(obj)


def _status_from_score(score: int | float | None) -> str:
    """Map confidence to the stricter Agent 1 status bands."""
    score = int(score or 0)
    if score >= 90:
        return "AUTO_ACCEPT"
    if score >= 60:
        return "MANUAL_REVIEW"
    return "REJECT"


def _first_house_number(text: str | None) -> str | None:
    if not text:
        return None
    leading = _leading_house_number(str(text))
    if leading:
        return leading
    match = re.search(r"\b\d+[A-Z]?\b", str(text).upper())
    return match.group(0) if match else None


def _distance_m(lat1, lon1, lat2, lon2) -> float | None:
    try:
        lat1, lon1, lat2, lon2 = map(float, (lat1, lon1, lat2, lon2))
    except (TypeError, ValueError):
        return None
    if not (-90 <= lat1 <= 90 and -90 <= lat2 <= 90 and -180 <= lon1 <= 180 and -180 <= lon2 <= 180):
        return None
    radius_m = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return radius_m * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _has_deliverability_signal(result) -> bool:
    if not result or not getattr(result, "success", False):
        return False
    dpv = str(getattr(result, "dpv_match", "") or "").upper()
    if dpv in {"Y", "S", "D"}:
        return True
    raw = getattr(result, "raw_response", None) or {}
    codes = str(raw.get("Results") or raw.get("results") or raw.get("result_codes") or "").upper()
    return any(code in codes for code in ("AS01", "AS02", "AV24", "AV25"))


def _apply_quality_gates(*, status: str, score: int, chosen, comparison, addr: Address,
                         canonical, exc_reason: str | None) -> tuple[str, int, str | None]:
    """Conservative post-arbitration gates to keep AUTO_ACCEPT high precision."""
    reasons: list[str] = []
    chosen_success = bool(chosen and getattr(chosen, "success", False))
    conflict = str(getattr(comparison, "conflict_level", "") or "").upper()

    if not chosen_success:
        if addr.latitude and addr.longitude:
            reasons.append("provider validation failed; coordinates available")
            return "MANUAL_REVIEW", max(score, 35), _join_reasons(exc_reason, reasons)
        reasons.append("provider validation failed")
        return "REJECT", min(score, 30), _join_reasons(exc_reason, reasons)

    if status == "AUTO_ACCEPT":
        if not _has_deliverability_signal(chosen):
            reasons.append("missing strong deliverability signal")
            status = "MANUAL_REVIEW"
            score = min(score, 89)

        input_num = _first_house_number(getattr(canonical, "raw_address", None))
        chosen_num = _first_house_number(getattr(chosen, "standardized_address", None))
        if input_num and chosen_num and input_num != chosen_num:
            reasons.append(f"house number mismatch input={input_num} provider={chosen_num}")
            status = "MANUAL_REVIEW"
            score = min(score, 79)

        if conflict in {"MAJOR_CONFLICT", "SMARTY_ONLY", "MELISSA_ONLY"}:
            reasons.append(f"provider conflict={conflict}")
            status = "MANUAL_REVIEW"
            score = min(score, 89)

        dist = _distance_m(
            getattr(addr, "latitude", None),
            getattr(addr, "longitude", None),
            getattr(chosen, "latitude", None),
            getattr(chosen, "longitude", None),
        )
        if dist is not None and dist > 150:
            reasons.append(f"provider geocode is {dist:.0f}m from supplied coordinates")
            status = "MANUAL_REVIEW"
            score = min(score, 79)

    if status == "REJECT" and score >= 60:
        status = "MANUAL_REVIEW"

    return status, score, _join_reasons(exc_reason, reasons)


def _coord_address_hard_mismatch(addr: Address) -> str:
    meta = addr.raw_metadata if isinstance(addr.raw_metadata, dict) else {}
    av = meta.get("address_validation") if isinstance(meta.get("address_validation"), dict) else {}
    status = str(
        getattr(addr, "coord_address_match_status", None)
        or av.get("match_status")
        or ""
    ).strip().upper()
    return status if status in {"ADDRESS_MISMATCH", "MISMATCH"} else ""


def _apply_coord_mismatch_gate(
    *,
    status: str,
    score: int,
    exc_reason: str | None,
    addr: Address,
) -> tuple[str, int, str | None]:
    mismatch_status = _coord_address_hard_mismatch(addr)
    if not mismatch_status:
        return status, score, exc_reason
    reason = (
        f"coordinate/address validation returned {mismatch_status}; "
        "provider-standardized address was not accepted as final"
    )
    return "REJECT", min(score, 30), _join_reasons(exc_reason, [reason])


def _mask_credential(value: str | None) -> str:
    if not value:
        return "(missing)"
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}...{value[-4:]}"


def _redact_sensitive_text(value: str | None) -> str | None:
    if not value:
        return value
    text = str(value)
    text = re.sub(r"(?i)(auth-token=)[^&\s;]+", r"\1<redacted>", text)
    text = re.sub(r"(?i)(auth-id=)[^&\s;]+", r"\1<redacted>", text)
    text = re.sub(r"(?i)(id=)[^&\s;]+", r"\1<redacted>", text)
    return text


def _log_smarty_credentials() -> None:
    auth_id = os.getenv("SMARTY_AUTH_ID")
    logger.info(
        "Agent1 Smarty credentials: auth-id=%s env_file=%s master_env=%s",
        _mask_credential(auth_id),
        _agent_env,
        _root_env,
    )


def _join_reasons(existing: str | None, reasons: list[str]) -> str | None:
    parts = [p for p in [existing, *reasons] if p]
    return "; ".join(dict.fromkeys(parts)) if parts else None


def _log_provider_exchange(
    *,
    idx: int,
    total: int,
    address_id: int,
    provider: str,
    request: dict,
    result,
) -> None:
    """Log full provider request/response for validation auditing."""
    safe_request = dict(request)
    if provider == "melissa":
        safe_request = {**safe_request, "id": "***"}
    logger.info(
        "Agent1 [%d/%d] id=%s %s REQUEST: %s",
        idx, total, address_id, provider.upper(), _json_for_log(safe_request),
    )
    if result.success:
        response_body = result.raw_response or {}
    else:
        response_body = {
            "success": False,
            "error": _redact_sensitive_text(result.error),
            "raw_response": result.raw_response,
        }
    logger.info(
        "Agent1 [%d/%d] id=%s %s RESPONSE: %s",
        idx, total, address_id, provider.upper(), _json_for_log(response_body),
    )


def _row_to_raw(addr: Address) -> RawAddressRecord:
    """Convert an Address ORM row to a RawAddressRecord for the agent."""
    meta = addr.raw_metadata or {}
    fields = resolve_agent1_input(addr)
    return RawAddressRecord(
        source_file=addr.source_file or "db",
        source_type="db",
        row_id=str(addr.source_row_number or addr.id),
        address_id=str(addr.id),
        raw_address=fields.raw_address,
        city=fields.city,
        state=fields.state,
        zip_code=fields.zip_code,
        country=fields.country,
        latitude=fields.latitude,
        longitude=fields.longitude,
        network_node=addr.network_node or meta.get("network_node"),
        terminal_id=addr.terminal_id or meta.get("terminal_id"),
    )


def run_agent1_for_job(
    job_id: str,
    progress_callback=None,
    address_ids: list[int] | None = None,
    agent_options: dict[str, bool] | None = None,
) -> dict:
    """
    Run Agent 1 for addresses in `job_id`.
    If address_ids is provided, only those specific addresses are processed.
    progress_callback(done, total) is called after each record if provided.
    Returns summary dict.
    """
    from data_ingestion.utils.pipeline_options import agent1_provider_mode

    _configure_file_logging()
    _load_agent_env(override=True)
    _log_smarty_credentials()
    opts = agent_options or {}
    provider_mode = agent1_provider_mode(opts) if opts else _provider_mode()
    if provider_mode == "none":
        return {"total": 0, "processed": 0, "skipped": True, "reason": "no validation providers enabled"}

    session = get_session_factory()()
    smarty   = SmartyProvider()
    melissa  = MelissaProvider() if provider_mode == "dual_provider" else None

    use_cache = bool(opts.get("use_cache", _cache_enabled())) if opts else _cache_enabled()
    cache = AddressCache(cache_file=_CACHE_FILE).load() if use_cache else None
    cache_stats = cache.stats() if cache else {"total_entries": 0}
    _ensure_agent1_table(session)
    logger.info("=" * 72)
    if use_cache:
        logger.info(
            "Agent1 START: job_id=%r cache=ENABLED entries=%d cache_file=%s",
            job_id, cache_stats.get("total_entries", 0), _CACHE_FILE,
        )
    else:
        logger.info(
            "Agent1 START: job_id=%r cache=DISABLED (FTTH_AGENT1_SKIP_CACHE) — "
            "every address will call Smarty + Melissa",
            job_id,
        )
    if _logging_enabled():
        logger.info("Agent1 log file path: %s", _LOG_FILE.resolve())
    logger.info("Agent1 provider mode: %s", provider_mode)

    summary = {"total": 0, "auto_accept": 0, "manual_review": 0,
               "reject": 0, "errored": 0, "cache_hits": 0, "cache_misses": 0}
    try:
        from sqlalchemy import select as _sel, text as _txt
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

        total = len(addresses)
        summary["total"] = total
        logger.info("Agent1: %d addresses to process for job_id=%r", total, job_id)

        for idx, addr in enumerate(addresses, 1):
            try:
                raw_rec   = _row_to_raw(addr)
                resolved  = resolve_agent1_input(addr)
                canonical = canonicalize(raw_rec)

                smarty_req = build_smarty_payload(resolved, canonical)
                melissa_req = build_melissa_payload(resolved, canonical)

                logger.info(
                    "Agent1 [%d/%d] id=%s INPUT: raw=%r city=%r state=%r zip=%r country=%r "
                    "lat=%s lon=%s sources=%s",
                    idx, total, addr.id,
                    canonical.raw_address,
                    getattr(canonical, "city", None),
                    getattr(canonical, "state", None),
                    getattr(canonical, "zip_code", None),
                    resolved.country,
                    resolved.latitude,
                    resolved.longitude,
                    resolved.sources,
                )
                logger.info(
                    "Agent1 [%d/%d] id=%s PROVIDER_INPUT smarty=%s melissa=%s",
                    idx, total, addr.id,
                    _json_for_log(smarty_req),
                    _json_for_log(melissa_req),
                )

                cache_key = generate_cache_key(canonical)
                cached    = cache.get(cache_key) if use_cache else None

                # ── Upsert skeleton ───────────────────────────────────────────
                existing = session.scalar(
                    _sel(Agent1Result).where(Agent1Result.address_id == addr.id)
                )
                now = datetime.utcnow()
                if existing:
                    row = existing
                    row.updated_at = now
                else:
                    row = Agent1Result(job_id=job_id, address_id=addr.id,
                                      created_at=now, updated_at=now)
                    session.add(row)

                row.raw_address       = canonical.raw_address
                row.canonical_address = canonical.normalized_full_address

                smarty_result = None
                melissa_result = None
                conf_audit = None
                cache_snapshot = None

                if cached:
                    cache_snapshot = cached
                    # ── CACHE HIT — restore from cache, no API calls ──────────
                    logger.info(
                        "Agent1 [%d/%d] id=%s CACHE HIT: normalized=%r → status=%s score=%s",
                        idx, total, addr.id,
                        canonical.normalized_full_address,
                        cached.get("validation_status"),
                        cached.get("confidence_score", cached.get("score")),
                    )
                    summary["cache_hits"] += 1

                    score  = cached.get("confidence_score", cached.get("score", 0))
                    status = cached.get("validation_status") or _status_from_score(score)

                    row.smarty_standardized_address  = cached.get("smarty_standardized_address")
                    row.smarty_dpv                   = cached.get("smarty_dpv")
                    row.smarty_zip_plus_4            = cached.get("smarty_zip_plus_4")
                    row.smarty_vacant                = cached.get("smarty_vacant")
                    row.smarty_record_type           = cached.get("smarty_record_type")
                    row.smarty_lat                   = cached.get("smarty_lat")
                    row.smarty_lon                   = cached.get("smarty_lon")
                    row.melissa_standardized_address = cached.get("melissa_standardized_address")
                    row.melissa_dpv                  = cached.get("melissa_dpv")
                    row.melissa_zip_plus_4           = cached.get("melissa_zip_plus_4")
                    row.melissa_vacant               = cached.get("melissa_vacant")
                    row.melissa_record_type          = cached.get("melissa_record_type")
                    if provider_mode == "smarty_only" and cached.get("smarty_standardized_address"):
                        row.chosen_standardized_address = cached.get("smarty_standardized_address")
                        row.chosen_provider = "smarty"
                    else:
                        row.chosen_standardized_address  = (
                            cached.get("chosen_standardized_address")
                            or cached.get("standardized_address")
                        )
                        row.chosen_provider              = cached.get("chosen_provider", cached.get("provider", "cache"))
                    row.structure_hint               = cached.get("structure_hint", "CACHE_HIT")
                    row.confidence_score             = score
                    row.validation_status            = status
                    row.exception_reason             = cached.get("exception_reason")
                    row.comparison_reason            = "Loaded from cache"
                    row.data = {
                        "status": status,
                        "source": "cache",
                        "chosen_provider": row.chosen_provider,
                        "chosen_standardized_address": row.chosen_standardized_address,
                        "confidence_score": score,
                        "validation_status": status,
                        "structure_hint": row.structure_hint,
                        "exception_reason": row.exception_reason,
                        "comparison_reason": "Loaded from cache",
                        "normalized_full_address": cached.get("normalized_full_address"),
                        "cached_at": cached.get("cached_at"),
                        "smarty_standardized_address": row.smarty_standardized_address,
                        "smarty_dpv": row.smarty_dpv,
                        "smarty_zip_plus_4": row.smarty_zip_plus_4,
                        "smarty_vacant": row.smarty_vacant,
                        "smarty_record_type": row.smarty_record_type,
                        "smarty_lat": row.smarty_lat,
                        "smarty_lon": row.smarty_lon,
                        "smarty_geocode_precision": cached.get("smarty_geocode_precision"),
                        "melissa_standardized_address": row.melissa_standardized_address,
                        "melissa_dpv": row.melissa_dpv,
                        "melissa_zip_plus_4": row.melissa_zip_plus_4,
                        "melissa_vacant": row.melissa_vacant,
                        "melissa_record_type": row.melissa_record_type,
                        "melissa_lat": cached.get("melissa_lat"),
                        "melissa_lon": cached.get("melissa_lon"),
                        "raw_address": row.raw_address,
                        "canonical_address": row.canonical_address,
                    }
                    _upsert_agent_results(session, job_id, addr.id, row.data)

                    # ── API-failure safety net (cache-hit path) ──────────────
                    # Re-apply the REJECT→MANUAL_REVIEW upgrade for stale cache
                    # entries that were stored before this fix was deployed.
                    # Conditions: both providers had empty results AND the address
                    # has valid coordinates in the addresses table.
                    _c_smarty   = cached.get("smarty_standardized_address")
                    _c_melissa  = cached.get("melissa_standardized_address")
                    if (status == "REJECT"
                            and not _c_smarty and not _c_melissa
                            and (addr.latitude or addr.longitude)):
                        status = "MANUAL_REVIEW"
                        score  = max(score, 35)
                        row.validation_status = status
                        row.confidence_score  = score
                        row.exception_reason  = (
                            f"{cached.get('exception_reason') or 'Both providers failed'} "
                            "(auto-upgraded to MANUAL_REVIEW: valid coordinates present)"
                        )

                    if status == "AUTO_ACCEPT":
                        reasons = []
                        if not row.smarty_dpv and not row.melissa_dpv:
                            reasons.append("cached result missing deliverability signal")
                        dist = _distance_m(addr.latitude, addr.longitude, row.smarty_lat, row.smarty_lon)
                        if dist is not None and dist > 150:
                            reasons.append(f"cached provider geocode is {dist:.0f}m from supplied coordinates")
                        if reasons:
                            status = "MANUAL_REVIEW"
                            score = min(int(score or 0), 79)
                            row.validation_status = status
                            row.confidence_score = score
                            row.exception_reason = _join_reasons(row.exception_reason, reasons)

                    # ── Final Smarty house-number check (cached path) ─────────
                    # Binary rule applied after all other gates:
                    # raw-input house number == Smarty house number → 100 / AUTO_ACCEPT
                    # any mismatch or missing house number          →  35 / MANUAL_REVIEW
                    if row.smarty_standardized_address:
                        _raw_hn    = _first_house_number(canonical.raw_address)
                        _smarty_hn = _first_house_number(row.smarty_standardized_address)
                        if _raw_hn and _smarty_hn and _raw_hn == _smarty_hn:
                            score = 100
                            status = "AUTO_ACCEPT"
                            row.confidence_score  = score
                            row.validation_status = status
                            row.exception_reason  = None
                            logger.info(
                                "Agent1 [%d/%d] id=%s CACHE house-number MATCH raw=%r smarty=%r"
                                " → score=100 AUTO_ACCEPT",
                                idx, total, addr.id, _raw_hn, _smarty_hn,
                            )
                        else:
                            score = 35
                            status = "MANUAL_REVIEW"
                            row.confidence_score  = score
                            row.validation_status = status
                            logger.info(
                                "Agent1 [%d/%d] id=%s CACHE house-number MISMATCH raw=%r smarty=%r"
                                " → score=35 MANUAL_REVIEW",
                                idx, total, addr.id, _raw_hn, _smarty_hn,
                            )

                    smarty_lat = cached.get("smarty_lat")
                    smarty_lon = cached.get("smarty_lon")

                    # Rebuild provider objects so raw_metadata gets smarty/melissa sub-JSON
                    # even on cache hits (API-failure entries have null row columns).
                    _cache_exc = cached.get("exception_reason") or row.exception_reason
                    smarty_result = ProviderResult(
                        provider="smarty",
                        success=bool(row.smarty_standardized_address),
                        standardized_address=row.smarty_standardized_address,
                        dpv_match=row.smarty_dpv,
                        zip_plus_4=row.smarty_zip_plus_4,
                        vacant=row.smarty_vacant,
                        record_type=row.smarty_record_type,
                        latitude=row.smarty_lat,
                        longitude=row.smarty_lon,
                        geocode_precision=cached.get("smarty_geocode_precision"),
                        error=_cache_exc if not row.smarty_standardized_address else None,
                    )
                    melissa_result = ProviderResult(
                        provider="melissa",
                        success=bool(row.melissa_standardized_address),
                        standardized_address=row.melissa_standardized_address,
                        dpv_match=row.melissa_dpv,
                        zip_plus_4=row.melissa_zip_plus_4,
                        vacant=row.melissa_vacant,
                        record_type=row.melissa_record_type,
                        latitude=cached.get("melissa_lat"),
                        longitude=cached.get("melissa_lon"),
                        error=_cache_exc if not row.melissa_standardized_address else None,
                    )

                else:
                    # ── CACHE MISS — call APIs ────────────────────────────────
                    logger.info(
                        "Agent1 [%d/%d] id=%s CACHE MISS: normalized=%r — calling Smarty + Melissa",
                        idx, total, addr.id, canonical.normalized_full_address,
                    )
                    summary["cache_misses"] += 1

                    # ── Smarty API call ───────────────────────────────────────
                    smarty_result = smarty.validate(canonical, request=smarty_req)
                    _log_provider_exchange(
                        idx=idx, total=total, address_id=addr.id,
                        provider="smarty", request=smarty_req, result=smarty_result,
                    )
                    logger.info(
                        "Agent1 [%d/%d] id=%s SMARTY SUMMARY: success=%s standardized=%r "
                        "dpv=%r lat=%s lon=%s zip4=%r record_type=%r",
                        idx, total, addr.id,
                        smarty_result.success,
                        smarty_result.standardized_address,
                        smarty_result.dpv_match,
                        smarty_result.latitude,
                        smarty_result.longitude,
                        smarty_result.zip_plus_4,
                        smarty_result.record_type,
                    )

                    # ── Melissa API call ──────────────────────────────────────
                    if provider_mode == "dual_provider":
                        melissa_result = melissa.validate(canonical, request=melissa_req)
                        _log_provider_exchange(
                            idx=idx, total=total, address_id=addr.id,
                            provider="melissa", request=melissa_req, result=melissa_result,
                        )
                        logger.info(
                            "Agent1 [%d/%d] id=%s MELISSA SUMMARY: success=%s standardized=%r "
                            "dpv=%r lat=%s lon=%s zip4=%r record_type=%r",
                            idx, total, addr.id,
                            melissa_result.success,
                            melissa_result.standardized_address,
                            melissa_result.dpv_match,
                            melissa_result.latitude,
                            melissa_result.longitude,
                            melissa_result.zip_plus_4,
                            melissa_result.record_type,
                        )
                    else:
                        melissa_result = ProviderResult(
                            provider="melissa",
                            success=False,
                            error="Skipped because FTTH_AGENT1_PROVIDER_MODE=smarty_only",
                        )
                        logger.info(
                            "Agent1 [%d/%d] id=%s MELISSA SKIPPED: provider_mode=smarty_only",
                            idx, total, addr.id,
                        )

                    comparison = None
                    if smarty_result.success and melissa_result.success:
                        try:
                            comparison = compare_results(smarty_result, melissa_result)
                            logger.debug(
                                "Agent1 [%d/%d] id=%s COMPARISON: conflict_level=%s reason=%s",
                                idx, total, addr.id,
                                getattr(comparison, "conflict_level", "?"),
                                getattr(comparison, "reason", "?"),
                            )
                        except Exception:
                            comparison = None

                    conf_audit = provider_confidence_audit(smarty_result, melissa_result)
                    logger.info(
                        "Agent1 [%d/%d] id=%s CONFIDENCE smarty_score=%s details=%s",
                        idx, total, addr.id,
                        conf_audit["smarty"].get("score"),
                        conf_audit["smarty"].get("details"),
                    )
                    logger.info(
                        "Agent1 [%d/%d] id=%s CONFIDENCE melissa_score=%s details=%s codes=%s",
                        idx, total, addr.id,
                        conf_audit["melissa"].get("score"),
                        conf_audit["melissa"].get("details"),
                        conf_audit["melissa"].get("codes"),
                    )

                    if provider_mode == "smarty_only":
                        chosen = smarty_result
                        score = score_provider_result(smarty_result)
                    else:
                        chosen, score = provider_arbitration(smarty_result, melissa_result)
                    hint   = structure_hint(chosen, canonical.normalized_full_address)
                    if provider_mode == "smarty_only":
                        status = _status_from_score(score) if smarty_result.success else "REJECT"
                        exc_reason = None if status == "AUTO_ACCEPT" else (
                            smarty_result.error or "Smarty confidence below auto-accept threshold"
                        )
                    else:
                        status, exc_reason = validation_status(score, chosen, comparison)
                    status, score, exc_reason = _apply_quality_gates(
                        status=status,
                        score=score,
                        chosen=chosen,
                        comparison=comparison,
                        addr=addr,
                        canonical=canonical,
                        exc_reason=exc_reason,
                    )
                    exc_reason = _redact_sensitive_text(exc_reason)

                    logger.info(
                        "Agent1 [%d/%d] id=%s ARBITRATION: chosen_provider=%r score=%d "
                        "status=%s hint=%r exc=%r",
                        idx, total, addr.id,
                        chosen.provider if chosen else None,
                        score, status, hint, exc_reason,
                    )

                    # ── API-failure safety net ───────────────────────────────
                    # When both providers fail (e.g. expired Melissa licence or
                    # network error) but the address already has valid coords,
                    # downgrade REJECT → MANUAL_REVIEW so agents 2-6 can still
                    # classify the record using imagery / parcel data.
                    if (status == "REJECT"
                            and not smarty_result.success
                            and not melissa_result.success
                            and (addr.latitude or addr.longitude)):
                        status = "MANUAL_REVIEW"
                        score  = max(score, 35)
                        exc_reason = (
                            f"{exc_reason or 'Both providers failed'} "
                            "(auto-upgraded to MANUAL_REVIEW: valid coordinates present)"
                        )

                    # ── Final Smarty house-number check ──────────────────────
                    # Binary rule applied after all other gates:
                    # raw-input house number == Smarty house number → 100 / AUTO_ACCEPT
                    # any mismatch or missing house number          →  35 / MANUAL_REVIEW
                    if smarty_result.success:
                        _raw_hn    = _first_house_number(getattr(canonical, "raw_address", None))
                        _smarty_hn = _first_house_number(smarty_result.standardized_address)
                        if _raw_hn and _smarty_hn and _raw_hn == _smarty_hn:
                            score      = 100
                            status     = "AUTO_ACCEPT"
                            exc_reason = None
                            logger.info(
                                "Agent1 [%d/%d] id=%s house-number MATCH raw=%r smarty=%r"
                                " → score=100 AUTO_ACCEPT",
                                idx, total, addr.id, _raw_hn, _smarty_hn,
                            )
                        else:
                            score  = 35
                            status = "MANUAL_REVIEW"
                            logger.info(
                                "Agent1 [%d/%d] id=%s house-number MISMATCH raw=%r smarty=%r"
                                " → score=35 MANUAL_REVIEW",
                                idx, total, addr.id, _raw_hn, _smarty_hn,
                            )

                    status, score, exc_reason = _apply_coord_mismatch_gate(
                        status=status,
                        score=score,
                        exc_reason=exc_reason,
                        addr=addr,
                    )

                    comparison_reason = (
                        f"{comparison.conflict_level}: {comparison.reason}"
                        if comparison
                        else ("smarty_only" if smarty_result.success else
                              "melissa_only" if melissa_result.success else
                              "both_failed")
                    )

                    row.smarty_standardized_address  = smarty_result.standardized_address
                    row.smarty_dpv                   = smarty_result.dpv_match
                    row.smarty_zip_plus_4            = smarty_result.zip_plus_4
                    row.smarty_vacant                = smarty_result.vacant
                    row.smarty_record_type           = smarty_result.record_type
                    row.smarty_lat                   = smarty_result.latitude
                    row.smarty_lon                   = smarty_result.longitude
                    row.melissa_standardized_address = melissa_result.standardized_address
                    row.melissa_dpv                  = melissa_result.dpv_match
                    row.melissa_zip_plus_4           = melissa_result.zip_plus_4
                    row.melissa_vacant               = melissa_result.vacant
                    row.melissa_record_type          = melissa_result.record_type
                    row.chosen_standardized_address  = chosen.standardized_address
                    row.chosen_provider              = chosen.provider
                    row.structure_hint               = hint
                    row.confidence_score             = score
                    row.validation_status            = status
                    row.exception_reason             = exc_reason
                    row.comparison_reason            = comparison_reason
                    row.data = {
                        "status": status,
                        "source": chosen.provider if chosen else "unknown",
                        "chosen_provider": chosen.provider if chosen else None,
                        "chosen_standardized_address": chosen.standardized_address if chosen else None,
                        "confidence_score": score,
                        "validation_status": status,
                        "structure_hint": hint,
                        "exception_reason": exc_reason,
                        "comparison_reason": comparison_reason,
                        "smarty_standardized_address": smarty_result.standardized_address,
                        "smarty_dpv": smarty_result.dpv_match,
                        "smarty_zip_plus_4": smarty_result.zip_plus_4,
                        "smarty_vacant": smarty_result.vacant,
                        "smarty_record_type": smarty_result.record_type,
                        "smarty_lat": smarty_result.latitude,
                        "smarty_lon": smarty_result.longitude,
                        "melissa_standardized_address": melissa_result.standardized_address,
                        "melissa_dpv": melissa_result.dpv_match,
                        "melissa_zip_plus_4": melissa_result.zip_plus_4,
                        "melissa_vacant": melissa_result.vacant,
                        "melissa_record_type": melissa_result.record_type,
                        "raw_address": row.raw_address,
                        "canonical_address": row.canonical_address,
                    }
                    _upsert_agent_results(session, job_id, addr.id, row.data)

                    smarty_lat = smarty_result.latitude
                    smarty_lon = smarty_result.longitude

                    # ── Write full result to cache ────────────────────────────
                    if use_cache:
                        cache.set(cache_key, {
                            "normalized_full_address":       canonical.normalized_full_address,
                            "chosen_provider":               chosen.provider,
                            "chosen_standardized_address":   chosen.standardized_address,
                            "confidence_score":              score,
                            "validation_status":             status,
                            "structure_hint":                hint,
                            "exception_reason":              exc_reason,
                            "smarty_standardized_address":   smarty_result.standardized_address,
                            "smarty_dpv":                    smarty_result.dpv_match,
                            "smarty_zip_plus_4":             smarty_result.zip_plus_4,
                            "smarty_vacant":                 smarty_result.vacant,
                            "smarty_record_type":            smarty_result.record_type,
                            "smarty_lat":                    smarty_result.latitude,
                            "smarty_lon":                    smarty_result.longitude,
                            "smarty_geocode_precision":      smarty_result.geocode_precision,
                            "melissa_standardized_address":  melissa_result.standardized_address,
                            "melissa_dpv":                   melissa_result.dpv_match,
                            "melissa_zip_plus_4":            melissa_result.zip_plus_4,
                            "melissa_vacant":                melissa_result.vacant,
                            "melissa_record_type":           melissa_result.record_type,
                            "melissa_lat":                   melissa_result.latitude,
                            "melissa_lon":                   melissa_result.longitude,
                        })

                logger.info(
                    "Agent1 [%d/%d] id=%s FINAL: status=%s score=%d provider=%r "
                    "standardized=%r",
                    idx, total, addr.id,
                    row.validation_status,
                    row.confidence_score or 0,
                    row.chosen_provider,
                    row.chosen_standardized_address,
                )

                _meta = addr.raw_metadata if isinstance(addr.raw_metadata, dict) else {}
                _zip_full = row.smarty_zip_plus_4 or row.melissa_zip_plus_4 or addr.zip_code or ""
                _zip5 = str(_zip_full)[:5] if _zip_full else ""
                persist_agent1_address_validator_in_raw_metadata(
                    addr,
                    row,
                    smarty_result=smarty_result,
                    melissa_result=melissa_result,
                    conf_audit=conf_audit,
                    cached=cache_snapshot,
                )
                persist_ai_metadata(
                    addr,
                    agent_name="agent1_address_validator",
                    address=row.chosen_standardized_address or row.raw_address or "",
                    street=row.chosen_standardized_address or row.raw_address or "",
                    latitude=row.smarty_lat,
                    longitude=row.smarty_lon,
                    city=addr.city or "",
                    country=str(_meta.get("country_code") or _meta.get("country") or "US").upper()[:8],
                    zip=_zip5,
                    zip_code=str(_zip_full),
                    confidence=row.confidence_score,
                    remarks=row.exception_reason or row.comparison_reason or "",
                    ai_type=row.validation_status or "",
                )

                # Agent results live in agent1_results / validated_* — not upload columns.
                if smarty_lat:
                    meta = dict(addr.raw_metadata or {})
                    for k in list(meta.keys()):
                        kl = k.lower().replace(" ", "_")
                        if kl == "network_node_latitude":
                            meta[k] = smarty_lat
                        elif kl == "network_node_longitude":
                            meta[k] = smarty_lon
                    addr.raw_metadata = meta
                    flag_modified(addr, "raw_metadata")

                session.flush()

                if row.validation_status == "AUTO_ACCEPT":
                    summary["auto_accept"] += 1
                elif row.validation_status == "MANUAL_REVIEW":
                    summary["manual_review"] += 1
                else:
                    summary["reject"] += 1

            except Exception as e:
                logger.warning(
                    "Agent1 [%d/%d] id=%s ERROR: %s", idx, total, addr.id, e, exc_info=True
                )
                summary["errored"] += 1

            if progress_callback:
                progress_callback(idx, total)

        session.commit()

        if use_cache and cache is not None:
            cache.save()
            entries_now = cache.stats().get("total_entries", 0)
        else:
            entries_now = 0
        logger.info(
            "Agent1 COMPLETE: job_id=%r summary=%s cache_entries_now=%d",
            job_id, summary, entries_now,
        )
        logger.info("=" * 72)
        return summary

    except Exception as e:
        session.rollback()
        logger.error("Agent1 FAILED: job_id=%r error=%s", job_id, e, exc_info=True)
        raise
    finally:
        session.close()

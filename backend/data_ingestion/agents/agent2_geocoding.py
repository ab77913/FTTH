"""
Agent 2 — Unified Geocoding (Reverse + Forward)
================================================
Merges former Agent 0 (reverse geocoding) and Agent 2 (forward geocoding).

Pipeline phases (``run_geocoding_for_job``):
  1. Reverse geocode coord-only rows (Nominatim → Google) → ``agent1_results``
  2. Compare uploaded address vs coordinates for all rows
  3. Forward geocode using a tiered fallback strategy:
       Google Geocoding API → OSM Nominatim → street centerline interpolation
     Results stored in ``agent_results`` with agent_name="agent2_geocoding".

``run_agent2_for_job`` remains the forward-only entry point for tests and callers
that only need phase 3.

Logging:
  Forward geocoding: ``logs/agent2_geocoding.log`` (``FTTH_AGENT2_LOG_FILE``).
  Reverse geocoding: ``logs/reverse_geocoder.log`` (``FTTH_RGC_LOG_FILE``).
"""
from __future__ import annotations
from data_ingestion.config.paths import PROJECT_ROOT
from data_ingestion.config.log_paths import agent_log_path as _default_agent_log_path

import json
import logging
import math
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

from sqlalchemy import select as _sel
from sqlalchemy.orm.attributes import flag_modified

from data_ingestion.database.db import get_session_factory
from data_ingestion.database.models import Address, Agent1Result, AgentResult, AgentTable
from data_ingestion.utils.geocode_options import (
    any_forward_enabled,
    normalize_agent2_options,
)
from data_ingestion.utils.agent_db_input import (
    resolve_db_address_line,
    resolve_db_hint_coordinates,
)
from data_ingestion.utils.address_match import address_match_percent, finalize_geocoder_confidence, resolve_upload_address_line
from data_ingestion.utils.ai_metadata import persist_ai_metadata
from data_ingestion.utils.api_result_metadata import persist_provider_results
from data_ingestion.utils.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

_PROJECT_ROOT = PROJECT_ROOT
_LOG_FILE = Path(
    os.environ.get(
        "FTTH_AGENT2_LOG_FILE",
        str(_default_agent_log_path("agent2_geocoding")),
    )
)
_LOG_CONFIGURED = False
_LOG_CONFIGURED_PID: int = -1
_LOG_LOCK = threading.Lock()

try:
    from dotenv import load_dotenv
    load_dotenv(_PROJECT_ROOT / ".env", override=True)
except ImportError:
    pass

_GOOGLE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
_NOMINATIM_SEARCH_URL = "https://nominatim.openstreetmap.org/search"
_NOMINATIM_USER_AGENT = "FTTH-Agent2-Geocoding/1.0"
_AGENT_NAME = "agent2_geocoding"
_DISPLAY_NAME = "Agent 2: Geocoding (Reverse + Google/OSM/Interpolation)"

_HIGH_CONFIDENCE_THRESHOLD = float(os.environ.get("FTTH_AGENT2_HIGH_CONFIDENCE", "90"))
_GOOGLE_CONFIDENCE_THRESHOLD = float(os.environ.get("FTTH_AGENT2_GOOGLE_CONFIDENCE", "85"))
_OSM_CONFIDENCE_THRESHOLD = float(os.environ.get("FTTH_AGENT2_OSM_CONFIDENCE", "80"))
_ADDRESS_MATCH_REQUIRED = int(os.environ.get("FTTH_AGENT2_ADDRESS_MATCH_REQUIRED", "100"))
_LOW_MATCH_CONFIDENCE_CAP = float(os.environ.get("FTTH_AGENT2_LOW_MATCH_CONFIDENCE", "35"))
_OSM_VIEWBOX_DELTA = 0.5  # degrees (~55 km)
_NOMINATIM_MIN_INTERVAL_S = 1.05
_last_nominatim_call = 0.0
_nominatim_lock = threading.Lock()
_nominatim_rate_limiter: RateLimiter | None = None
_SSL_VERIFY_WARNED = False

_GOOGLE_LOCATION_CONFIDENCE = {
    "ROOFTOP": 95,
    "RANGE_INTERPOLATED": 75,
    "GEOMETRIC_CENTER": 60,
    "APPROXIMATE": 40,
}


def _env_int(name: str, default: int, *, min_value: int = 1, max_value: int = 64) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(min_value, min(max_value, value))


def _agent2_batch_size() -> int:
    return _env_int("FTTH_AGENT2_BATCH_SIZE", 25, min_value=1, max_value=500)


def _agent2_worker_count(stage: str, default: int = 4) -> int:
    specific = f"FTTH_AGENT2_{stage.upper()}_WORKERS"
    fallback = os.environ.get("FTTH_AGENT2_WORKERS")
    raw_default = int(fallback) if fallback and fallback.isdigit() else default
    return _env_int(specific, raw_default, min_value=1, max_value=32)


def _agent2_parallel_enabled(total: int, workers: int, batch_size: int) -> bool:
    if os.environ.get("FTTH_AGENT2_PARALLEL", "1").lower() in ("0", "false", "no", "off"):
        return False
    return workers > 1 and total > batch_size


def _chunks(values: list[int], size: int) -> list[list[int]]:
    return [values[i:i + size] for i in range(0, len(values), size)]


def _empty_forward_summary(total: int = 0) -> dict[str, int]:
    return {
        "total": total,
        "geocoded": 0,
        "failed": 0,
        "skipped": 0,
        "google": 0,
        "osm": 0,
        "interpolated": 0,
        "reverse_geocoded": 0,
    }


def _merge_count_summaries(base: dict[str, int], extra: dict[str, int]) -> dict[str, int]:
    for key, value in extra.items():
        if isinstance(value, int):
            base[key] = int(base.get(key, 0)) + value
    return base


def _get_nominatim_rate_limiter() -> RateLimiter:
    global _nominatim_rate_limiter
    if _nominatim_rate_limiter is None:
        _nominatim_rate_limiter = RateLimiter("nominatim", rate=1, period=1.05)
    return _nominatim_rate_limiter


def _logging_enabled() -> bool:
    return os.environ.get("FTTH_AGENT2_LOG", "1").lower() not in ("0", "false", "no", "off")


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
        if not any(isinstance(h, logging.FileHandler) for h in logger.handlers):
            logger.addHandler(handler)
        _LOG_CONFIGURED = True
        _LOG_CONFIGURED_PID = current_pid


def _log_info(msg: str, *args: object) -> None:
    _configure_file_logging()
    if _logging_enabled():
        logger.info(msg, *args)


def _log_debug(msg: str, *args: object) -> None:
    _configure_file_logging()
    if _logging_enabled():
        logger.debug(msg, *args)


def _log_warning(msg: str, *args: object) -> None:
    _configure_file_logging()
    if _logging_enabled():
        logger.warning(msg, *args)


def _log_error(msg: str, *args: object, exc_info: bool = False) -> None:
    _configure_file_logging()
    if _logging_enabled():
        logger.error(msg, *args, exc_info=exc_info)


def _verbose_api_logs() -> bool:
    return os.environ.get("FTTH_AGENT2_LOG_VERBOSE", "0").lower() in ("1", "true", "yes", "on")


def _fmt_coords(lat: float | None, lon: float | None) -> str:
    if not _coords_valid(lat, lon):
        return "none"
    return f"{lat:.6f}, {lon:.6f}"


def _fmt_distance(d_m: float | None) -> str:
    if d_m is None:
        return "n/a"
    if d_m < 1000:
        return f"{d_m:.1f} m from upload pin"
    return f"{d_m / 1000:.2f} km from upload pin"


def _provider_step_label(result: dict[str, Any], *, step_name: str) -> str:
    """One-line human summary for a fallback step."""
    if not result.get("ok"):
        return f"  [{step_name}] FAILED — no usable coordinates returned"
    conf = float(result.get("confidence") or 0)
    loc = result.get("location_type") or "n/a"
    dist = _fmt_distance(result.get("coord_distance_m"))
    addr = (result.get("formatted_address") or "")[:100]
    flags: list[str] = []
    if result.get("is_estimated"):
        flags.append("estimated")
    if result.get("pinned_to_source"):
        flags.append("pinned to upload pin")
    if result.get("fallback_used") and step_name == "GOOGLE":
        flags.append("fallback")
    flag_txt = f" ({', '.join(flags)})" if flags else ""
    return (
        f"  [{step_name}] OK — confidence={conf:.0f}, quality={loc}, "
        f"distance={dist}{flag_txt}\n"
        f"             address: {addr}"
    )


def _log_job_header(job_id: str, total: int) -> None:
    """Explain the log format once per job."""
    _log_info("=" * 72)
    _log_info("AGENT 2 — GEOCODING LOG")
    _log_info("Log file: %s", _LOG_FILE.resolve())
    _log_info("Job: %s | Addresses: %d", job_id, total)
    _log_info(
        "Fallback order: (1) Google  →  (2) OSM  →  (3) Street interpolation"
    )
    _log_info(
        "Stop early when confidence >= %.0f (Reverse), >= %.0f (Google) or >= %.0f (OSM)",
        _HIGH_CONFIDENCE_THRESHOLD,
        _GOOGLE_CONFIDENCE_THRESHOLD,
        _OSM_CONFIDENCE_THRESHOLD,
    )
    _log_info(
        "Confidence = API quality (ROOFTOP/APPROXIMATE) + bonus when result is near upload pin"
    )
    _log_info("=" * 72)


def _log_address_report(
    *,
    idx: int,
    total: int,
    address_id: int,
    input_address: str,
    input_source: str,
    hint_lat: float | None,
    hint_lon: float | None,
    steps: list[str],
    winner: dict[str, Any] | None,
    status: str,
) -> None:
    """Readable per-address summary block."""
    _log_info("-" * 72)
    _log_info("ADDRESS %d of %d | address_id=%s | status=%s", idx, total, address_id, status)
    _log_info("  Input address : %s", input_address[:200])
    _log_info("  Input source  : %s", input_source)
    _log_info("  Upload pin    : %s", _fmt_coords(hint_lat, hint_lon))
    _log_info("FALLBACK CHAIN:")
    for line in steps:
        _log_info(line)
    if winner and winner.get("ok"):
        _log_info(
            "SELECTED      : %s | confidence=%.0f | %s | lat/lon=%s",
            winner.get("source", "?"),
            float(winner.get("confidence") or 0),
            winner.get("location_type") or "n/a",
            _fmt_coords(winner.get("latitude"), winner.get("longitude")),
        )
        _log_info(
            "RESULT        : %s",
            (winner.get("formatted_address") or "")[:200],
        )
        decision = winner.get("_decision_reason") or ""
        if decision:
            _log_info("WHY           : %s", decision)
    elif status == "reverse_geocoded":
        _log_info("SELECTED      : REVERSE_GEOCODER (high confidence already achieved)")
    elif status == "skipped":
        _log_info("SELECTED      : none (no address text to geocode)")
    else:
        _log_info("SELECTED      : none — all providers failed")
    _log_info("-" * 72)


def _log_job_footer(summary: dict[str, int]) -> None:
    _log_info("=" * 72)
    _log_info("AGENT 2 JOB COMPLETE")
    _log_info("  Total processed : %d", summary.get("total", 0))
    _log_info("  Geocoded        : %d", summary.get("geocoded", 0))
    _log_info("    via Google    : %d", summary.get("google", 0))
    _log_info("    via OSM       : %d", summary.get("osm", 0))
    _log_info("    via Street    : %d  (interpolation, estimated)", summary.get("interpolated", 0))
    _log_info("    via Reverse   : %d  (high confidence)", summary.get("reverse_geocoded", 0))
    _log_info("  Skipped         : %d", summary.get("skipped", 0))
    _log_info("  Failed          : %d", summary.get("failed", 0))
    _log_info("=" * 72)


def _dotenv_values() -> dict[str, str]:
    env_file = _PROJECT_ROOT / ".env"
    if not env_file.exists():
        return {}
    try:
        from dotenv import dotenv_values
        return {
            str(key): str(value).strip()
            for key, value in dotenv_values(env_file).items()
            if value is not None and str(value).strip()
        }
    except Exception:
        _log_warning("Agent2 could not read .env values from %s", env_file)
        return {}


def _config_value(*names: str) -> str:
    values = _dotenv_values()
    for name in names:
        value = values.get(name)
        if value:
            return value
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def _get_api_key() -> str:
    return _config_value("GOOGLE_GEOCODING_API_KEY", "GOOGLE_MAPS_API_KEY", "GOOGLE_API_KEY")


def _coords_valid(lat: Any, lon: Any) -> bool:
    if lat is None or lon is None:
        return False
    if not isinstance(lat, (int, float, str)) or not isinstance(lon, (int, float, str)):
        return False
    try:
        lat_f = float(lat)
        lon_f = float(lon)
    except (TypeError, ValueError):
        return False
    if lat_f == 0.0 and lon_f == 0.0:
        return False
    return -90.0 <= lat_f <= 90.0 and -180.0 <= lon_f <= 180.0


def _resolve_input_address(addr: Address, a1: Agent1Result | None) -> tuple[str, str]:
    """Return (address_text, source_label) from database columns (+ Agent 1 when available)."""
    return resolve_db_address_line(addr, a1)


def _resolve_hint_coords(addr: Address, a1: Agent1Result | None) -> tuple[float | None, float | None]:
    """Best-effort coordinates from Address DB columns for Google bounds and OSM viewbox biasing."""
    return resolve_db_hint_coordinates(addr, a1)


def _ssl_verify() -> bool | str:
    """CA bundle for Nominatim HTTPS (certifi fixes many Windows SSL issues)."""
    if os.environ.get("FTTH_SSL_VERIFY", "1").lower() in ("0", "false", "no", "off"):
        return False
    custom = os.environ.get("FTTH_CA_BUNDLE", "").strip()
    if custom:
        p = Path(custom)
        if p.is_file():
            return str(p)
        _log_warning("FTTH_CA_BUNDLE path not found: %s", custom)
    try:
        import certifi
        return certifi.where()
    except ImportError:
        return True


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
    return 2 * r * math.asin(math.sqrt(max(0.0, min(1.0, a))))


def _forward_bounds(lat: float, lon: float, delta: float = 0.12) -> str:
    """Google Geocoding API bounds bias (~13 km at mid-latitudes)."""
    return f"{lat - delta},{lon - delta}|{lat + delta},{lon + delta}"


def _base_confidence_from_location_type(location_type: str, source: str) -> float:
    loc = (location_type or "").upper()
    if source == "INTERPOLATED":
        return 76.0
    if source == "OSM":
        return _GOOGLE_LOCATION_CONFIDENCE.get(loc, 82.0)
    return float(_GOOGLE_LOCATION_CONFIDENCE.get(loc, 50))


def _source_fallback_penalty(source: str, distance_m: float | None) -> float:
    """Light penalty for non-Google sources; smaller when coords agree with the pin."""
    strong_geo = distance_m is not None and distance_m <= 50
    if source == "OSM":
        return 1.0 if strong_geo else 2.0
    if source == "INTERPOLATED":
        return 3.0 if strong_geo else 5.0
    return 0.0


def _source_geo_bonus(result: dict[str, Any], distance_m: float | None) -> float:
    """Extra boost for OSM / interpolation when they align with pipeline coordinates."""
    source = str(result.get("source") or "")
    bonus = 0.0
    if source == "OSM":
        if result.get("pinned_to_source"):
            bonus += 10.0
        elif distance_m is not None and distance_m <= 100:
            bonus += 6.0
    elif source == "INTERPOLATED":
        if result.get("has_house_number"):
            bonus += 8.0
        if distance_m is not None and distance_m <= 200:
            bonus += 6.0
        elif distance_m is not None and distance_m <= 500:
            bonus += 3.0
    return bonus


def _estimated_confidence_cap(result: dict[str, Any], distance_m: float | None) -> float:
    """Allow strong interpolated matches to reach medium/high confidence."""
    if not result.get("is_estimated"):
        return 99.0
    if distance_m is not None and distance_m <= 50:
        return 92.0
    if distance_m is not None and distance_m <= 200:
        return 88.0
    return 82.0


def _house_number_str(text: str) -> str:
    m = re.match(r"^\s*(\d+[A-Za-z]?)(?:\s+|$)", (text or "").strip())
    return m.group(1).upper() if m else ""


def _rooftop_house_number_match(input_address: str, result: dict[str, Any]) -> bool:
    if str(result.get("location_type") or "").upper() != "ROOFTOP":
        return False
    input_hn = _house_number_str(input_address)
    if not input_hn:
        return False
    result_hn = str(
        result.get("house_number")
        or _house_number_str(result.get("formatted_address") or "")
        or _house_number_str(result.get("display_name") or "")
    ).upper()
    return result_hn == input_hn


def _compute_confidence(
    result: dict[str, Any],
    *,
    hint_lat: float | None,
    hint_lon: float | None,
    a1: Agent1Result | None,
    addr: Address | None,
    address_match_pct: int = 0,
) -> float:
    """
    Score geocode quality using location_type plus agreement with pipeline coords.
    Distance-based boosts apply only when the formatted address matches the input 100%.
    """
    source = str(result.get("source") or "GOOGLE")
    loc_type = str(result.get("location_type") or "")
    base = float(result.get("_base_confidence") or _base_confidence_from_location_type(loc_type, source))
    score = base

    result_lat = result.get("latitude")
    result_lon = result.get("longitude")
    distance_m: float | None = None
    strict_match = address_match_pct >= _ADDRESS_MATCH_REQUIRED
    if (
        strict_match
        and _coords_valid(hint_lat, hint_lon)
        and result_lat is not None
        and result_lon is not None
    ):
        distance_m = _haversine_m(float(hint_lat), float(hint_lon), float(result_lat), float(result_lon))
        if distance_m <= 20:
            score += 15.0
        elif distance_m <= 50:
            score += 12.0
        elif distance_m <= 100:
            score += 8.0
        elif distance_m <= 200:
            score += 4.0
        elif distance_m > 500:
            score -= 12.0
    elif (
        _coords_valid(hint_lat, hint_lon)
        and result_lat is not None
        and result_lon is not None
    ):
        distance_m = _haversine_m(float(hint_lat), float(hint_lon), float(result_lat), float(result_lon))

    if not strict_match:
        score = min(score, _LOW_MATCH_CONFIDENCE_CAP)
    else:
        if addr:
            match_status = (addr.coord_address_match_status or "").strip().upper()
            if match_status == "MATCH":
                score += 8.0
            elif match_status == "MISMATCH_WARN":
                score -= 5.0
            elif match_status == "MISMATCH":
                score -= 15.0
            rgc = addr.reverse_geocode_confidence_score
            if rgc is not None and int(rgc) >= 80:
                score += 5.0

        if a1:
            if (a1.validation_status or "").upper() == "AUTO_ACCEPT":
                score += 6.0
            elif a1.confidence_score is not None and int(a1.confidence_score) >= 80:
                score += 4.0

        score += _source_geo_bonus(result, distance_m)
        score -= _source_fallback_penalty(source, distance_m)

    cap = _estimated_confidence_cap(result, distance_m)
    final = max(0.0, min(score, cap))
    result["coord_distance_m"] = distance_m
    _log_debug(
        "confidence source=%s loc_type=%r base=%.1f distance_m=%s final=%.1f",
        source, loc_type, base, distance_m, final,
    )
    return final


def _finalize_geocode_result(
    result: dict[str, Any],
    *,
    input_address: str,
    hint_lat: float | None,
    hint_lon: float | None,
    a1: Agent1Result | None,
    addr: Address | None,
) -> dict[str, Any]:
    if not result.get("ok"):
        return result

    coord_distance_m: float | None = None
    result_lat = result.get("latitude")
    result_lon = result.get("longitude")
    if (
        _coords_valid(hint_lat, hint_lon)
        and result_lat is not None
        and result_lon is not None
    ):
        coord_distance_m = _haversine_m(
            float(hint_lat), float(hint_lon), float(result_lat), float(result_lon),
        )

    match_pct = address_match_percent(
        input_address,
        result.get("formatted_address") or "",
        city=(addr.city if addr else None),
        state=(addr.state if addr else None),
        zip_code=(addr.zip_code if addr else None),
        coord_distance_m=coord_distance_m,
    )

    if _rooftop_house_number_match(input_address, result):
        match_pct = 100

    result["address_match_percent"] = match_pct
    accepted = match_pct >= _ADDRESS_MATCH_REQUIRED
    result["address_accepted"] = accepted

    result["confidence"] = _compute_confidence(
        result,
        hint_lat=hint_lat,
        hint_lon=hint_lon,
        a1=a1,
        addr=addr,
        address_match_pct=match_pct,
    )
    result["confidence"] = finalize_geocoder_confidence(
        result["confidence"],
        location_type=str(result.get("location_type") or ""),
        address_match_percent=match_pct,
    )
    if not accepted:
        result["confidence"] = min(float(result["confidence"]), _LOW_MATCH_CONFIDENCE_CAP)
        result["_reject_reason"] = (
            f"address match {match_pct}% (requires {_ADDRESS_MATCH_REQUIRED}%)"
        )
        _log_info(
            "Geocode rejected for fallback: source=%s match=%s%% formatted=%r",
            result.get("source"),
            match_pct,
            (result.get("formatted_address") or "")[:120],
        )

        # Secondary house-number check against the raw input address.
        # Even when full address text similarity is low, an exact house-number
        # match means the geocoder found the right building → boost to 100.
        input_hn = _house_number_str(input_address)
        result_hn = str(
            result.get("house_number")
            or _house_number_str(result.get("formatted_address") or "")
            or _house_number_str(result.get("display_name") or "")
        ).upper()
        if input_hn and result_hn and result_hn == input_hn:
            result["confidence"] = 100.0
            result["address_accepted"] = True
            result.pop("_reject_reason", None)
            _log_info(
                "House-number match overrides low address similarity → confidence 100: "
                "input_hn=%r result_hn=%r source=%s",
                input_hn,
                result_hn,
                result.get("source"),
            )

    return result


def _geocode_accepted(result: dict[str, Any]) -> bool:
    return bool(
        result.get("ok")
        and result.get("address_accepted")
        and float(result.get("confidence") or 0) > 0
    )


def _fm_road_query_aliases(address: str) -> list[str]:
    """Return safer query aliases for rural FM-road addresses."""
    text = str(address or "")
    aliases: list[str] = []
    patterns = (
        (r"\bFM\s+ROAD\s+(\d+)\b", r"FM \1"),
        (r"\bFM\s+RD\s+(\d+)\b", r"FM \1"),
        (r"\bFARM\s+TO\s+MARKET\s+ROAD\s+(\d+)\b", r"FM \1"),
        (r"\bFARM\s+TO\s+MARKET\s+RD\s+(\d+)\b", r"FM \1"),
        (r"\bFARM\s+TO\s+MARKET\s+(\d+)\b", r"FM \1"),
    )
    for pattern, replacement in patterns:
        alias = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
        if alias != text and alias not in aliases:
            aliases.append(alias)
    return aliases


def _imperial_tx_query_aliases(address: str) -> list[str]:
    """Return local aliases used by map providers around Imperial, TX."""
    text = str(address or "")
    normalized = " ".join(text.upper().replace(",", " ").split())
    if "IMPERIAL" not in normalized or "TX" not in normalized:
        return []

    aliases: list[str] = []
    patterns = (
        (r"\bCOOLIDGE\b", "COOLEDGE"),
        (r"\bSTATE\s+HIGHWAY\s+11\b", "FM 11"),
        (r"\bHIGHWAY\s+11\b", "FM 11"),
        (r"\bHWY\s+11\b", "FM 11"),
    )
    for pattern, replacement in patterns:
        alias = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
        if alias != text and alias not in aliases:
            aliases.append(alias)
    return aliases


def _geocode_query_aliases(address: str) -> list[str]:
    aliases: list[str] = []
    for alias in [*_fm_road_query_aliases(address), *_imperial_tx_query_aliases(address)]:
        if alias not in aliases:
            aliases.append(alias)
    return aliases


def _empty_result(source: str) -> dict[str, Any]:
    result = {
        "formatted_address": "",
        "latitude": None,
        "longitude": None,
        "location_type": "",
        "place_id": "",
        "confidence": 0.0,
        "source": source,
        "fallback_used": source != "GOOGLE",
        "is_estimated": source == "INTERPOLATED",
        "ok": False,
    }
    if source == "GOOGLE":
        result.update({
            "google_status": "",
            "google_error_message": "",
            "zero_results": False,
        })
    return result


def _forward_geocode_google(
    address: str,
    *,
    hint_lat: float | None = None,
    hint_lon: float | None = None,
    addr: Address | None = None,
) -> dict[str, Any]:
    _log_info(
        "_forward_geocode_google IN: address=%r hint=(%s,%s)",
        address[:200],
        hint_lat,
        hint_lon,
    )
    api_key = _get_api_key()
    if not api_key.strip():
        _log_warning("GOOGLE_GEOCODING_API_KEY not set — skipping Google forward geocode")
        return _empty_result("GOOGLE")

    request_payload: dict[str, str] = {"address": address}
    params: dict[str, str] = {**request_payload, "key": api_key}
    if _coords_valid(hint_lat, hint_lon):
        params["bounds"] = _forward_bounds(float(hint_lat), float(hint_lon))
        request_payload["bounds"] = params["bounds"]
    query = urllib.parse.urlencode(params)
    url = f"{_GOOGLE_URL}?{query}"
    _log_debug(
        "Google request URL (key redacted): %s",
        f"{_GOOGLE_URL}?{urllib.parse.urlencode({k: v for k, v in params.items() if k != 'key'})}&key=***",
    )
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode())
        raw_json = json.dumps(data, default=str)
        if _verbose_api_logs():
            _log_info("Google raw response: %s", raw_json)
        else:
            _log_debug("Google raw response: %s", raw_json)
        if data.get("status") != "OK" or not data.get("results"):
            _log_warning(
                "Google no usable result: status=%r error_message=%r",
                data.get("status"),
                data.get("error_message"),
            )
            empty = _empty_result("GOOGLE")
            empty["google_status"] = str(data.get("status") or "")
            empty["google_error_message"] = str(data.get("error_message") or "")
            empty["zero_results"] = data.get("status") == "ZERO_RESULTS"
            return empty
        r = data["results"][0]
        loc = r["geometry"]["location"]
        loc_type = (r.get("geometry", {}).get("location_type") or "").upper()
        parsed = {
            "formatted_address": r.get("formatted_address", ""),
            "latitude": loc["lat"],
            "longitude": loc["lng"],
            "location_type": loc_type,
            "place_id": r.get("place_id", ""),
            "_base_confidence": float(_GOOGLE_LOCATION_CONFIDENCE.get(loc_type, 50)),
            "source": "GOOGLE",
            "fallback_used": False,
            "is_estimated": False,
            "ok": True,
            "google_status": str(data.get("status") or ""),
            "google_error_message": str(data.get("error_message") or ""),
            "zero_results": False,
        }
        _log_info(
            "_forward_geocode_google OUT: formatted_address=%r lat=%r lon=%r "
            "location_type=%r base_confidence=%s",
            parsed["formatted_address"],
            parsed["latitude"],
            parsed["longitude"],
            parsed["location_type"],
            parsed["_base_confidence"],
        )
        return parsed
    except Exception as exc:
        _log_error(
            "Google geocode failed for address=%r: %s",
            address[:200],
            exc,
            exc_info=True,
        )
        return _empty_result("GOOGLE")


def _geocode(address: str) -> dict[str, Any] | None:
    """Legacy test helper: perform one Google-style geocode call and return the old shape."""
    params = urllib.parse.urlencode({"address": address, "key": _get_api_key() or "test"})
    req = urllib.request.Request(f"{_GOOGLE_URL}?{params}")
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode())
        if data.get("status") != "OK" or not data.get("results"):
            return None
        result = data["results"][0]
        geometry = result.get("geometry", {})
        loc = geometry.get("location", {})
        loc_type = (geometry.get("location_type") or "").upper()
        return {
            "formatted_address": result.get("formatted_address", ""),
            "latitude": loc.get("lat"),
            "longitude": loc.get("lng"),
            "location_type": loc_type,
            "place_id": result.get("place_id", ""),
            "confidence": int(_GOOGLE_LOCATION_CONFIDENCE.get(loc_type, 50)),
        }
    except Exception:
        return None


def _nominatim_throttle() -> None:
    """Respect Nominatim usage policy across threads/processes when Redis is available."""
    global _last_nominatim_call
    try:
        with _nominatim_lock:
            _get_nominatim_rate_limiter().acquire()
        return
    except Exception as exc:
        _log_warning("Redis-backed Nominatim limiter failed; using local throttle: %s", exc)
    with _nominatim_lock:
        now = time.monotonic()
        wait = _NOMINATIM_MIN_INTERVAL_S - (now - _last_nominatim_call)
        if wait > 0:
            time.sleep(wait)
        _last_nominatim_call = time.monotonic()


def _nominatim_search(
    params: dict[str, Any],
    *,
    addr: Address | None = None,
    provider: str = "osm",
) -> list[dict[str, Any]]:
    """Nominatim search with certifi + SSL fallback (Windows/corporate networks)."""
    global _SSL_VERIFY_WARNED
    _nominatim_throttle()
    headers = {"User-Agent": _NOMINATIM_USER_AGENT, "Accept-Language": "en"}
    verify = _ssl_verify()
    if verify is False and not _SSL_VERIFY_WARNED:
        _log_warning("FTTH_SSL_VERIFY=0 — Nominatim HTTPS certificate verification disabled")
        _SSL_VERIFY_WARNED = True
    try:
        resp = requests.get(
            _NOMINATIM_SEARCH_URL,
            params=params,
            headers=headers,
            timeout=12,
            verify=verify,
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []
    except requests.exceptions.SSLError as exc:
        allow_fallback = os.environ.get("FTTH_SSL_INSECURE_FALLBACK", "1").lower() not in (
            "0", "false", "no", "off",
        )
        if not allow_fallback or verify is False:
            raise
        _log_warning(
            "Nominatim SSL verify failed (%s) — retrying with verify=False",
            exc,
        )
        try:
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        except Exception:
            pass
        resp = requests.get(
            _NOMINATIM_SEARCH_URL,
            params=params,
            headers=headers,
            timeout=12,
            verify=False,
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []


def _osm_confidence_from_place_rank(place_rank: int) -> tuple[str, float]:
    if place_rank <= 10:
        return "ROOFTOP", 93.0
    if place_rank <= 20:
        return "RANGE_INTERPOLATED", 88.0
    if place_rank <= 25:
        return "GEOMETRIC_CENTER", 82.0
    return "APPROXIMATE", 72.0


def _parse_osm_result(
    results: list[dict[str, Any]],
    *,
    pin_lat: float | None = None,
    pin_lon: float | None = None,
) -> dict[str, Any]:
    if not results:
        return _empty_result("OSM")

    r = results[0]
    lat = float(r["lat"]) if r.get("lat") else None
    lon = float(r["lon"]) if r.get("lon") else None
    place_rank = int(r.get("place_rank") or 30)
    loc_type, confidence = _osm_confidence_from_place_rank(place_rank)

    if lat is None or lon is None:
        return _empty_result("OSM")

    return {
        "formatted_address": r.get("display_name", ""),
        "latitude": lat,
        "longitude": lon,
        "location_type": loc_type,
        "place_id": str(r.get("place_id", "")),
        "_base_confidence": confidence,
        "place_rank": place_rank,
        "pinned_to_source": False,
        "source": "OSM",
        "fallback_used": True,
        "is_estimated": False,
        "ok": True,
    }


def _geocode_osm(
    address: str,
    *,
    hint_lat: float | None = None,
    hint_lon: float | None = None,
    addr: Address | None = None,
) -> dict[str, Any]:
    _log_info(
        "_geocode_osm IN: address=%r hint_lat=%s hint_lon=%s",
        address[:200],
        hint_lat,
        hint_lon,
    )
    has_hint = _coords_valid(hint_lat, hint_lon)

    params: dict[str, Any] = {
        "q": address,
        "format": "json",
        "addressdetails": 1,
        "limit": 5,
        "countrycodes": "us",
    }
    if has_hint:
        d = _OSM_VIEWBOX_DELTA
        params["viewbox"] = f"{hint_lon - d},{hint_lat + d},{hint_lon + d},{hint_lat - d}"
        params["bounded"] = 1

    try:
        results = _nominatim_search(params, addr=addr)
        if not results and has_hint:
            _log_info("Bounded OSM search empty — retrying without viewbox")
            fallback_params = {k: v for k, v in params.items() if k not in ("viewbox", "bounded")}
            fallback_params["limit"] = 1
            results = _nominatim_search(fallback_params, addr=addr)

        pin_lat = float(hint_lat) if has_hint else None
        pin_lon = float(hint_lon) if has_hint else None
        parsed = _parse_osm_result(results[:1], pin_lat=pin_lat, pin_lon=pin_lon)
        _log_info(
            "_geocode_osm OUT: formatted_address=%r lat=%r lon=%r confidence=%s pinned=%s",
            parsed.get("formatted_address"),
            parsed.get("latitude"),
            parsed.get("longitude"),
            parsed.get("confidence"),
            has_hint,
        )
        return parsed
    except Exception as exc:
        _log_error("OSM geocode failed for address=%r: %s", address[:200], exc, exc_info=True)
        return _empty_result("OSM")


def _extract_house_number(address: str) -> int | None:
    m = re.match(r"^\s*(\d+)", (address or "").strip())
    return int(m.group(1)) if m else None


def _strip_house_number(address: str) -> str:
    return re.sub(r"^\s*\d+\s*", "", (address or "").strip())


def _interpolate_along_bbox(
    house_number: int,
    low_number: int,
    high_number: int,
    sw_lat: float,
    sw_lon: float,
    ne_lat: float,
    ne_lon: float,
) -> tuple[float, float]:
    if high_number == low_number:
        fraction = 0.5
    else:
        fraction = (house_number - low_number) / (high_number - low_number)
        fraction = max(0.0, min(1.0, fraction))
    lat = sw_lat + fraction * (ne_lat - sw_lat)
    lon = sw_lon + fraction * (ne_lon - sw_lon)
    return round(lat, 7), round(lon, 7)


def _interpolate_address(address: str, *, addr: Address | None = None) -> dict[str, Any]:
    _log_info("_interpolate_address IN: address=%r", address[:200])
    house_number = _extract_house_number(address)
    street_query = _strip_house_number(address)
    if not street_query:
        _log_warning("Interpolation skipped — no street name in address=%r", address[:200])
        return _empty_result("INTERPOLATED")

    try:
        results = _nominatim_search({
            "q": street_query,
            "format": "json",
            "addressdetails": 1,
            "limit": 1,
            "featuretype": "street",
        }, addr=addr, provider="osm_interpolation")
    except Exception as exc:
        _log_error("Interpolation street lookup failed: %s", exc, exc_info=True)
        return _empty_result("INTERPOLATED")

    if not results:
        _log_warning("Interpolation: no street centerline for %r", street_query)
        return _empty_result("INTERPOLATED")

    raw = results[0]
    bbox_raw = raw.get("boundingbox") or []
    lat: float | None = None
    lon: float | None = None

    if len(bbox_raw) == 4:
        sw_lat, ne_lat, sw_lon, ne_lon = (
            float(bbox_raw[0]),
            float(bbox_raw[1]),
            float(bbox_raw[2]),
            float(bbox_raw[3]),
        )
        if house_number is not None:
            lat, lon = _interpolate_along_bbox(house_number, 1, 9999, sw_lat, sw_lon, ne_lat, ne_lon)
        else:
            lat = round((sw_lat + ne_lat) / 2, 7)
            lon = round((sw_lon + ne_lon) / 2, 7)
    else:
        lat = float(raw["lat"]) if raw.get("lat") else None
        lon = float(raw["lon"]) if raw.get("lon") else None

    if lat is None or lon is None:
        return _empty_result("INTERPOLATED")

    base_confidence = 80.0 if house_number is not None else 74.0
    
    # base_address = raw.get("display_name", address)
    # if house_number is not None and str(house_number) not in base_address:
    #     formatted_address = f"{house_number} {base_address}"
    # else:
    #     formatted_address = base_address

    parsed = {
        # "formatted_address": formatted_address,
          "formatted_address": raw.get("display_name", address),
        "latitude": lat,
        "longitude": lon,
        "location_type": "RANGE_INTERPOLATED",
        "place_id": str(raw.get("place_id", "")),
        "_base_confidence": base_confidence,
        "has_house_number": house_number is not None,
        "source": "INTERPOLATED",
        "fallback_used": True,
        "is_estimated": True,
        "ok": True,
    }
    _log_info(
        "_interpolate_address OUT: formatted_address=%r lat=%r lon=%r base_confidence=%s",
        parsed["formatted_address"],
        lat,
        lon,
        parsed["_base_confidence"],
    )
    return parsed


def _geocode_with_fallback(
    address: str,
    *,
    hint_lat: float | None = None,
    hint_lon: float | None = None,
    a1: Agent1Result | None = None,
    addr: Address | None = None,
    geocode_options: dict[str, bool] | None = None,
) -> tuple[dict[str, Any] | None, list[str], dict[str, Any]]:
    """
    Forward geocode using enabled providers only (Google → OSM → street interpolation).
    Returns (best geocode dict or None, human-readable step lines for the log, executed models dict).
    """
    raw_options = geocode_options or {}
    opts = normalize_agent2_options(raw_options)
    steps: list[str] = []
    executed_models: dict[str, Any] = {}
    has_google_key = bool(_get_api_key().strip())

    google = _empty_result("GOOGLE")
    if opts["google_geocoding"]:
        google = (
            _finalize_geocode_result(
                _forward_geocode_google(address, hint_lat=hint_lat, hint_lon=hint_lon, addr=addr),
                input_address=address,
                hint_lat=hint_lat,
                hint_lon=hint_lon,
                a1=a1,
                addr=addr,
            )
            if has_google_key
            else _empty_result("GOOGLE")
        )
        if has_google_key and not _geocode_accepted(google):
            best_alias_candidate: dict[str, Any] | None = None
            for alias in _geocode_query_aliases(address):
                alias_google = _finalize_geocode_result(
                    _forward_geocode_google(alias, hint_lat=hint_lat, hint_lon=hint_lon, addr=addr),
                    input_address=alias,
                    hint_lat=hint_lat,
                    hint_lon=hint_lon,
                    a1=a1,
                    addr=addr,
                )
                if _geocode_accepted(alias_google):
                    alias_google["query_alias"] = alias
                    alias_google["_decision_reason"] = (
                        f"Google accepted query alias {alias!r} "
                        f"(match={alias_google.get('address_match_percent')}%)"
                    )
                    google = alias_google
                    steps.append(f"  [GOOGLE] accepted query alias: {alias}")
                    break
                if alias_google.get("ok"):
                    alias_google["query_alias"] = alias
                    current_match = int(float((best_alias_candidate or {}).get("address_match_percent") or 0))
                    alias_match = int(float(alias_google.get("address_match_percent") or 0))
                    if best_alias_candidate is None or alias_match > current_match:
                        best_alias_candidate = alias_google
            else:
                if best_alias_candidate is not None:
                    google = best_alias_candidate
                    steps.append(f"  [GOOGLE] partial query alias: {google.get('query_alias')}")
        executed_models["geocoding"] = google
        steps.append(_provider_step_label(google, step_name="GOOGLE"))
        if _geocode_accepted(google) and google["confidence"] >= _GOOGLE_CONFIDENCE_THRESHOLD:
            google["_decision_reason"] = (
                f"Google confidence {google['confidence']:.0f} >= {_GOOGLE_CONFIDENCE_THRESHOLD:.0f} "
                f"and address match {google.get('address_match_percent')}%"
            )
            _log_info("Fallback chain: using GOOGLE (confidence=%.1f match=%s%% distance_m=%s)",
                      google["confidence"], google.get("address_match_percent"), google.get("coord_distance_m"))
            return google, steps, executed_models
        if google.get("ok") and not google.get("address_accepted"):
            steps.append(
                f"  [GOOGLE] address match {google.get('address_match_percent')}% "
                f"(requires {_ADDRESS_MATCH_REQUIRED}%) — trying next provider"
            )
    else:
        steps.append("  [GOOGLE] SKIPPED — disabled in Agent 2 options")

    osm = _empty_result("OSM")
    if opts["osm_geocoding"]:
        osm = _finalize_geocode_result(
            _geocode_osm(address, hint_lat=hint_lat, hint_lon=hint_lon, addr=addr),
            input_address=address,
            hint_lat=hint_lat,
            hint_lon=hint_lon,
            a1=a1,
            addr=addr,
        )
        executed_models["osm"] = osm
        steps.append(_provider_step_label(osm, step_name="OSM"))
        osm_required = _GOOGLE_CONFIDENCE_THRESHOLD if not has_google_key else _OSM_CONFIDENCE_THRESHOLD
        if _geocode_accepted(osm) and osm["confidence"] >= osm_required:
            osm["_decision_reason"] = (
                f"Google below threshold; OSM confidence {osm['confidence']:.0f} >= {osm_required:.0f} "
                f"and address match {osm.get('address_match_percent')}%"
            )
            _log_info("Fallback chain: using OSM (confidence=%.1f match=%s%% distance_m=%s)",
                      osm["confidence"], osm.get("address_match_percent"), osm.get("coord_distance_m"))
            return osm, steps, executed_models
        if osm.get("ok") and not osm.get("address_accepted"):
            steps.append(
                f"  [OSM] address match {osm.get('address_match_percent')}% "
                f"(requires {_ADDRESS_MATCH_REQUIRED}%) — trying next provider"
            )
    else:
        steps.append("  [OSM] SKIPPED — disabled in Agent 2 options")

    interp = _empty_result("INTERPOLATED")
    if opts["street_interpolation"]:
        interp = _finalize_geocode_result(
            _interpolate_address(address, addr=addr),
            input_address=address,
            hint_lat=hint_lat,
            hint_lon=hint_lon,
            a1=a1,
            addr=addr,
        )
        executed_models["street_interpolated"] = interp
        steps.append(_provider_step_label(interp, step_name="STREET"))
    else:
        steps.append("  [STREET] SKIPPED — disabled in Agent 2 options")

    candidates = [c for c in (google, osm, interp) if _geocode_accepted(c)]
    if candidates:
        best = max(candidates, key=lambda c: c["confidence"])
        best["_decision_reason"] = (
            f"Picked best of {len(candidates)} accepted provider(s) by highest confidence "
            f"({best.get('source')} = {best['confidence']:.0f}, match={best.get('address_match_percent')}%)"
        )
        _log_info(
            "Fallback chain: picked %s (confidence=%.1f distance_m=%s) from %d candidate(s)",
            best["source"],
            best["confidence"],
            best.get("coord_distance_m"),
            len(candidates),
        )
        return best, steps, executed_models

    _log_warning("Fallback chain: all enabled providers failed for address=%r", address[:200])
    return None, steps, executed_models


def _persist_agent2_ai(addr: Address, geo: dict[str, Any], *, input_address: str) -> None:
    zip_code = (addr.zip_code or "").strip()
    zip5 = zip_code[:5] if zip_code else ""
    meta = addr.raw_metadata if isinstance(addr.raw_metadata, dict) else {}
    country = (meta.get("country_code") or meta.get("country") or "US")
    persist_ai_metadata(
        addr,
        agent_name="agent2_geocoding",
        address=geo.get("formatted_address") or input_address,
        street=geo.get("formatted_address") or input_address,
        latitude=geo.get("latitude"),
        longitude=geo.get("longitude"),
        city=addr.city or "",
        country=str(country).upper()[:8],
        zip=zip5,
        zip_code=zip_code,
        confidence=geo.get("confidence"),
        remarks=geo.get("_decision_reason") or geo.get("_reject_reason") or "",
        ai_type=str(geo.get("source") or ""),
    )


def _reverse_geocode_skip_ok(addr: Address) -> bool:
    """
    True only when reverse geocode at the pin is ROOFTOP, text match is 100%,
    and reverse confidence meets the high threshold (forward match does not count).
    """
    meta = addr.raw_metadata if isinstance(addr.raw_metadata, dict) else {}
    av = meta.get("address_validation") if isinstance(meta.get("address_validation"), dict) else {}
    reverse_pct = av.get("reverse_address_match_percent")
    if reverse_pct is None:
        return False
    loc = (
        str(meta.get("reverse_geocode_location_type") or av.get("location_type") or "")
        .strip()
        .upper()
    )
    if loc != "ROOFTOP" or int(reverse_pct) < _ADDRESS_MATCH_REQUIRED:
        return False
    rg_score = addr.reverse_geocode_confidence_score
    return rg_score is not None and int(rg_score) >= _HIGH_CONFIDENCE_THRESHOLD


def _coord_validation_match_ok(addr: Address) -> bool:
    """True when coordinate/address validation already resolved the row as MATCH."""
    meta = addr.raw_metadata if isinstance(addr.raw_metadata, dict) else {}
    av = meta.get("address_validation") if isinstance(meta.get("address_validation"), dict) else {}
    status = str(addr.coord_address_match_status or av.get("match_status") or "").strip().upper()
    if status != "MATCH":
        return False
    confidence = av.get("confidence_score")
    try:
        return confidence is None or int(float(confidence)) >= int(_HIGH_CONFIDENCE_THRESHOLD)
    except (TypeError, ValueError):
        return False


def _coord_match_selection(addr: Address) -> tuple[str, str, str]:
    """Return selected provider, direction, and reason from reverse/forward validation."""
    meta = addr.raw_metadata if isinstance(addr.raw_metadata, dict) else {}
    av = meta.get("address_validation") if isinstance(meta.get("address_validation"), dict) else {}
    direction = str(av.get("selected_direction") or "").strip().lower()
    provider = str(av.get("selected_provider") or "").strip().upper()
    reason = str(av.get("selection_reason") or "").strip()
    if direction not in {"reverse", "forward"}:
        reverse_pct = av.get("reverse_address_match_percent")
        forward_pct = av.get("forward_address_match_percent")
        try:
            reverse_score = int(float(reverse_pct or 0))
        except (TypeError, ValueError):
            reverse_score = 0
        try:
            forward_score = int(float(forward_pct or 0))
        except (TypeError, ValueError):
            forward_score = 0
        direction = "reverse" if reverse_score > forward_score else "forward"
        reason = reason or "legacy MATCH selection inferred from reverse/forward address scores"
    if not provider:
        if direction == "forward":
            provider = "GOOGLE_FORWARD"
        else:
            reverse_block = meta.get("reverse_geocoding") if isinstance(meta.get("reverse_geocoding"), dict) else {}
            reverse_source = str(reverse_block.get("source") or "").strip().upper()
            provider = {
                "GOOGLE": "GOOGLE_REVERSE",
                "NOMINATIM": "NOMINATIM_REVERSE",
            }.get(reverse_source, reverse_source or "REVERSE_GEOCODER")
    return provider, direction, reason


def _sync_agent1_coord_match(
    session,
    *,
    job_id: str,
    addr: Address,
    row: Agent1Result | None,
    address: str,
    provider: str,
    selection_reason: str,
) -> Agent1Result:
    """Replace stale reverse/Smarty display fields with the accepted coordinate MATCH."""
    now = datetime.utcnow()
    if row is None:
        row = Agent1Result(job_id=job_id, address_id=addr.id, created_at=now, updated_at=now)
        session.add(row)
    else:
        row.updated_at = now
    row.raw_address = addr.raw_address
    row.canonical_address = address
    row.chosen_standardized_address = address
    chosen_provider = str(provider or "geocoder").strip().lower()
    row.chosen_provider = chosen_provider
    row.confidence_score = 100
    row.validation_status = "MATCH"
    row.exception_reason = None
    row.comparison_reason = (
        "Coordinate/address MATCH accepted before Smarty validation; "
        f"{selection_reason or f'{provider} selected'}"
    )
    data = dict(row.data or {})
    data.update({
        "status": "MATCH",
        "validation_status": "MATCH",
        "confidence_score": 100,
        "chosen_provider": chosen_provider,
        "chosen_standardized_address": address,
        "latitude": addr.validated_latitude if addr.validated_latitude is not None else addr.latitude,
        "longitude": addr.validated_longitude if addr.validated_longitude is not None else addr.longitude,
        "comparison_reason": row.comparison_reason,
    })
    row.data = data
    return row


def _coord_validation_text_match_ok(addr: Address) -> bool:
    """True when coord validation accepted uploaded address text (100% match)."""
    meta = addr.raw_metadata if isinstance(addr.raw_metadata, dict) else {}
    av = meta.get("address_validation") if isinstance(meta.get("address_validation"), dict) else {}
    pct = av.get("address_match_percent")
    if pct is not None:
        return int(pct) >= _ADDRESS_MATCH_REQUIRED
    status = (addr.coord_address_match_status or av.get("match_status") or "").upper()
    if status in ("ADDRESS_MISMATCH", "MISMATCH", "UNVERIFIED"):
        return False
    if status == "MATCH":
        return True
    if status == "COORDS_VALIDATED":
        raw: str | None = None
        for attr in ("source_raw_address", "raw_address"):
            val = getattr(addr, attr, None)
            if isinstance(val, str) and val.strip():
                raw = val.strip()
                break
        upload = resolve_upload_address_line(raw, meta)
        return not upload
    return False


def _ensure_table(session) -> None:
    if not session.execute(_sel(AgentTable).where(AgentTable.agent_name == _AGENT_NAME)).scalar_one_or_none():
        session.add(AgentTable(
            agent_name=_AGENT_NAME,
            display_name=_DISPLAY_NAME,
            owner="system",
            description="Geocodes via Google with OSM and street-interpolation fallback",
            color_rules=[],
        ))
        session.commit()
        _log_info("AgentTable row created for agent_name=%r", _AGENT_NAME)
    else:
        _log_debug("AgentTable row already exists for agent_name=%r", _AGENT_NAME)


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
    _log_debug(
        "AgentResult upsert address_id=%s status=%r source=%r confidence=%s",
        address_id,
        data.get("status"),
        data.get("source"),
        data.get("confidence"),
    )


def _run_agent2_chunks_parallel(
    job_id: str,
    *,
    address_ids: list[int],
    progress_callback=None,
    geocode_options: dict[str, bool] | None = None,
    total: int | None = None,
) -> dict[str, int]:
    batch_size = _agent2_batch_size()
    workers = _agent2_worker_count("forward")
    chunks = _chunks(address_ids, batch_size)
    summary = _empty_forward_summary(0)
    done = 0
    progress_lock = threading.Lock()

    _log_info(
        "Agent 2 forward parallel mode: workers=%d batch_size=%d chunks=%d records=%d",
        workers,
        batch_size,
        len(chunks),
        len(address_ids),
    )

    def _chunk_progress(_done: int, _chunk_total: int) -> None:
        nonlocal done
        if not progress_callback:
            return
        with progress_lock:
            done += 1
            current = done
        progress_callback(current, total or len(address_ids))

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="agent2-forward") as executor:
        futures = [
            executor.submit(
                run_agent2_for_job,
                job_id,
                address_ids=chunk,
                progress_callback=_chunk_progress,
                geocode_options=geocode_options,
            )
            for chunk in chunks
        ]
        for future in as_completed(futures):
            _merge_count_summaries(summary, future.result())

    summary["total"] = len(address_ids)
    return summary


def run_geocoding_for_job(
    job_id: str,
    *,
    coord_only_ids: list[int],
    all_ids: list[int],
    address_ids: list[int],
    forward_ids: list[int],
    progress_callback=None,
    geocode_options: dict[str, bool] | None = None,
) -> dict:
    """
    Unified geocoding: reverse (coord-only) → coord validation → forward geocode.

    *geocode_options* controls which phases/providers run (see geocode_options.py).
    """
    from data_ingestion.agents.reverse_geocoder import (
        log_stage_skipped,
        run_address_coord_validation_for_job,
        run_reverse_geocoder_for_job,
    )

    raw_options = geocode_options or {}
    opts = normalize_agent2_options(raw_options)
    option_summary = {
        **opts,
        "coord_match_threshold_m": raw_options.get("coord_match_threshold_m"),
        "coord_mismatch_warn_m": raw_options.get("coord_mismatch_warn_m"),
    }
    _configure_file_logging()
    _log_info("=" * 72)
    _log_info("AGENT 2 — UNIFIED GEOCODING")
    address_total = len(all_ids) or len(forward_ids) or len(coord_only_ids) or 1
    _log_info(
        "Job=%s | addresses=%d | reverse=%d | validate=%d | forward=%d",
        job_id,
        address_total,
        len(coord_only_ids),
        len(all_ids),
        len(forward_ids),
    )
    _log_info("Enabled options: %s", option_summary)
    _log_info("=" * 72)

    phase_totals = [
        len(coord_only_ids),
        len(all_ids),
        len(forward_ids),
    ]
    phase_denoms = [max(total, 1) for total in phase_totals]
    phase_weights = (0.30, 0.20, 0.50)
    phase_done = [0, 0, 0]
    phase_active = [
        bool(opts["reverse_geocoder"] and coord_only_ids),
        bool(opts["coord_validation"] and all_ids),
        bool(forward_ids),
    ]
    active_weight_sum = sum(
        phase_weights[i] for i in range(3) if phase_active[i]
    ) or 1.0

    def _combined_progress(phase_idx: int, done: int, total: int) -> None:
        if not progress_callback:
            return
        phase_done[phase_idx] = done
        weighted = sum(
            phase_weights[i] * (phase_done[i] / phase_denoms[i])
            for i in range(3)
            if phase_active[i]
        ) / active_weight_sum
        progress_callback(
            int(round(weighted * address_total)),
            address_total,
        )

    if opts["reverse_geocoder"] and coord_only_ids:
        reverse_summary = run_reverse_geocoder_for_job(
            job_id,
            coord_only_ids,
            progress_callback=lambda done, total: _combined_progress(0, done, total),
        )
    elif not opts["reverse_geocoder"]:
        reverse_summary = {"skipped": True, "reason": "reverse_geocoder disabled"}
    else:
        log_stage_skipped(
            job_id,
            coord_only=0,
            with_address=len(address_ids),
            total=len(all_ids),
        )
        reverse_summary = {"skipped": True}

    if opts["coord_validation"] and all_ids:
        validation_summary = run_address_coord_validation_for_job(
            job_id,
            all_ids,
            progress_callback=lambda done, total: _combined_progress(1, done, total),
            allow_reverse_fallback=opts.get("reverse_geocoder", True),
            match_threshold_m=raw_options.get("coord_match_threshold_m"),
            mismatch_warn_threshold_m=raw_options.get("coord_mismatch_warn_m"),
        )
    else:
        validation_summary = {"skipped": True, "reason": "coord_validation disabled"}

    if forward_ids:
        forward_summary = run_agent2_for_job(
            job_id,
            address_ids=forward_ids,
            progress_callback=lambda done, total: _combined_progress(2, done, total),
            geocode_options=opts,
        )
    else:
        forward_summary = {"skipped": True}

    if progress_callback:
        progress_callback(address_total, address_total)

    return {
        "total": address_total,
        "reverse": reverse_summary,
        "coord_address_validation": validation_summary,
        "forward": forward_summary,
        "reverse_geocoder": reverse_summary,
        "agent2_geocoding": forward_summary,
        "geocode_options": option_summary,
    }


def run_agent2_for_job(
    job_id: str,
    address_ids: list[int] | None = None,
    progress_callback=None,
    geocode_options: dict[str, bool] | None = None,
) -> dict:
    """Forward-geocode addresses for the given job and store results in agent_results."""
    opts = normalize_agent2_options(geocode_options)
    _configure_file_logging()
    session = get_session_factory()()
    try:
        _ensure_table(session)

        stmt = _sel(Address).where(Address.job_id == job_id).order_by(Address.id)
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
        _log_job_header(job_id, total)
        _log_info(
            "Settings: GOOGLE_KEY=%s reverse_conf=%.0f google_conf=%.0f osm_conf=%.0f agent1_rows=%d options=%s",
            "set" if _get_api_key().strip() else "missing",
            _HIGH_CONFIDENCE_THRESHOLD,
            _GOOGLE_CONFIDENCE_THRESHOLD,
            _OSM_CONFIDENCE_THRESHOLD,
            len(a1_map),
            opts,
        )
        for a in addresses:
            _log_debug(
                "  Address id=%s lat=%r lon=%r raw_address=%r",
                a.id, a.latitude, a.longitude, a.raw_address,
            )

        summary: dict[str, int] = {
            "total": total,
            "geocoded": 0,
            "failed": 0,
            "skipped": 0,
            "google": 0,
            "osm": 0,
            "interpolated": 0,
            "reverse_geocoded": 0,
        }

        batch_size = _agent2_batch_size()
        workers = _agent2_worker_count("forward")
        if _agent2_parallel_enabled(total, workers, batch_size):
            session.close()
            parallel_summary = _run_agent2_chunks_parallel(
                job_id,
                address_ids=addr_ids,
                progress_callback=progress_callback,
                geocode_options=opts,
                total=total,
            )
            _log_job_footer(parallel_summary)
            return parallel_summary

        for idx, addr in enumerate(addresses, 1):
            chain_steps: list[str] = []
            report_status = "failed"
            winner: dict[str, Any] | None = None
            address_str = ""
            address_source = "none"
            hint_lat: float | None = None
            hint_lon: float | None = None
            try:
                a1 = a1_map.get(addr.id)
                meta = addr.raw_metadata or {}
                old_bundle = meta.get("old", {})
                new_bundle = meta.get("new", {})

                rg_score = addr.reverse_geocode_confidence_score
                coord_match_ok = _coord_validation_match_ok(addr)
                if _reverse_geocode_skip_ok(addr) or coord_match_ok:
                    if coord_match_ok:
                        selected_source, selected_direction, selection_reason = _coord_match_selection(addr)
                        address_str = str(
                            addr.validated_raw_address
                            or meta.get("ADDRESS")
                            or addr.raw_address
                            or ""
                        ).strip()
                        address_source = f"coord_address_match_{selected_direction}"
                        selected_confidence = 100
                        a1 = _sync_agent1_coord_match(
                            session,
                            job_id=job_id,
                            addr=addr,
                            row=a1,
                            address=address_str,
                            provider=selected_source,
                            selection_reason=selection_reason,
                        )
                        a1_map[addr.id] = a1
                    else:
                        address_str, address_source = _resolve_input_address(addr, a1)
                        selected_source = "REVERSE_GEOCODER"
                        selected_confidence = int(rg_score or 0)
                    hint_lat, hint_lon = _resolve_hint_coords(addr, a1)

                    summary["geocoded"] += 1
                    summary["reverse_geocoded"] += 1
                    report_status = "validated_match" if coord_match_ok else "reverse_geocoded"
                    if coord_match_ok:
                        chain_steps.append(
                            "  [FORWARD GEOCODE] SKIPPED — coordinate/address status is MATCH "
                            f"at confidence 100; selected {selected_direction} ({selected_source})"
                        )
                    else:
                        chain_steps.append(
                            f"  [FORWARD GEOCODE] SKIPPED — Reverse geocode confidence {rg_score} >= "
                            f"{_HIGH_CONFIDENCE_THRESHOLD} and address text match OK"
                        )

                    final_result_obj = {
                        "source": selected_source,
                        "confidence": selected_confidence,
                        "formatted_address": addr.validated_raw_address or addr.raw_address,
                        "latitude": addr.validated_latitude if addr.validated_latitude is not None else addr.latitude,
                        "longitude": addr.validated_longitude if addr.validated_longitude is not None else addr.longitude,
                    }
                    meta["final_result"] = final_result_obj
                    addr.raw_metadata = meta
                    flag_modified(addr, "raw_metadata")

                    _upsert(session, job_id, addr.id, {
                        "status": "validated_match" if coord_match_ok else "reverse_geocoded",
                        "reason": (
                            "coordinate/address MATCH accepted at confidence 100"
                            if coord_match_ok
                            else f"high reverse geocode confidence ({rg_score})"
                        ),
                        "input_address": address_str,
                        "input_source": address_source,
                        "formatted_address": addr.validated_raw_address or addr.raw_address,
                        "latitude": addr.validated_latitude if addr.validated_latitude is not None else addr.latitude,
                        "longitude": addr.validated_longitude if addr.validated_longitude is not None else addr.longitude,
                        "confidence": selected_confidence,
                        "source": selected_source,
                        "is_estimated": False,
                        "fallback_used": False,
                        "old": old_bundle,
                        "new": new_bundle,
                        "final_result": final_result_obj,
                    })
                else:
                    address_str, address_source = _resolve_input_address(addr, a1)
                    hint_lat, hint_lon = _resolve_hint_coords(addr, a1)
                    if (
                        rg_score is not None
                        and rg_score >= _HIGH_CONFIDENCE_THRESHOLD
                        and not _reverse_geocode_skip_ok(addr)
                    ):
                        chain_steps.append(
                            f"  [FORWARD GEOCODE] Running — reverse confidence {rg_score} but "
                            f"reverse text match or ROOFTOP check failed "
                            f"(reverse_match={((addr.raw_metadata or {}).get('address_validation') or {}).get('reverse_address_match_percent')})"
                        )

                    if not address_str:
                        summary["skipped"] += 1
                        report_status = "skipped"
                        
                        meta["final_result"] = None
                        addr.raw_metadata = meta
                        flag_modified(addr, "raw_metadata")
                        
                        _upsert(session, job_id, addr.id, {
                            "status": "skipped",
                            "reason": "no address available in database",
                            "input_address": "",
                            "confidence": 0,
                            "old": old_bundle,
                            "new": new_bundle,
                            "final_result": None
                        })
                    else:
                        geo, chain_steps, executed_models = _geocode_with_fallback(
                            address_str,
                            hint_lat=hint_lat,
                            hint_lon=hint_lon,
                            a1=a1,
                            addr=addr,
                            geocode_options=opts,
                        )
                        winner = geo
                        if geo:
                            report_status = "geocoded"
                            summary["geocoded"] += 1
                            source = str(geo.get("source", "GOOGLE")).upper()
                            if source == "OSM":
                                summary["osm"] += 1
                            elif source == "INTERPOLATED":
                                summary["interpolated"] += 1
                            else:
                                summary["google"] += 1

                            final_result_obj = {
                                "source": geo.get("source", "GOOGLE"),
                                "confidence": geo["confidence"],
                                "formatted_address": geo["formatted_address"],
                                "latitude": geo["latitude"],
                                "longitude": geo["longitude"],
                            }
                            persist_provider_results(addr, executed_models)
                            meta = dict(addr.raw_metadata or {})
                            meta["final_result"] = final_result_obj
                            _persist_agent2_ai(addr, geo, input_address=address_str)
                            addr.raw_metadata = meta
                            flag_modified(addr, "raw_metadata")

                            # Do not overwrite upload lat/lon; geocoded coords go to agent_results only.

                            _upsert(session, job_id, addr.id, {
                                "status": "geocoded",
                                "input_address": address_str,
                                "input_source": address_source,
                                "formatted_address": geo["formatted_address"],
                                "latitude": geo["latitude"],
                                "longitude": geo["longitude"],
                                "location_type": geo["location_type"],
                                "place_id": geo["place_id"],
                                "confidence": geo["confidence"],
                                "coord_distance_m": geo.get("coord_distance_m"),
                                "source": geo.get("source", "GOOGLE"),
                                "fallback_used": geo.get("fallback_used", False),
                                "is_estimated": geo.get("is_estimated", False),
                                "old": old_bundle,
                                "new": new_bundle,
                                **executed_models,
                                "final_result": final_result_obj
                            })
                        else:
                            if not any_forward_enabled(opts):
                                summary["skipped"] += 1
                                report_status = "skipped"
                                fail_reason = "forward geocoding modes disabled"
                            else:
                                summary["failed"] += 1
                                report_status = "failed"
                                fail_reason = "no provider returned 100% address match"
                            
                            persist_provider_results(addr, executed_models)
                            meta = dict(addr.raw_metadata or {})
                            meta["final_result"] = None
                            addr.raw_metadata = meta
                            flag_modified(addr, "raw_metadata")
                            
                            _upsert(session, job_id, addr.id, {
                                "status": report_status,
                                "input_address": address_str,
                                "input_source": address_source,
                                "reason": fail_reason,
                                "confidence": 0,
                                "old": old_bundle,
                                "new": new_bundle,
                                **executed_models,
                                "final_result": None
                            })
            except Exception as exc:
                _log_error(
                    "[%d/%d] address_id=%s ERROR: %s",
                    idx, total, addr.id, exc,
                    exc_info=True,
                )
                summary["failed"] += 1
                report_status = "error"
                chain_steps.append(f"  [ERROR] {exc}")

            _log_address_report(
                idx=idx,
                total=total,
                address_id=addr.id,
                input_address=address_str,
                input_source=address_source,
                hint_lat=hint_lat,
                hint_lon=hint_lon,
                steps=chain_steps,
                winner=winner,
                status=report_status,
            )

            session.flush()
            if progress_callback:
                progress_callback(idx, total)

        session.commit()
        _log_job_footer(summary)
        return summary
    except Exception as exc:
        session.rollback()
        _log_error("run_agent2_for_job FAILED job_id=%r — rolled back: %s", job_id, exc, exc_info=True)
        raise
    finally:
        session.close()
        _log_debug("session.close job_id=%r", job_id)

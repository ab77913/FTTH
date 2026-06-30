"""
Reverse Geocoder — coords-only address records (Agent 2 phase 1)
================================================================
Implementation module for reverse geocoding, orchestrated by agent2_geocoding.py.
Called when a record has lat/lon but no raw_address (or a KMZ placemark label).

Flow:
  1. For each address_id in the provided list, load the Address row
  2. Build cache key f"{lat:.5f},{lon:.5f}"
  3. Check Redis cache (TTL 30 days)
       CACHE HIT  → restore result, skip HTTP
       CACHE MISS → call Nominatim (respects 1 req/sec via Redis rate limiter);
                    fallback to Google Maps if GOOGLE_MAPS_API_KEY is set
  4. Upsert result into agent1_results; store confidence on addresses.reverse_geocode_confidence_score
  5. Backfill addresses.raw_address, .city, .state, .zip_code (additive — don't overwrite existing)
  6. Compare uploaded address vs coordinates; write old/new/ADDRESS into raw_metadata
     (and mirror key fields to validated_* DB columns for querying)

Logging:
  All activity is written to ``logs/reverse_geocoder.log`` (rotating).
  Override path: ``FTTH_RGC_LOG_FILE``. Disable: ``FTTH_RGC_LOG=0``.

Changes from v1:
  - Nominatim quality gate: results with neither road nor city are rejected before
    caching, allowing Google fallback to run even when Nominatim technically responds.
  - Cache miss entries (TTL 1 h) prevent hammering APIs for known-bad coordinates
    while still allowing retry after a short window.  Low-quality Nominatim results
    are never written to the positive cache.
  - Dynamic confidence scoring replaces the hardcoded 90.
  - Two-tier distance thresholds: FTTH_COORD_MATCH_THRESHOLD_M (default 100 m) for
    MATCH and FTTH_COORD_MISMATCH_WARN_M (default 500 m) for a soft MISMATCH_WARN
    status, making rural/large-parcel addresses less likely to be flagged as hard
    mismatches.
  - Haversine float guard: clamps `a` to [0, 1] on both sides to handle fp rounding.
  - Logging PID guard: each forked Celery worker attaches its own rotating handler
    so log records are not lost after a fork.

Changes from v2:
  - Confidence scoring now uses native API signals:
      Nominatim: place_rank (0–30), importance (0–1.0), boundingbox precision check.
      Google:    geometry.location_type (ROOFTOP / RANGE_INTERPOLATED /
                 GEOMETRIC_CENTER / APPROXIMATE).
  - _nominatim_result_is_reliable() also rejects place_rank < 20 (county-level).
  - Bounding-box precision helper (_nominatim_bbox_is_precise) guards against
    large-area matches that happen to include a road/city token.
  - match_status is forwarded into _compute_confidence so validation outcome
    can further adjust the score.
  - place_rank, importance, location_type stored in cached geo dict so cache
    hits also produce accurate scores without re-calling the API.
"""
from __future__ import annotations
from data_ingestion.config.paths import PROJECT_ROOT
from data_ingestion.config.log_paths import agent_log_path as _default_agent_log_path

import json
import logging
import math
import os
import re
import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any
import urllib.parse

import requests

from sqlalchemy import select as _sel
from sqlalchemy.orm.attributes import flag_modified

from data_ingestion.database.db import ensure_address_validation_columns, get_session_factory
from data_ingestion.database.geo import set_address_geom
from data_ingestion.database.models import Address, Agent1Result
from data_ingestion.utils.address_match import (
    address_match_percent_for_geo,
    finalize_geocoder_confidence,
    looks_like_street_address,
    resolve_upload_address_line,
    strip_kml_category_prefix,
)
from data_ingestion.utils.address_metadata import sync_reverse_geocode_confidence_in_raw_metadata
from data_ingestion.utils.api_result_metadata import persist_provider_result
from data_ingestion.utils.redis_cache import RedisCache
from data_ingestion.utils.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

# Bump when raw_metadata address_validation schema changes (see logs on validation start).
_RAW_METADATA_SCHEMA_VERSION = 4

_PROJECT_ROOT = PROJECT_ROOT
_LOG_FILE = Path(os.environ.get("FTTH_RGC_LOG_FILE", str(_default_agent_log_path("reverse_geocoder"))))

try:
    from dotenv import load_dotenv
    load_dotenv(_PROJECT_ROOT / ".env", override=True)
except ImportError:
    pass

_LOG_CONFIGURED = False
_LOG_CONFIGURED_PID: int = -1
_LOG_LOCK = threading.Lock()


def _logging_enabled() -> bool:
    return os.environ.get("FTTH_RGC_LOG", "1").lower() not in ("0", "false", "no", "off")


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


# ── Confidence scoring constants ─────────────────────────────────────────────

# Google geometry.location_type → score delta
_LOCATION_TYPE_BONUS: dict[str, int] = {
    "ROOFTOP": 25,            # exact rooftop match — highest precision
    "RANGE_INTERPOLATED": 10, # interpolated along a road segment
    "GEOMETRIC_CENTER": 0,    # centroid of a named feature — neutral
    "APPROXIMATE": -15,       # broad area match — penalise
}

# Nominatim place_rank ranges → score delta
# place_rank 30 = building/house, 26 = road, 16–25 = suburb/postcode/town,
# 12–15 = city, <12 = county / country.
_PLACE_RANK_BONUSES: list[tuple[range, int]] = [
    (range(28, 31), 20),   # house / building — rooftop-level
    (range(24, 28), 12),   # road / path
    (range(16, 24), 5),    # suburb / postcode / town
    (range(0,  16), -10),  # city-level or coarser — penalise
]

# Nominatim importance thresholds
_IMPORTANCE_HIGH = 0.7   # well-known place — small bonus
_IMPORTANCE_LOW  = 0.3   # obscure / uncertain — small penalty

# Match-status modifiers applied at the end of scoring
_MATCH_STATUS_DELTA: dict[str, int] = {
    "MATCH":            5,
    "MISMATCH_WARN":  -10,
    "MISMATCH":       -25,
    "ADDRESS_MISMATCH": -30,
    "COORDS_VALIDATED": 0,
    "UNVERIFIED":     -20,
}


# ── Geocoder quality helpers ─────────────────────────────────────────────────

def _nominatim_bbox_is_precise(data: dict[str, Any]) -> bool:
    """
    Return True when the Nominatim bounding box is small enough to indicate
    a precise match (~1 km on each side).

    A large bounding box (e.g. a whole county) on an otherwise road/city-level
    result is a signal that the match covers a wide area, not a specific address.
    When the bbox is absent we optimistically return True so the result is not
    downgraded based on missing metadata alone.
    """
    bb = data.get("boundingbox")  # ["lat1", "lat2", "lon1", "lon2"] as strings
    if not bb or len(bb) != 4:
        return True
    try:
        lat_span = abs(float(bb[1]) - float(bb[0]))
        lon_span = abs(float(bb[3]) - float(bb[2]))
    except (TypeError, ValueError):
        return True
    # 0.01 degrees ≈ 1.1 km — coarser than that is suspicious for a house/road result
    return lat_span < 0.01 and lon_span < 0.01


def _nominatim_result_is_reliable(geo: dict[str, Any]) -> bool:
    """
    Return True when the Nominatim result is specific enough to be useful.

    Criteria (all must pass):
      • result has at least road OR city                  (unchanged from v1)
      • place_rank >= 20  (road-level or better;
        rank 19 = suburb, 16 = city, 12 = county …)     (NEW in v2)

    County-level or country-level results (road and city both absent, or
    place_rank < 20) are too coarse for FTTH address validation and should
    not block the Google fallback.
    """
    has_detail = bool(geo.get("road") or geo.get("city"))
    place_rank = int(geo.get("place_rank") or 0)
    return has_detail and place_rank >= 20


def _reverse_geo_location_type(geo: dict[str, Any] | None) -> str:
    """Extract Google location_type or infer from Nominatim place_rank."""
    if not geo:
        return ""
    loc = (geo.get("location_type") or "").strip().upper()
    if loc:
        return loc
    if geo.get("source") == "nominatim":
        place_rank = int(geo.get("place_rank") or 0)
        if place_rank >= 28:
            return "ROOFTOP"
        if place_rank >= 24:
            return "RANGE_INTERPOLATED"
        if place_rank >= 16:
            return "GEOMETRIC_CENTER"
        return "APPROXIMATE"
    return ""


def _address_match_scores(
    upload_line: str,
    *,
    reverse_geo: dict[str, Any] | None,
    forward_geo: dict[str, Any] | None,
    addr: Address,
    coord_distance_m: float | None = None,
) -> dict[str, int]:
    """Per-provider and best text-match scores (upload vs reverse / forward separately)."""
    if not upload_line.strip():
        return {"reverse": 100, "forward": 100, "best": 100}
    kwargs = {
        "city": addr.city,
        "state": addr.state,
        "zip_code": addr.zip_code,
        "coord_distance_m": coord_distance_m,
    }
    reverse_pct = (
        address_match_percent_for_geo(upload_line, reverse_geo, **kwargs)
        if reverse_geo
        else 0
    )
    forward_pct = (
        address_match_percent_for_geo(upload_line, forward_geo, **kwargs)
        if forward_geo
        else 0
    )
    return {
        "reverse": reverse_pct,
        "forward": forward_pct,
        "best": max(reverse_pct, forward_pct),
    }


def _best_address_match_percent(
    upload_line: str,
    *,
    reverse_geo: dict[str, Any] | None,
    forward_geo: dict[str, Any] | None,
    addr: Address,
    coord_distance_m: float | None = None,
) -> int:
    """Highest text-match score between upload line and reverse/forward geocoder outputs."""
    return _address_match_scores(
        upload_line,
        reverse_geo=reverse_geo,
        forward_geo=forward_geo,
        addr=addr,
        coord_distance_m=coord_distance_m,
    )["best"]


def _compute_confidence(
    geo: dict[str, Any] | None,
    distance_m: float | None,
    *,
    match_status: str = "",
    address_match_percent: int | None = None,
) -> int:
    """
    Dynamic confidence score (0–99).

    Scoring breakdown
    -----------------
    Base                                    50

    Field-level signals (both providers):
      house_number present                 +20   (rooftop-level precision)
      road present                         +15
      postcode present                     +10

    Google-specific (geometry.location_type):
      ROOFTOP                              +25
      RANGE_INTERPOLATED                   +10
      GEOMETRIC_CENTER                      +0
      APPROXIMATE                          -15

    Nominatim-specific:
      place_rank 28–30 (house/building)    +20
      place_rank 24–27 (road/path)         +12
      place_rank 16–23 (suburb/postcode)    +5
      place_rank  0–15 (city or coarser)   -10
      importance >= 0.7 (well-known)        +5
      importance <  0.3 (obscure)           -5

    Distance cross-validation:
      distance_m < 50 m                    +10   (very tight agreement)
      distance_m < MATCH_THRESHOLD_M       +5    (within match window)
      distance_m < MISMATCH_WARN_M         -10   (soft mismatch zone)
      distance_m >= MISMATCH_WARN_M        -25   (hard mismatch)

    Match-status modifier:
      MATCH                                 +5
      MISMATCH_WARN                        -10
      MISMATCH                             -25
      COORDS_VALIDATED                      +0
      ADDRESS_MISMATCH                    -30
      UNVERIFIED                           -20
    """
    if geo is None:
        return 0

    score = 50
    source = geo.get("source", "")

    # ── Field-level signals (both providers) ─────────────────────────────
    if geo.get("house_number"):
        score += 20
    if geo.get("road"):
        score += 15
    if geo.get("postcode"):
        score += 10

    # ── Google: geometry.location_type ───────────────────────────────────
    location_type = (geo.get("location_type") or "").upper()
    if location_type:
        delta = _LOCATION_TYPE_BONUS.get(location_type, 0)
        score += delta
        _log_debug("confidence location_type=%r delta=%+d", location_type, delta)

    # ── Nominatim: place_rank + importance + bbox ─────────────────────────
    if source == "nominatim":
        place_rank = int(geo.get("place_rank") or 0)
        for rank_range, bonus in _PLACE_RANK_BONUSES:
            if place_rank in rank_range:
                score += bonus
                _log_debug("confidence place_rank=%d delta=%+d", place_rank, bonus)
                break

        importance = float(geo.get("importance") or 0.0)
        if importance >= _IMPORTANCE_HIGH:
            score += 5
            _log_debug("confidence importance=%.3f delta=+5", importance)
        elif importance < _IMPORTANCE_LOW:
            score -= 5
            _log_debug("confidence importance=%.3f delta=-5", importance)

        # Penalise if the bounding box covers a large area even though road/city
        # fields are present — indicates a coarse polygon match.
        if not geo.get("_bbox_precise", True):
            score -= 10
            _log_debug("confidence bbox imprecise delta=-10")

    # ── Distance cross-validation ─────────────────────────────────────────
    if distance_m is not None:
        if distance_m < 50:
            score += 10
            _log_debug("confidence distance=%.1fm delta=+10", distance_m)
        elif distance_m < _MATCH_THRESHOLD_M:
            score += 5
            _log_debug("confidence distance=%.1fm delta=+5", distance_m)
        elif distance_m < _MISMATCH_WARN_THRESHOLD_M:
            score -= 10
            _log_debug("confidence distance=%.1fm delta=-10", distance_m)
        else:
            score -= 25
            _log_debug("confidence distance=%.1fm delta=-25", distance_m)

    # ── Match-status modifier ─────────────────────────────────────────────
    if match_status:
        delta = _MATCH_STATUS_DELTA.get(match_status, 0)
        score += delta
        _log_debug("confidence match_status=%r delta=%+d", match_status, delta)

    final = max(0, min(score, 99))
    if (
        address_match_percent is not None
        and address_match_percent < _ADDRESS_MATCH_REQUIRED
    ):
        final = min(final, _LOW_MATCH_CONFIDENCE_CAP)
    _log_debug(
        "confidence FINAL=%d (source=%r location_type=%r match_status=%r distance_m=%s address_match=%s)",
        final, source, location_type, match_status, distance_m, address_match_percent,
    )
    return final


def _finalize_reverse_confidence(
    raw_score: int,
    *,
    location_type: str,
    reverse_address_match_percent: int | None,
) -> int:
    """Reverse geocode confidence — ROOFTOP + 100% text match required for high score."""
    return int(
        finalize_geocoder_confidence(
            raw_score,
            location_type=location_type,
            address_match_percent=reverse_address_match_percent,
        )
    )


def _apply_reverse_geocode_confidence(
    addr: Address,
    geo: dict[str, Any] | None,
    *,
    match_status: str = "",
    distance_m: float | None = None,
    address_match_percent: int | None = None,
) -> int:
    """Persist reverse-geocode confidence on the addresses row (0 when geo is missing)."""
    if not geo:
        addr.reverse_geocode_confidence_score = 0
        return 0
    raw = _compute_confidence(
        geo,
        distance_m=distance_m,
        match_status=match_status,
        address_match_percent=address_match_percent,
    )
    confidence = _finalize_reverse_confidence(
        raw,
        location_type=_reverse_geo_location_type(geo),
        reverse_address_match_percent=address_match_percent,
    )
    addr.reverse_geocode_confidence_score = confidence
    return confidence


def _refresh_reverse_geocode_confidence(
    session,
    addr: Address,
    geo: dict[str, Any],
    *,
    match_status: str = "",
    distance_m: float | None = None,
    address_match_percent: int | None = None,
) -> int:
    """Update addresses.reverse_geocode_confidence_score; mirror to agent1_results when present."""
    confidence = _apply_reverse_geocode_confidence(
        addr,
        geo,
        match_status=match_status,
        distance_m=distance_m,
        address_match_percent=address_match_percent,
    )
    _sync_confidence_in_raw_metadata(addr, confidence)
    row = session.scalar(_sel(Agent1Result).where(Agent1Result.address_id == addr.id))
    if row:
        row.confidence_score = confidence
        row.updated_at = datetime.utcnow()
    _log_debug(
        "reverse_geocode confidence refresh address_id=%s score=%s match_status=%r distance_m=%s",
        addr.id,
        confidence,
        match_status,
        distance_m,
    )
    return confidence


def is_kmz_placemark_label(address_text: str | None, meta: dict | None) -> bool:
    """True when raw_address is a KMZ folder/placemark label, not a postal address."""
    if not address_text or not str(address_text).strip():
        return False
    meta = meta or {}
    if meta.get("source_format") not in ("kml", "kmz"):
        return False
    text = str(address_text).strip()
    placemark = str(meta.get("placemark_name") or meta.get("Address") or meta.get("address") or "").strip()
    category = str(meta.get("category") or "").strip()
    labels: set[str] = set()
    if placemark:
        labels.add(placemark)
    if category:
        labels.add(category)
    if category and placemark:
        labels.add(f"{category}: {placemark}")
    if text not in labels:
        return False
    # KML display label — but placemark may still hold a real street address.
    if looks_like_street_address(placemark) or looks_like_street_address(strip_kml_category_prefix(text, meta)):
        return False
    return True


def _resolve_upload_address(addr: Address, meta: dict | None = None) -> str:
    """Upload street line for text match / forward geocode (handles KML household rows)."""
    meta = meta if isinstance(meta, dict) else (addr.raw_metadata or {})
    raw: str | None = None
    for attr in ("source_raw_address", "raw_address"):
        val = getattr(addr, attr, None)
        if isinstance(val, str):
            s = val.strip()
            if s:
                raw = s
                break
    return resolve_upload_address_line(raw, meta)


def log_stage_skipped(job_id: str, *, coord_only: int, with_address: int, total: int) -> None:
    """Called by pipeline_runner when no coord-only rows need reverse geocoding."""
    _configure_file_logging()
    _log_info("=" * 72)
    _log_info(
        "reverse_geocoder SKIPPED for job_id=%r: coord_only=0 (with_address=%d total=%d). "
        "Rows need lat+lon and no raw_address to enter this stage.",
        job_id,
        with_address,
        total,
    )
    _log_info("log file path: %s", _LOG_FILE.resolve())
    _log_info("=" * 72)


_NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
_GOOGLE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
_USER_AGENT = "FTTH-DataIngestion/1.0"
_CACHE_TTL = 2_592_000           # 30 days — positive (good quality) results
_CACHE_MISS_TTL = 3_600          # 1 hour  — negative / low-quality miss entries
_MATCH_THRESHOLD_M = float(os.environ.get("FTTH_COORD_MATCH_THRESHOLD_M", "100"))
_MISMATCH_WARN_THRESHOLD_M = float(os.environ.get("FTTH_COORD_MISMATCH_WARN_M", "500"))
_ADDRESS_MATCH_REQUIRED = int(os.environ.get("FTTH_AGENT2_ADDRESS_MATCH_REQUIRED", "100"))
_LOW_MATCH_CONFIDENCE_CAP = int(os.environ.get("FTTH_AGENT2_LOW_MATCH_CONFIDENCE", "35"))
_CACHE_RESULT_MAX_DISTANCE_M = float(os.environ.get("FTTH_RGC_CACHE_MAX_DISTANCE_M", "2000"))


def _cached_geo_matches_query(geo: dict[str, Any] | None, lat: float, lon: float) -> bool:
    """Reject poisoned cache entries whose result point is far from the cache key."""
    if not geo:
        return False
    geo_lat = geo.get("latitude")
    geo_lon = geo.get("longitude")
    if geo_lat in (None, "") or geo_lon in (None, ""):
        # Legacy Nominatim entries may not carry their result point.
        return True
    try:
        distance_m = _haversine_m(float(lat), float(lon), float(geo_lat), float(geo_lon))
    except (TypeError, ValueError):
        return False
    return distance_m <= _CACHE_RESULT_MAX_DISTANCE_M

# Module-level singletons (created on first use)
_cache: RedisCache | None = None
_rate_limiter: RateLimiter | None = None
_cache_lock = threading.Lock()
_rate_limiter_lock = threading.Lock()
_validation_threshold_lock = threading.Lock()


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


def _merge_int_summaries(base: dict[str, int], extra: dict[str, int]) -> dict[str, int]:
    for key, value in extra.items():
        if isinstance(value, int):
            base[key] = int(base.get(key, 0)) + value
    return base


def _get_cache() -> RedisCache:
    global _cache
    if _cache is None:
        _cache = RedisCache("ftth:rgc", ttl=_CACHE_TTL)
    return _cache


def _get_rate_limiter() -> RateLimiter:
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = RateLimiter("nominatim", rate=1, period=1.0)
    return _rate_limiter


_SSL_VERIFY_WARNED = False


def _ssl_verify() -> bool | str:
    """
    CA bundle for HTTPS geocoder calls.
    - Default: certifi (fixes many Windows/Python SSL issues)
    - FTTH_CA_BUNDLE: path to corporate root PEM
    - FTTH_SSL_VERIFY=0: disable verification (corporate proxy only)
    """
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


def _http_get_json(url: str, *, timeout: float = 12) -> dict[str, Any]:
    """GET JSON over HTTPS using requests + certifi (avoids urllib SSL issues on Windows)."""
    global _SSL_VERIFY_WARNED
    verify = _ssl_verify()
    if verify is False and not _SSL_VERIFY_WARNED:
        _log_warning("FTTH_SSL_VERIFY=0 — HTTPS certificate verification is disabled for geocoders")
        _SSL_VERIFY_WARNED = True
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": _USER_AGENT},
            timeout=timeout,
            verify=verify,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.SSLError as exc:
        allow_fallback = os.environ.get("FTTH_SSL_INSECURE_FALLBACK", "1").lower() not in (
            "0", "false", "no", "off",
        )
        if not allow_fallback or verify is False:
            raise
        _log_warning(
            "HTTPS certificate verification failed (%s). Retrying without verification. "
            "Fix: set FTTH_CA_BUNDLE to your corporate root PEM, or FTTH_SSL_VERIFY=0, "
            "or FTTH_SSL_INSECURE_FALLBACK=0 to disable this retry.",
            exc,
        )
        try:
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        except Exception:
            pass
        resp = requests.get(
            url,
            headers={"User-Agent": _USER_AGENT},
            timeout=timeout,
            verify=False,
        )
        resp.raise_for_status()
        return resp.json()


# ── Geocoding calls ─────────────────────────────────────────────────────────────

def _call_nominatim(lat: float, lon: float, addr: Address | None = None) -> dict[str, Any] | None:
    _log_info("_call_nominatim IN: lat=%r lon=%r", lat, lon)
    request_payload = {"format": "json", "lat": lat, "lon": lon, "addressdetails": 1}
    params = urllib.parse.urlencode(request_payload)
    url = f"{_NOMINATIM_URL}?{params}"
    _log_debug("Nominatim request URL: %s", url)
    with _rate_limiter_lock:
        _get_rate_limiter().acquire()
    _log_debug("Nominatim rate limiter acquired for (%.5f, %.5f)", lat, lon)
    try:
        data = _http_get_json(url, timeout=10)
        _log_info("Nominatim raw response: %s", json.dumps(data, default=str))
        if "error" in data:
            _log_warning("Nominatim error field: %s", data.get("error"))
            return None
        addr = data.get("address", {})
        city = (
            addr.get("city")
            or addr.get("town")
            or addr.get("village")
            or addr.get("hamlet")
            or addr.get("county")
            or ""
        )

        # ── place_rank / importance / bbox are new in v2 ──────────────────
        place_rank = int(data.get("place_rank") or 0)
        importance = float(data.get("importance") or 0.0)
        bbox_precise = _nominatim_bbox_is_precise(data)

        parsed = {
            "display_name": data.get("display_name", ""),
            "house_number": addr.get("house_number", ""),
            "road": addr.get("road", "") or addr.get("pedestrian", "") or addr.get("path", ""),
            "city": city,
            "state": addr.get("state", ""),
            "postcode": addr.get("postcode", ""),
            "country": addr.get("country", ""),
            "country_code": (addr.get("country_code") or "")[:8].upper(),
            # Native API signals stored in the cached dict so cache hits score correctly
            "place_rank": place_rank,
            "importance": importance,
            "_bbox_precise": bbox_precise,
            "address_type": data.get("type", ""),
            "address_types": [data.get("type")] if data.get("type") else [],
            "source": "nominatim",
        }
        _log_info(
            "Nominatim parsed: %s  (place_rank=%d importance=%.3f bbox_precise=%s)",
            parsed, place_rank, importance, bbox_precise,
        )
        return parsed
    except Exception:
        _log_error("Nominatim failed for lat=%r lon=%r", lat, lon, exc_info=True)
        return None


def _call_google(
    lat: float,
    lon: float,
    api_key: str,
    addr: Address | None = None,
) -> dict[str, Any] | None:
    _log_info("_call_google IN: lat=%r lon=%r api_key_set=%s", lat, lon, bool(api_key))
    request_payload = {"latlng": f"{lat},{lon}"}
    params = urllib.parse.urlencode({**request_payload, "key": api_key})
    url = f"{_GOOGLE_URL}?{params}"
    _log_debug("Google request URL (key redacted): %s", f"{_GOOGLE_URL}?latlng={lat},{lon}&key=***")
    try:
        data = _http_get_json(url, timeout=10)
        _log_info("Google raw response: %s", json.dumps(data, default=str))
        if data.get("status") != "OK" or not data.get("results"):
            _log_warning(
                "Google no usable result: status=%r error_message=%r",
                data.get("status"),
                data.get("error_message"),
            )
            return None
        parsed = _parse_google_geocode_result(data["results"][0])
        parsed["source"] = "google"
        _log_info(
            "Google parsed: %s  (location_type=%r)",
            parsed, parsed.get("location_type"),
        )
        return parsed
    except Exception:
        _log_error("Google failed for lat=%r lon=%r", lat, lon, exc_info=True)
        return None


def _parse_google_geocode_result(result: dict[str, Any]) -> dict[str, Any]:
    """
    Normalize a Google Geocoding API result to the shared geo component dict.

    location_type is extracted and stored so _compute_confidence can use it
    for cache hits without re-calling the API.
    """
    comps: dict[str, str] = {}
    country_code = ""
    for c in result.get("address_components", []):
        for t in c.get("types", []):
            if t not in comps:
                comps[t] = c.get("long_name", "")
            if t == "country":
                country_code = c.get("short_name", "") or country_code
    city = (
        comps.get("locality")
        or comps.get("sublocality")
        or comps.get("administrative_area_level_2", "")
    )
    geometry = result.get("geometry", {})
    loc = geometry.get("location", {})

    # location_type is the primary Google precision signal — store it in the dict
    location_type = geometry.get("location_type", "")

    parsed: dict[str, Any] = {
        "display_name": result.get("formatted_address", ""),
        "house_number": comps.get("street_number", ""),
        "road": comps.get("route", ""),
        "city": city,
        "state": comps.get("administrative_area_level_1", ""),
        "postcode": comps.get("postal_code", ""),
        "country": comps.get("country", ""),
        "country_code": country_code.upper()[:8] if country_code else "",
        # Native API precision signal — persisted in cache dict
        "location_type": location_type,
        "address_type": (result.get("types") or [""])[0],
        "address_types": list(result.get("types") or []),
        "source": "google",
    }
    if loc:
        parsed["latitude"] = float(loc.get("lat", 0))
        parsed["longitude"] = float(loc.get("lng", 0))
    return parsed


def _use_nominatim() -> bool:
    """Whether to call Nominatim (off by default when a Google geocoder key is configured)."""
    flag = os.environ.get("FTTH_SKIP_NOMINATIM", "").lower()
    if flag in ("1", "true", "yes"):
        return False
    if flag in ("0", "false", "no"):
        return True
    if os.environ.get("FTTH_USE_NOMINATIM", "").lower() in ("1", "true", "yes"):
        return True
    # Corporate Windows networks often block Nominatim SSL; prefer Google when available.
    return not bool(_google_api_key())


def _reverse_geocode(lat: float, lon: float, addr: Address | None = None) -> dict[str, Any] | None:
    """
    Try Nominatim first (if enabled); fall back to Google when a Google API key is set.

    Quality gate: if Nominatim returns a result but it lacks both road and city,
    or has place_rank < 20 (county/country level), it is treated as unreliable and
    Google is tried regardless of FTTH_PREFER_GOOGLE_GEOCODER.
    """
    _log_info("_reverse_geocode IN: lat=%r lon=%r", lat, lon)
    api_key = _google_api_key()
    skip_nominatim = not _use_nominatim()
    prefer_google = os.environ.get("FTTH_PREFER_GOOGLE_GEOCODER", "").lower() in ("1", "true", "yes")

    if api_key and (skip_nominatim or prefer_google):
        _log_info("Using Google reverse geocode (FTTH_SKIP_NOMINATIM / FTTH_PREFER_GOOGLE_GEOCODER)")
        google_result = _call_google(lat, lon, api_key, addr=addr)
        if google_result:
            _log_info("_reverse_geocode OUT (google): %s", google_result)
            return google_result

    if not skip_nominatim:
        nominatim_result = _call_nominatim(lat, lon, addr=addr)
        if nominatim_result:
            if _nominatim_result_is_reliable(nominatim_result):
                _log_info("_reverse_geocode OUT (nominatim): %s", nominatim_result)
                return nominatim_result
            _log_warning(
                "Nominatim result for (%.5f, %.5f) is too coarse "
                "(no road/city or place_rank=%d < 20) — falling back to Google. "
                "display_name=%r",
                lat, lon,
                nominatim_result.get("place_rank", 0),
                nominatim_result.get("display_name"),
            )

    if api_key:
        _log_info("Nominatim miss or coarse result — falling back to Google Maps")
        google_result = _call_google(lat, lon, api_key, addr=addr)
        _log_info("_reverse_geocode OUT (google fallback): %s", google_result)
        return google_result

    _log_warning("_reverse_geocode OUT: no result (Nominatim empty/coarse, no Google API key)")
    return None


def _build_standardized(geo: dict[str, Any]) -> str:
    """Build a short human-readable address from geocoder components."""
    house = geo.get("house_number", "").strip()
    road = geo.get("road", "").strip()
    city = geo.get("city", "").strip()
    state = geo.get("state", "").strip()
    postcode = geo.get("postcode", "").strip()

    parts: list[str] = []
    if house and road:
        parts.append(f"{house} {road}")
    elif road:
        parts.append(road)

    city_state = ", ".join(filter(None, [city, state]))
    if postcode:
        city_state = f"{city_state} {postcode}".strip() if city_state else postcode
    if city_state:
        parts.append(city_state)

    return ", ".join(parts)


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Great-circle distance in metres between two WGS84 points.

    Both bounds of the discriminant are clamped to guard against floating-point
    rounding producing values just outside [0, 1].
    """
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
    return 2 * r * math.asin(math.sqrt(max(0.0, min(1.0, a))))


def _google_api_key() -> str:
    env_file = _PROJECT_ROOT / ".env"
    values: dict[str, str] = {}
    if env_file.exists():
        try:
            from dotenv import dotenv_values
            values = {
                str(key): str(value).strip()
                for key, value in dotenv_values(env_file).items()
                if value is not None and str(value).strip()
            }
        except Exception:
            _log_warning("Reverse geocoder could not read .env values from %s", env_file)
    for name in ("GOOGLE_GEOCODING_API_KEY", "GOOGLE_MAPS_API_KEY", "GOOGLE_API_KEY"):
        value = values.get(name) or os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def _negative_cache_suffix() -> str:
    api_key = _google_api_key()
    if not api_key:
        return "nokey"
    return hashlib.sha1(api_key.encode("utf-8")).hexdigest()[:10]


def _call_google_forward(
    address: str,
    api_key: str,
    *,
    bounds: tuple[float, float, float, float] | None = None,
    addr: Address | None = None,
) -> dict[str, Any] | None:
    """Forward-geocode a postal address string (Google Geocoding API)."""
    if not address or not address.strip():
        return None
    _log_info("_call_google_forward IN: address=%r bounds=%s", address[:200], bounds)
    request_payload: dict[str, str] = {"address": address}
    params: dict[str, str] = {**request_payload, "key": api_key}
    if bounds:
        sw_lat, sw_lon, ne_lat, ne_lon = bounds
        params["bounds"] = f"{sw_lat},{sw_lon}|{ne_lat},{ne_lon}"
        request_payload["bounds"] = params["bounds"]
    url = f"{_GOOGLE_URL}?{urllib.parse.urlencode(params)}"
    try:
        data = _http_get_json(url, timeout=12)
        if data.get("status") != "OK" or not data.get("results"):
            _log_warning(
                "Google forward geocode failed: status=%r error=%r",
                data.get("status"),
                data.get("error_message"),
            )
            return None
        parsed = _parse_google_geocode_result(data["results"][0])
        parsed["source"] = "google_forward"
        # location_type already set by _parse_google_geocode_result
        _log_info(
            "Google forward parsed: %s  (location_type=%r)",
            parsed, parsed.get("location_type"),
        )
        return parsed
    except Exception:
        _log_error("Google forward geocode failed for address=%r", address[:200], exc_info=True)
        return None


def _full_address_line(addr: Address) -> str:
    """Best-effort single-line address for forward geocoding."""
    meta = addr.raw_metadata if isinstance(addr.raw_metadata, dict) else {}
    base = _resolve_upload_address(addr, meta)
    parts: list[str] = []
    if base:
        parts.append(base)
    city_state = ", ".join(filter(None, [addr.city, addr.state]))
    if addr.zip_code:
        city_state = f"{city_state} {addr.zip_code}".strip() if city_state else str(addr.zip_code)
    if city_state and (not parts or city_state not in parts[0]):
        parts.append(city_state)
    return ", ".join(parts) if parts else ""


def _address_line_with_pin_context(
    addr: Address,
    reverse_geo: dict[str, Any] | None,
    base_line: str,
) -> str:
    """Append city/state/zip from reverse geocode when the upload line omits them."""
    if not base_line or not reverse_geo:
        return base_line
    city = (addr.city or "").strip() or (reverse_geo.get("city") or "").strip()
    state = (addr.state or "").strip() or (reverse_geo.get("state") or "").strip()
    zip_code = (addr.zip_code or "").strip() or (reverse_geo.get("postcode") or "").strip()
    tail = ", ".join(filter(None, [city, state]))
    if zip_code:
        tail = f"{tail} {zip_code}".strip() if tail else zip_code
    if not tail:
        return base_line
    lower = base_line.lower()
    if tail.lower() in lower:
        return base_line
    return f"{base_line}, {tail}"


def _forward_bounds(src_lat: float, src_lon: float, delta: float = 0.12) -> tuple[float, float, float, float]:
    """Viewport bias for forward geocode (~13 km at mid-latitudes)."""
    return (src_lat - delta, src_lon - delta, src_lat + delta, src_lon + delta)


def _house_number_from_text(text: str) -> str:
    m = re.match(r"^(\d+[A-Za-z]?)\s+", (text or "").strip())
    return m.group(1) if m else ""


_ROAD_ABBREV = {
    "nw": "northwest", "ne": "northeast", "sw": "southwest", "se": "southeast",
    " n ": " north ", " s ": " south ", " e ": " east ", " w ": " west ",
    " rd": " road", " st": " street", " ave": " avenue", " blvd": " boulevard",
    " dr": " drive", " ln": " lane", " ct": " court", " pl": " place",
    " ter": " terrace", " pkwy": " parkway", " hwy": " highway", " fwy": " freeway",
}


def _normalize_road(text: str) -> str:
    t = text.lower().strip().rstrip(",")
    for abbr, full in _ROAD_ABBREV.items():
        t = t.replace(abbr, full)
    return t


def _same_road_name(upload_line: str, geo: dict[str, Any]) -> bool:
    """True when geo's road field refers to the same street as upload_line.

    Handles common abbreviations: NW→northwest, Rd→road, Ave→avenue, etc.
    Used to detect 'right road, wrong house number' vs a completely wrong location.
    """
    road = str(geo.get("road") or "").strip()
    if not road or not upload_line:
        return False
    # Strip house number prefix from upload_line, keep only the street part.
    street_portion = re.sub(r"^\d+[A-Za-z]?\s+", "", upload_line.strip(), count=1)
    street_portion = street_portion.split(",")[0]  # drop city/state suffix if present
    road_norm = _normalize_road(road)
    street_norm = _normalize_road(street_portion)
    road_tokens = road_norm.split()
    street_tokens = set(street_norm.split())
    return bool(road_tokens) and all(tok in street_tokens for tok in road_tokens)


def _geo_house_number(geo: dict[str, Any] | None) -> str:
    if not geo:
        return ""
    return str(
        geo.get("house_number")
        or _house_number_from_text(geo.get("display_name", ""))
        or _house_number_from_text(geo.get("formatted_address", ""))
    ).upper()


def _street_line_from_geo(geo: dict[str, Any], fallback_source: str = "") -> str:
    house = (geo.get("house_number") or "").strip() or _house_number_from_text(fallback_source)
    road = (geo.get("road") or "").strip()
    if house and road:
        return f"{house} {road}"
    return road or house or fallback_source.strip()


def _address_fields_bundle(
    *,
    street_number_name: str = "",
    zip_postal_code: str = "",
    latitude: float | None = None,
    longitude: float | None = None,
    city_state: str = "",
    country_code: str = "",
) -> dict[str, Any]:
    """Standard old/new address payload stored under raw_metadata."""
    return {
        "street_number_name": (street_number_name or "").strip(),
        "zip_postal_code": (zip_postal_code or "").strip(),
        "latitude": latitude,
        "longitude": longitude,
        "city_state": (city_state or "").strip(),
        "country_code": (country_code or "").strip().upper()[:8],
    }


def _bundle_from_upload(addr: Address) -> dict[str, Any]:
    """Original values from the ingested row (before validation)."""
    src_addr = (addr.source_raw_address or addr.raw_address or "").strip()
    city_state = ", ".join(filter(None, [addr.city, addr.state]))
    lat = addr.source_latitude if addr.source_latitude is not None else addr.latitude
    lon = addr.source_longitude if addr.source_longitude is not None else addr.longitude
    meta = addr.raw_metadata or {}
    country = (meta.get("country_code") or meta.get("country") or "").strip().upper()[:8]
    return _address_fields_bundle(
        street_number_name=src_addr,
        zip_postal_code=(addr.zip_code or "").strip(),
        latitude=float(lat) if lat is not None else None,
        longitude=float(lon) if lon is not None else None,
        city_state=city_state,
        country_code=country,
    )


def _bundle_from_geo(
    geo: dict[str, Any] | None,
    lat: float,
    lon: float,
    fallback_source: str = "",
) -> dict[str, Any]:
    """Validated values from geocoder components."""
    if not geo:
        return _address_fields_bundle(
            street_number_name=fallback_source.strip(),
            latitude=lat,
            longitude=lon,
        )
    street = _street_line_from_geo(geo, fallback_source)
    city = (geo.get("city") or "").strip()
    state = (geo.get("state") or "").strip()
    return _address_fields_bundle(
        street_number_name=street,
        zip_postal_code=(geo.get("postcode") or "").strip(),
        latitude=lat,
        longitude=lon,
        city_state=", ".join(filter(None, [city, state])),
        country_code=(geo.get("country_code") or "").strip().upper()[:8],
    )


def _format_ADDRESS(bundle: dict[str, Any]) -> str:
    """Single-line validated address for raw_metadata['ADDRESS']."""
    parts: list[str] = []
    if bundle.get("street_number_name"):
        parts.append(str(bundle["street_number_name"]))
    if bundle.get("city_state"):
        parts.append(str(bundle["city_state"]))
    if bundle.get("zip_postal_code"):
        tail = str(bundle["zip_postal_code"])
        if bundle.get("country_code"):
            tail = f"{tail}, {bundle['country_code']}" if tail else str(bundle["country_code"])
        parts.append(tail)
    elif bundle.get("country_code"):
        parts.append(str(bundle["country_code"]))
    return ", ".join(filter(None, parts))


def _sync_confidence_in_raw_metadata(addr: Address, confidence: int) -> None:
    """Persist confidence on raw_metadata (top-level + address_validation when present)."""
    sync_reverse_geocode_confidence_in_raw_metadata(addr, confidence)


def _backfill_confidence_in_raw_metadata(addr: Address) -> bool:
    """
    Patch legacy rows whose address_validation block predates confidence_score.
    Returns True when raw_metadata was updated.
    """
    meta = dict(addr.raw_metadata or {})
    av = meta.get("address_validation")
    if not isinstance(av, dict):
        return False
    if av.get("confidence_score") is not None and meta.get("reverse_geocode_confidence_score") is not None:
        return False
    score = addr.reverse_geocode_confidence_score
    if score is None:
        return False
    _sync_confidence_in_raw_metadata(addr, int(score))
    return True


def _persist_address_validation_metadata(
    addr: Address,
    *,
    old: dict[str, Any],
    new: dict[str, Any],
    address_line: str,
    status: str,
    distance_m: float | None,
    notes: str,
    confidence_score: int = 0,
    location_type: str = "",
    address_match_pct: int | None = None,
    reverse_address_match_pct: int | None = None,
    forward_address_match_pct: int | None = None,
    reverse_confidence_score: int | None = None,
    selected_direction: str = "",
    selected_provider: str = "",
    selected_address_type: str = "",
    selection_reason: str = "",
) -> None:
    """Write old, new, ADDRESS, and address_validation into raw_metadata."""
    meta = dict(addr.raw_metadata or {})
    meta["old"] = old
    meta["new"] = new
    meta["ADDRESS"] = address_line or _format_ADDRESS(new)
    if location_type:
        meta["reverse_geocode_location_type"] = location_type
    meta["address_validation"] = {
        "match_status": status,
        "distance_m": distance_m,
        "notes": notes,
        "confidence_score": int(confidence_score),
        "location_type": location_type or None,
        "address_match_percent": address_match_pct,
        "reverse_address_match_percent": reverse_address_match_pct,
        "forward_address_match_percent": forward_address_match_pct,
        "selected_direction": selected_direction or None,
        "selected_provider": selected_provider or None,
        "selected_address_type": selected_address_type or None,
        "selection_reason": selection_reason or None,
    }
    if reverse_confidence_score is not None:
        meta["reverse_geocode_confidence_score"] = int(reverse_confidence_score)
    addr.raw_metadata = meta
    flag_modified(addr, "raw_metadata")
    _log_info(
        "raw_metadata address_validation persisted address_id=%s confidence_score=%s "
        "match_status=%r location_type=%r address_match_percent=%s",
        addr.id,
        confidence_score,
        status,
        location_type,
        address_match_pct,
    )


def _persist_coord_validation_api_results(
    addr: Address,
    *,
    reverse_geo: dict[str, Any] | None,
    forward_geo: dict[str, Any] | None,
    reverse_address_match_pct: int | None,
    forward_address_match_pct: int | None,
    distance_m: float | None,
    reverse_confidence: int,
    forward_confidence: int | None = None,
) -> None:
    """Store reverse / Google forward API payloads used during coord validation."""
    if reverse_geo:
        reverse_block = dict(reverse_geo)
        reverse_block["address_match_percent"] = reverse_address_match_pct
        reverse_block["coord_distance_m"] = distance_m
        reverse_block["confidence"] = reverse_confidence
        persist_provider_result(addr, "reverse_geocoding", reverse_block)
    if forward_geo:
        forward_block = dict(forward_geo)
        forward_block["coord_distance_m"] = distance_m
        forward_block["address_match_percent"] = forward_address_match_pct
        if forward_confidence is not None:
            forward_block["confidence"] = forward_confidence
        persist_provider_result(addr, "google_geocoding", forward_block)


def _apply_validated_components(
    addr: Address,
    geo: dict[str, Any] | None,
    *,
    lat: float,
    lon: float,
    full_line: str,
) -> None:
    """Write validated_* columns from a normalized geo component dict."""
    addr.validated_raw_address = full_line or None
    _set_validated_coords(addr, lat, lon)
    if not geo:
        return
    street = _street_line_from_geo(geo, addr.source_raw_address or addr.raw_address or "")
    city = (geo.get("city") or "").strip()
    state = (geo.get("state") or "").strip()
    postcode = (geo.get("postcode") or "").strip()
    country_code = (geo.get("country_code") or "").strip().upper()[:8]
    addr.validated_street_line = street or None
    addr.validated_postcode = postcode or None
    addr.validated_city_state = ", ".join(filter(None, [city, state])) or None
    addr.validated_country_code = country_code or None


def _snapshot_source_fields(addr: Address) -> None:
    """Freeze original upload values once in source_* columns."""
    if addr.source_latitude is None and addr.latitude is not None:
        addr.source_latitude = addr.latitude
    if addr.source_longitude is None and addr.longitude is not None:
        addr.source_longitude = addr.longitude
    if addr.source_raw_address is None and addr.raw_address:
        addr.source_raw_address = addr.raw_address


def _set_validated_coords(addr: Address, lat: float, lon: float) -> None:
    addr.validated_latitude = lat
    addr.validated_longitude = lon
    set_address_geom(addr, lon, lat)


def _rooftop_full_match(geo: dict[str, Any] | None, match_pct: int) -> bool:
    if not geo:
        return False
    loc = str(geo.get("location_type") or _reverse_geo_location_type(geo) or "").upper()
    return loc == "ROOFTOP" and int(match_pct) >= _ADDRESS_MATCH_REQUIRED


_ADDRESS_TYPE_QUALITY = {
    "street_address": 100,
    "premise": 95,
    "subpremise": 92,
    "house": 90,
    "residential": 88,
    "building": 85,
    "establishment": 75,
    "point_of_interest": 70,
    "route": 45,
    "road": 45,
    "intersection": 40,
    "postal_code": 25,
    "neighborhood": 20,
    "locality": 15,
    "administrative_area_level_1": 10,
    "administrative_area_level_2": 10,
    "country": 5,
}
_LOCATION_TYPE_QUALITY = {
    "ROOFTOP": 100,
    "RANGE_INTERPOLATED": 80,
    "GEOMETRIC_CENTER": 55,
    "APPROXIMATE": 30,
}


def _geo_address_types(geo: dict[str, Any] | None) -> list[str]:
    if not geo:
        return []
    raw = geo.get("address_types") or geo.get("types") or geo.get("address_type") or []
    if isinstance(raw, str):
        raw = [raw]
    return [str(value).strip().lower() for value in raw if str(value).strip()]


def _best_geo_address_type(geo: dict[str, Any] | None) -> str:
    types = _geo_address_types(geo)
    if not types:
        return ""
    return max(types, key=lambda value: _ADDRESS_TYPE_QUALITY.get(value, 0))


def _match_geo_rank(
    upload_line: str,
    geo: dict[str, Any],
    match_pct: int | None,
    *,
    direction: str,
) -> tuple[int, int, int, int, int, int]:
    input_house = _house_number_from_text(upload_line or "").upper()
    geo_house = _geo_house_number(geo).upper()
    exact_house = int(bool(input_house and geo_house and input_house == geo_house))
    address_type = _best_geo_address_type(geo)
    address_type_quality = _ADDRESS_TYPE_QUALITY.get(address_type, 0)
    location_type = str(geo.get("location_type") or _reverse_geo_location_type(geo) or "").upper()
    location_quality = _LOCATION_TYPE_QUALITY.get(location_type, 0)
    complete_address = int(bool(geo_house and str(geo.get("road") or "").strip()))
    # Forward wins only a final tie for postal-address rows. KMZ/coordinate rows
    # bypass this ranking and remain reverse-driven.
    direction_tiebreak = int(direction == "forward")
    return (
        exact_house,
        int(match_pct or 0),
        address_type_quality,
        complete_address,
        location_quality,
        direction_tiebreak,
    )


def _select_match_geo(
    upload_line: str,
    *,
    reverse_geo: dict[str, Any] | None,
    forward_geo: dict[str, Any] | None,
    reverse_match_pct: int | None,
    forward_match_pct: int | None,
    coordinate_driven: bool = False,
) -> tuple[dict[str, Any] | None, str, str]:
    """Choose the correct MATCH result while retaining both provider payloads."""
    if coordinate_driven:
        direction = "reverse" if reverse_geo else "forward"
        return (
            reverse_geo or forward_geo,
            direction,
            "coordinate/KMZ input prefers reverse result",
        )
    if not reverse_geo:
        return forward_geo, "forward", "reverse result unavailable"
    if not forward_geo:
        return reverse_geo, "reverse", "forward result unavailable"

    reverse_rank = _match_geo_rank(upload_line, reverse_geo, reverse_match_pct, direction="reverse")
    forward_rank = _match_geo_rank(upload_line, forward_geo, forward_match_pct, direction="forward")
    if reverse_rank > forward_rank:
        winner, direction = reverse_geo, "reverse"
    else:
        winner, direction = forward_geo, "forward"
    reason = (
        f"{direction} selected by house/text match, address type, and precision "
        f"(reverse={reverse_rank[:-1]}, forward={forward_rank[:-1]})"
    )
    return winner, direction, reason


def _selected_provider(direction: str, geo: dict[str, Any] | None) -> str:
    source = str((geo or {}).get("source") or "").strip().upper()
    if direction == "forward":
        return "GOOGLE_FORWARD" if source in {"", "GOOGLE", "GOOGLE_FORWARD"} else f"{source}_FORWARD"
    if source == "GOOGLE":
        return "GOOGLE_REVERSE"
    if source == "NOMINATIM":
        return "NOMINATIM_REVERSE"
    return source or "REVERSE_GEOCODER"


def _reverse_precise_house_number_match(
    upload_line: str,
    geo: dict[str, Any] | None,
    *,
    distance_m: float | None = None,
) -> bool:
    if not upload_line or not geo:
        return False
    loc = str(geo.get("location_type") or _reverse_geo_location_type(geo) or "").upper()
    input_hn = _house_number_from_text(upload_line).upper()
    reverse_hn = _geo_house_number(geo)
    if not (input_hn and reverse_hn and input_hn == reverse_hn):
        return False
    if loc == "ROOFTOP":
        return True
    return distance_m is not None and distance_m <= _MATCH_THRESHOLD_M


def _refine_coord_match_status(
    status: str,
    *,
    forward: dict[str, Any] | None,
    reverse_geo: dict[str, Any] | None,
    match_scores: dict[str, int],
    confidence_score: int,
    notes: str,
) -> tuple[str, str]:
    """
    Downgrade distance-only MATCH when text, ROOFTOP, reverse-at-pin, or confidence
    do not meet the same bar used for high geocoder confidence.
    """
    if status != "MATCH":
        return status, notes

    reasons: list[str] = []
    forward_rooftop_match = bool(forward and _rooftop_full_match(forward, match_scores["forward"]))
    reverse_rooftop_match = bool(reverse_geo and _rooftop_full_match(reverse_geo, match_scores["reverse"]))
    reverse_full_match = bool(reverse_geo and match_scores["reverse"] >= _ADDRESS_MATCH_REQUIRED)

    if reverse_geo and match_scores["reverse"] < _ADDRESS_MATCH_REQUIRED:
        if not forward_rooftop_match:
            reasons.append(
                f"reverse at pin {match_scores['reverse']}% match "
                f"(requires {_ADDRESS_MATCH_REQUIRED}%)"
            )

    if forward and not forward_rooftop_match and not reverse_rooftop_match and not reverse_full_match:
        forward_loc = str(forward.get("location_type") or "").upper() or "n/a"
        reasons.append(
            f"forward location_type={forward_loc} with {match_scores['forward']}% text match "
            f"(requires ROOFTOP+{_ADDRESS_MATCH_REQUIRED}%)"
        )

    if confidence_score <= _LOW_MATCH_CONFIDENCE_CAP:
        reasons.append(
            f"validation confidence {confidence_score} "
            f"(requires >{_LOW_MATCH_CONFIDENCE_CAP} for MATCH)"
        )

    if not reasons:
        return status, notes

    refine_note = "; ".join(reasons)
    return "MISMATCH_WARN", f"{notes} Quality check: {refine_note}."


def _validate_address_coords(
    addr: Address,
    *,
    reverse_geo: dict[str, Any] | None = None,
    allow_reverse_fallback: bool = True,
) -> dict[str, Any]:
    """
    Compare source address vs source coordinates; populate validated_* columns.

    Match statuses:
      MATCH             — forward geocode agrees with pin within threshold AND ROOFTOP+100%
                          text match on forward and reverse-at-pin (when available), with
                          validation confidence above the low cap
      MISMATCH_WARN     — coords/text partially agree, or distance-only match without ROOFTOP
      MISMATCH          — disagrees beyond both thresholds (hard flag)
      COORDS_VALIDATED  — no address or KMZ label; pin used as authoritative source
      ADDRESS_MISMATCH — uploaded address text does not match reverse/forward geocoder (requires 100%)
      UNVERIFIED        — geocoding unavailable; source values kept as-is
      NO_COORDS         — row has no latitude/longitude

    Returns a small result dict for logging / summary.
    """
    meta = addr.raw_metadata or {}
    lat = addr.latitude
    lon = addr.longitude
    if not lat or not lon or lat == 0 or lon == 0:
        addr.coord_address_match_status = "NO_COORDS"
        addr.coord_address_validation_notes = "Missing latitude/longitude"
        _persist_address_validation_metadata(
            addr,
            old=_bundle_from_upload(addr),
            new=_address_fields_bundle(),
            address_line="",
            status="NO_COORDS",
            distance_m=None,
            notes="Missing latitude/longitude",
            confidence_score=0,
        )
        return {"status": "NO_COORDS", "distance_m": None, "geo": None, "confidence_score": 0}

    _snapshot_source_fields(addr)
    src_lat, src_lon = float(lat), float(lon)
    src_addr = addr.source_raw_address or addr.raw_address or ""
    kmz_label = is_kmz_placemark_label(src_addr, meta) or is_kmz_placemark_label(addr.raw_address, meta)

    if reverse_geo is None and allow_reverse_fallback:
        reverse_geo = _reverse_geocode(src_lat, src_lon, addr=addr)
    reverse_std = _build_standardized(reverse_geo) if reverse_geo else ""
    reverse_display = (reverse_geo or {}).get("display_name", "") or reverse_std

    address_line = _full_address_line(addr)
    if address_line and reverse_geo:
        address_line = _address_line_with_pin_context(addr, reverse_geo, address_line)
    forward: dict[str, Any] | None = None
    distance_m: float | None = None
    api_key = _google_api_key()
    pin_bounds = _forward_bounds(src_lat, src_lon)

    if address_line and not kmz_label and api_key:
        forward = _call_google_forward(address_line, api_key, bounds=pin_bounds, addr=addr)
        if not forward and reverse_std and reverse_std not in address_line:
            forward = _call_google_forward(
                f"{address_line}, {reverse_std}",
                api_key,
                bounds=pin_bounds,
                addr=addr,
            )
        if forward and forward.get("latitude") is not None:
            distance_m = _haversine_m(src_lat, src_lon, forward["latitude"], forward["longitude"])

    component_geo: dict[str, Any] | None = None

    # ── Decide match status and validated values ─────────────────────────────
    if kmz_label or not address_line:
        status = "COORDS_VALIDATED"
        val_addr = reverse_std or reverse_display or src_addr
        val_lat, val_lon = src_lat, src_lon
        component_geo = reverse_geo
        notes = (
            f"KMZ/placemark or no postal address; reverse-geocoded at ({src_lat:.5f},{src_lon:.5f}). "
            f"Reverse: {reverse_display or 'n/a'}"
        )
    elif forward and distance_m is not None and distance_m <= _MATCH_THRESHOLD_M:
        status = "MATCH"
        val_addr = forward.get("display_name") or address_line
        val_lat = float(forward["latitude"])
        val_lon = float(forward["longitude"])
        component_geo = forward
        notes = f"Address and coordinates agree within {distance_m:.1f}m (threshold {_MATCH_THRESHOLD_M}m)."
    elif forward and distance_m is not None and distance_m <= _MISMATCH_WARN_THRESHOLD_M:
        status = "MISMATCH_WARN"
        fwd_display = forward.get("display_name") or address_line
        if reverse_geo:
            val_addr = reverse_display or reverse_std or address_line
            val_lat, val_lon = src_lat, src_lon
            component_geo = reverse_geo
            notes = (
                f"Forward geocode ({fwd_display}) is {distance_m:.1f}m from pin "
                f"(>{_MATCH_THRESHOLD_M}m but <={_MISMATCH_WARN_THRESHOLD_M}m soft threshold); "
                f"validated from reverse geocode at pin. Reverse: {reverse_display or 'n/a'}"
            )
        else:
            val_addr = fwd_display or address_line
            val_lat = float(forward["latitude"])
            val_lon = float(forward["longitude"])
            component_geo = forward
            notes = (
                f"Address vs stored coordinates differ by {distance_m:.1f}m "
                f"(>{_MATCH_THRESHOLD_M}m but <={_MISMATCH_WARN_THRESHOLD_M}m soft threshold); "
                f"no reverse geocode at pin."
            )
    elif forward and distance_m is not None:
        status = "MISMATCH"
        fwd_display = forward.get("display_name") or address_line
        if reverse_geo:
            val_addr = reverse_display or reverse_std or address_line
            val_lat, val_lon = src_lat, src_lon
            component_geo = reverse_geo
            notes = (
                f"Forward geocode ({fwd_display}) is {distance_m:.1f}m from pin "
                f"(threshold {_MATCH_THRESHOLD_M}m); validated from reverse geocode at pin. "
                f"Reverse: {reverse_display or 'n/a'}"
            )
        else:
            val_addr = fwd_display or address_line
            val_lat = float(forward["latitude"])
            val_lon = float(forward["longitude"])
            component_geo = forward
            notes = (
                f"Address vs stored coordinates differ by {distance_m:.1f}m (threshold {_MATCH_THRESHOLD_M}m). "
                f"No reverse geocode at pin; kept forward result."
            )
    elif reverse_geo:
        status = "COORDS_VALIDATED"
        val_addr = reverse_std or reverse_display or address_line
        val_lat, val_lon = src_lat, src_lon
        component_geo = reverse_geo
        notes = "Forward geocode unavailable; validated from reverse geocode at stored coordinates."
    else:
        status = "UNVERIFIED"
        val_addr = address_line or src_addr
        val_lat, val_lon = src_lat, src_lon
        component_geo = None
        notes = "Could not forward- or reverse-geocode; kept source values in validated columns."

    upload_line = _resolve_upload_address(addr, meta)
    match_scores = {"reverse": 100, "forward": 100, "best": 100}
    if upload_line:
        match_scores = _address_match_scores(
            upload_line,
            reverse_geo=reverse_geo,
            forward_geo=forward,
            addr=addr,
            coord_distance_m=distance_m,
        )

        raw_hn = _house_number_from_text(upload_line).upper()
        forced_match = False
        if raw_hn:
            if forward:
                fwd_hn = _geo_house_number(forward)
                if fwd_hn == raw_hn:
                    match_scores["forward"] = 100
                    match_scores["best"] = max(match_scores["best"], 100)
                    forced_match = True
            if reverse_geo:
                rev_hn = _geo_house_number(reverse_geo)
                if rev_hn == raw_hn:
                    match_scores["reverse"] = 100
                    match_scores["best"] = max(match_scores["best"], 100)
                    forced_match = True

        if _reverse_precise_house_number_match(upload_line, reverse_geo, distance_m=distance_m):
            match_scores["reverse"] = 100
            match_scores["best"] = max(match_scores["best"], 100)
            forced_match = True

        if forced_match:
            status = "MATCH"

        if match_scores["best"] < _ADDRESS_MATCH_REQUIRED:
            status = "ADDRESS_MISMATCH"
            val_addr = upload_line
            val_lat, val_lon = src_lat, src_lon
            component_geo = reverse_geo
            notes = (
                f"Uploaded address text match {match_scores['best']}% "
                f"(requires {_ADDRESS_MATCH_REQUIRED}%). "
                f"Reverse at pin: {reverse_display or 'n/a'}"
            )
    address_match_pct = match_scores["best"]

    reverse_location_type = _reverse_geo_location_type(reverse_geo) if reverse_geo else ""
    reverse_confidence = 0
    if reverse_geo:
        reverse_confidence = _compute_confidence(
            reverse_geo,
            distance_m=distance_m,
            match_status=status,
            address_match_percent=match_scores["reverse"],
        )
        reverse_confidence = _finalize_reverse_confidence(
            reverse_confidence,
            location_type=reverse_location_type,
            reverse_address_match_percent=match_scores["reverse"],
        )
        # House-number override: finalize_geocoder_confidence caps non-ROOFTOP
        # results at 35 even when address text matches 100%.  Rural addresses are
        # rarely ROOFTOP, so we apply a direct override: if the reverse geocoder
        # returns the same house number as the raw input address → confidence = 100.
        _input_hn = _house_number_from_text(upload_line or "").upper() if upload_line else ""
        _rev_hn = _geo_house_number(reverse_geo)
        if _reverse_precise_house_number_match(upload_line or "", reverse_geo, distance_m=distance_m):
            reverse_confidence = 100
            _log_debug(
                "reverse confidence overridden to 100: precise house-number match input_hn=%r rev_hn=%r loc_type=%r distance_m=%s",
                _input_hn, _rev_hn, reverse_location_type,
                distance_m,
            )
        elif (
            not forward
            and reverse_location_type.upper() == "ROOFTOP"
            and upload_line
            and _same_road_name(upload_line, reverse_geo)
        ):
            # Forward geocoding failed completely (address not in Google/OSM database)
            # but the reverse geocode landed on the correct road at ROOFTOP precision.
            # This is typical for rural addresses whose house numbers are not registered
            # in Google's database — the road is confirmed, only the exact parcel
            # number is unverifiable from public geocoders.
            # Boost to 75 (above MANUAL_REVIEW floor) to signal: road confirmed,
            # house number source is the uploaded coordinates, not geocoder text.
            reverse_confidence = max(reverse_confidence, 75)
            _log_debug(
                "reverse confidence boosted to 75: forward failed, ROOFTOP on same road "
                "input_hn=%r rev_hn=%r road=%r loc_type=%r",
                _input_hn, _rev_hn, reverse_geo.get("road"), reverse_location_type,
            )

    if forward and match_scores["forward"] >= _ADDRESS_MATCH_REQUIRED:
        raw_forward_confidence = _compute_confidence(
            forward,
            distance_m=distance_m,
            match_status=status,
            address_match_percent=match_scores["forward"],
        )
        confidence_score = int(
            finalize_geocoder_confidence(
                raw_forward_confidence,
                location_type=str(forward.get("location_type") or ""),
                address_match_percent=match_scores["forward"],
            )
        )
    elif reverse_geo:
        confidence_score = reverse_confidence
    else:
        confidence_score = 0

    status, notes = _refine_coord_match_status(
        status,
        forward=forward,
        reverse_geo=reverse_geo,
        match_scores=match_scores,
        confidence_score=confidence_score,
        notes=notes,
    )
    if status == "MATCH":
        reverse_confidence = 100
        confidence_score = 100
        _log_debug(
            "reverse and A1/A2 confidence overridden to 100: final coord/address status is MATCH"
        )

    selected_direction = ""
    selection_reason = f"{status} validation decision"
    if status == "MATCH":
        component_geo, selected_direction, selection_reason = _select_match_geo(
            upload_line or address_line,
            reverse_geo=reverse_geo,
            forward_geo=forward,
            reverse_match_pct=match_scores["reverse"],
            forward_match_pct=match_scores["forward"],
            coordinate_driven=bool(kmz_label or not address_line),
        )
        if selected_direction == "reverse" and component_geo:
            val_addr = _build_standardized(component_geo) or component_geo.get("display_name") or address_line
            val_lat, val_lon = src_lat, src_lon
        elif selected_direction == "forward" and component_geo:
            val_addr = component_geo.get("display_name") or address_line
            val_lat = float(component_geo.get("latitude") or src_lat)
            val_lon = float(component_geo.get("longitude") or src_lon)
        notes = f"{notes} Selected {selected_direction}: {selection_reason}."
    elif status == "ADDRESS_MISMATCH":
        selected_direction = "source"
        selection_reason = "uploaded address retained because neither geocoder matched"
    elif component_geo is forward:
        selected_direction = "forward"
    elif component_geo is reverse_geo or reverse_geo is not None:
        selected_direction = "reverse"
    else:
        selected_direction = "source"

    selected_provider = (
        "UPLOAD"
        if selected_direction == "source"
        else _selected_provider(selected_direction, component_geo)
    )
    selected_address_type = _best_geo_address_type(component_geo)
    location_type = _reverse_geo_location_type(component_geo or reverse_geo or forward)

    old_bundle = _bundle_from_upload(addr)
    if status == "ADDRESS_MISMATCH":
        city_state = ", ".join(filter(None, [addr.city, addr.state]))
        new_bundle = _address_fields_bundle(
            street_number_name=upload_line,
            zip_postal_code=(addr.zip_code or "").strip(),
            latitude=val_lat,
            longitude=val_lon,
            city_state=city_state,
            country_code=(meta.get("country_code") or meta.get("country") or ""),
        )
    else:
        new_bundle = _bundle_from_geo(
            component_geo,
            val_lat,
            val_lon,
            fallback_source=src_addr,
        )
    final_address = val_addr or _format_ADDRESS(new_bundle)

    address_match_pct = match_scores["best"]
    addr.reverse_geocode_confidence_score = reverse_confidence

    _persist_address_validation_metadata(
        addr,
        old=old_bundle,
        new=new_bundle,
        address_line=final_address,
        status=status,
        distance_m=distance_m,
        notes=notes,
        confidence_score=confidence_score,
        location_type=location_type,
        address_match_pct=match_scores["best"],
        reverse_address_match_pct=match_scores["reverse"],
        forward_address_match_pct=match_scores["forward"],
        reverse_confidence_score=reverse_confidence,
        selected_direction=selected_direction,
        selected_provider=selected_provider,
        selected_address_type=selected_address_type,
        selection_reason=selection_reason,
    )
    forward_confidence = (
        confidence_score
        if forward and match_scores["forward"] >= _ADDRESS_MATCH_REQUIRED
        else None
    )
    _persist_coord_validation_api_results(
        addr,
        reverse_geo=reverse_geo,
        forward_geo=forward,
        reverse_address_match_pct=match_scores["reverse"],
        forward_address_match_pct=match_scores["forward"],
        distance_m=distance_m,
        reverse_confidence=reverse_confidence,
        forward_confidence=forward_confidence,
    )
    if status == "ADDRESS_MISMATCH":
        _apply_validated_components(addr, None, lat=val_lat, lon=val_lon, full_line=upload_line)
        addr.validated_street_line = upload_line or None
    else:
        _apply_validated_components(addr, component_geo, lat=val_lat, lon=val_lon, full_line=final_address)
    addr.coord_address_match_status = status
    addr.coord_address_distance_m = distance_m
    addr.coord_address_validation_notes = notes

    _log_info(
        "coord/address validation address_id=%s status=%s confidence_score=%s ADDRESS=%r "
        "old=%s new=%s notes=%s",
        addr.id,
        status,
        confidence_score,
        final_address,
        old_bundle,
        new_bundle,
        notes,
    )
    return {
        "status": status,
        "distance_m": distance_m,
        "geo": component_geo or reverse_geo,
        "reverse_geo": reverse_geo,
        "confidence_score": confidence_score,
        "reverse_confidence_score": reverse_confidence,
        "address_match_percent": address_match_pct,
        "reverse_address_match_percent": match_scores["reverse"],
        "forward_address_match_percent": match_scores["forward"],
        "location_type": location_type,
        "source_address": src_addr,
        "validated_address": final_address,
        "old": old_bundle,
        "new": new_bundle,
        "ADDRESS": final_address,
    }


def _empty_coord_validation_summary(total: int = 0) -> dict[str, int]:
    return {
        "total": total,
        "matched": 0,
        "mismatch": 0,
        "mismatch_warn": 0,
        "coords_validated": 0,
        "address_mismatch": 0,
        "unverified": 0,
        "no_coords": 0,
    }


def _run_coord_validation_chunk(
    job_id: str,
    address_ids: list[int],
    *,
    progress_callback=None,
    allow_reverse_fallback: bool = True,
) -> dict[str, int]:
    session = get_session_factory()()
    summary = _empty_coord_validation_summary()
    try:
        rows = session.scalars(
            _sel(Address).where(Address.id.in_(address_ids)).order_by(Address.id)
        ).all()
        total = len(rows)
        summary["total"] = total

        for idx, addr in enumerate(rows, 1):
            _backfill_confidence_in_raw_metadata(addr)
            result = _validate_address_coords(addr, allow_reverse_fallback=allow_reverse_fallback)
            reverse_score = result.get("reverse_confidence_score")
            if reverse_score is not None:
                _sync_confidence_in_raw_metadata(addr, int(reverse_score))
            st = result["status"]
            if st == "MATCH":
                summary["matched"] += 1
            elif st == "MISMATCH":
                summary["mismatch"] += 1
            elif st == "MISMATCH_WARN":
                summary["mismatch_warn"] += 1
            elif st == "ADDRESS_MISMATCH":
                summary["address_mismatch"] += 1
            elif st == "COORDS_VALIDATED":
                summary["coords_validated"] += 1
            elif st == "NO_COORDS":
                summary["no_coords"] += 1
            else:
                summary["unverified"] += 1
            session.flush()
            if progress_callback:
                progress_callback(idx, total)

        session.commit()
        return summary
    except Exception:
        session.rollback()
        _log_error("coord validation chunk FAILED job_id=%r ids=%s", job_id, address_ids, exc_info=True)
        raise
    finally:
        session.close()


def _run_coord_validation_chunks_parallel(
    job_id: str,
    *,
    address_ids: list[int],
    progress_callback=None,
    allow_reverse_fallback: bool = True,
) -> dict[str, int]:
    batch_size = _agent2_batch_size()
    workers = _agent2_worker_count("validation")
    chunks = _chunks(address_ids, batch_size)
    summary = _empty_coord_validation_summary()
    done = 0
    progress_lock = threading.Lock()

    _log_info(
        "Coord validation parallel mode: workers=%d batch_size=%d chunks=%d records=%d",
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
        progress_callback(current, len(address_ids))

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="agent2-validation") as executor:
        futures = [
            executor.submit(
                _run_coord_validation_chunk,
                job_id,
                chunk,
                progress_callback=_chunk_progress,
                allow_reverse_fallback=allow_reverse_fallback,
            )
            for chunk in chunks
        ]
        for future in as_completed(futures):
            _merge_int_summaries(summary, future.result())

    summary["total"] = len(address_ids)
    return summary


def _run_address_coord_validation_for_job_locked(
    job_id: str,
    address_ids: list[int],
    progress_callback=None,
    allow_reverse_fallback: bool = True,
    match_threshold_m: float | None = None,
    mismatch_warn_threshold_m: float | None = None,
) -> dict:
    """
    Compare raw_address vs latitude/longitude for each row; write source_* and validated_* columns.
    """
    _configure_file_logging()
    ensure_address_validation_columns()
    global _MATCH_THRESHOLD_M, _MISMATCH_WARN_THRESHOLD_M
    old_match_threshold = _MATCH_THRESHOLD_M
    old_warn_threshold = _MISMATCH_WARN_THRESHOLD_M
    if match_threshold_m is not None:
        try:
            _MATCH_THRESHOLD_M = max(1.0, float(match_threshold_m))
        except (TypeError, ValueError):
            _MATCH_THRESHOLD_M = old_match_threshold
    if mismatch_warn_threshold_m is not None:
        try:
            _MISMATCH_WARN_THRESHOLD_M = max(_MATCH_THRESHOLD_M, float(mismatch_warn_threshold_m))
        except (TypeError, ValueError):
            _MISMATCH_WARN_THRESHOLD_M = old_warn_threshold
    _log_info("=" * 72)
    _log_info(
        "reverse_geocoder loaded from %s (raw_metadata schema v%s)",
        Path(__file__).resolve(),
        _RAW_METADATA_SCHEMA_VERSION,
    )
    _log_info(
        "run_address_coord_validation_for_job IN: job_id=%r address_count=%d threshold_m=%s "
        "mismatch_warn_m=%s",
        job_id,
        len(address_ids),
        _MATCH_THRESHOLD_M,
        _MISMATCH_WARN_THRESHOLD_M,
    )
    if not address_ids:
        summary = {
            "total": 0,
            "matched": 0,
            "mismatch": 0,
            "mismatch_warn": 0,
            "coords_validated": 0,
            "address_mismatch": 0,
            "unverified": 0,
            "no_coords": 0,
        }
        _MATCH_THRESHOLD_M = old_match_threshold
        _MISMATCH_WARN_THRESHOLD_M = old_warn_threshold
        return summary

    batch_size = _agent2_batch_size()
    workers = _agent2_worker_count("validation")
    if _agent2_parallel_enabled(len(address_ids), workers, batch_size):
        try:
            summary = _run_coord_validation_chunks_parallel(
                job_id,
                address_ids=address_ids,
                progress_callback=progress_callback,
                allow_reverse_fallback=allow_reverse_fallback,
            )
            _log_info("run_address_coord_validation_for_job OUT: %s", summary)
            _log_info("=" * 72)
            return summary
        finally:
            _MATCH_THRESHOLD_M = old_match_threshold
            _MISMATCH_WARN_THRESHOLD_M = old_warn_threshold

    session = get_session_factory()()
    summary = {
        "total": 0,
        "matched": 0,
        "mismatch": 0,
        "mismatch_warn": 0,
        "coords_validated": 0,
        "address_mismatch": 0,
        "unverified": 0,
        "no_coords": 0,
    }
    try:
        rows = session.scalars(
            _sel(Address).where(Address.id.in_(address_ids)).order_by(Address.id)
        ).all()
        total = len(rows)
        summary["total"] = total

        for idx, addr in enumerate(rows, 1):
            _backfill_confidence_in_raw_metadata(addr)
            result = _validate_address_coords(addr, allow_reverse_fallback=allow_reverse_fallback)
            # _validate_address_coords already sets reverse_geocode_confidence_score and
            # address_validation.confidence_score. Only mirror reverse-only score to metadata.
            reverse_score = result.get("reverse_confidence_score")
            if reverse_score is not None:
                _sync_confidence_in_raw_metadata(addr, int(reverse_score))
            st = result["status"]
            if st == "MATCH":
                summary["matched"] += 1
            elif st == "MISMATCH":
                summary["mismatch"] += 1
            elif st == "MISMATCH_WARN":
                summary["mismatch_warn"] += 1
            elif st == "ADDRESS_MISMATCH":
                summary["address_mismatch"] += 1
            elif st == "COORDS_VALIDATED":
                summary["coords_validated"] += 1
            elif st == "NO_COORDS":
                summary["no_coords"] += 1
            else:
                summary["unverified"] += 1
            session.flush()
            if progress_callback:
                progress_callback(idx, total)

        session.commit()
        _log_info("run_address_coord_validation_for_job OUT: %s", summary)
        _log_info("=" * 72)
        return summary
    except Exception:
        session.rollback()
        _log_error("run_address_coord_validation_for_job FAILED job_id=%r", job_id, exc_info=True)
        raise
    finally:
        _MATCH_THRESHOLD_M = old_match_threshold
        _MISMATCH_WARN_THRESHOLD_M = old_warn_threshold
        session.close()


# ── Main entry point ────────────────────────────────────────────────────────────

def run_address_coord_validation_for_job(
    job_id: str,
    address_ids: list[int],
    progress_callback=None,
    allow_reverse_fallback: bool = True,
    match_threshold_m: float | None = None,
    mismatch_warn_threshold_m: float | None = None,
) -> dict:
    """Thread-safe public wrapper for coordinate/address validation."""
    with _validation_threshold_lock:
        return _run_address_coord_validation_for_job_locked(
            job_id,
            address_ids,
            progress_callback=progress_callback,
            allow_reverse_fallback=allow_reverse_fallback,
            match_threshold_m=match_threshold_m,
            mismatch_warn_threshold_m=mismatch_warn_threshold_m,
        )


def _run_reverse_chunks_parallel(
    job_id: str,
    *,
    address_ids: list[int],
    progress_callback=None,
    total: int | None = None,
) -> dict[str, int]:
    batch_size = _agent2_batch_size()
    workers = _agent2_worker_count("reverse")
    chunks = _chunks(address_ids, batch_size)
    summary = {"total": 0, "geocoded": 0, "failed": 0, "cache_hits": 0}
    done = 0
    progress_lock = threading.Lock()

    _log_info(
        "Reverse geocoder parallel mode: workers=%d batch_size=%d chunks=%d records=%d",
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

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="agent2-reverse") as executor:
        futures = [
            executor.submit(
                run_reverse_geocoder_for_job,
                job_id,
                chunk,
                progress_callback=_chunk_progress,
            )
            for chunk in chunks
        ]
        for future in as_completed(futures):
            _merge_int_summaries(summary, future.result())

    summary["total"] = len(address_ids)
    return summary


def run_reverse_geocoder_for_job(
    job_id: str,
    address_ids: list[int],
    progress_callback=None,
) -> dict:
    """
    Reverse-geocode coords-only address rows for the given job.
    Upserts results into agent1_results; backfills addresses fields.
    Returns summary dict.

    Cache strategy (updated):
      Positive cache (30 days): results that pass _nominatim_result_is_reliable()
        or come from Google. place_rank, importance, location_type are stored in
        the cached dict so confidence scoring is accurate on cache hits.
      Negative miss cache (1 hour): coordinates that returned no usable result from
        any provider. Prevents repeated API calls for bad coords while allowing retry.
    """
    _configure_file_logging()
    ensure_address_validation_columns()
    _log_info("=" * 72)
    _log_info("log file path: %s", _LOG_FILE.resolve())
    _log_info(
        "run_reverse_geocoder_for_job IN: job_id=%r address_count=%d address_ids=%s progress_callback=%s",
        job_id,
        len(address_ids),
        address_ids,
        progress_callback is not None,
    )
    if not address_ids:
        _log_info("no address_ids — returning empty summary")
        return {"total": 0, "geocoded": 0, "failed": 0, "cache_hits": 0}

    session = get_session_factory()()
    cache = _get_cache()
    summary = {"total": len(address_ids), "geocoded": 0, "failed": 0, "cache_hits": 0}
    _log_info(
        "Redis cache namespace=ftth:rgc google_key_set=%s",
        bool(_google_api_key()),
    )

    try:
        addresses = session.scalars(
            _sel(Address).where(Address.id.in_(address_ids)).order_by(Address.id)
        ).all()

        total = len(addresses)
        _log_info("loaded %d Address rows from DB (requested %d ids)", total, len(address_ids))
        if total != len(address_ids):
            missing = set(address_ids) - {a.id for a in addresses}
            _log_warning("address_ids not found in DB: %s", sorted(missing))
        for a in addresses:
            _log_info(
                "  Address id=%s lat=%r lon=%r raw_address=%r city=%r state=%r zip=%r",
                a.id, a.latitude, a.longitude, a.raw_address, a.city, a.state, a.zip_code,
            )

        batch_size = _agent2_batch_size()
        workers = _agent2_worker_count("reverse")
        if _agent2_parallel_enabled(total, workers, batch_size):
            sorted_ids = [a.id for a in addresses]
            session.close()
            parallel_summary = _run_reverse_chunks_parallel(
                job_id,
                address_ids=sorted_ids,
                progress_callback=progress_callback,
                total=total,
            )
            _log_info("run_reverse_geocoder_for_job OUT: summary=%s", parallel_summary)
            _log_info("=" * 72)
            return parallel_summary

        for idx, addr in enumerate(addresses, 1):
            lat = addr.latitude
            lon = addr.longitude
            cache_key = f"{lat:.5f},{lon:.5f}"
            miss_key = f"{cache_key}:miss:{_negative_cache_suffix()}"
            _log_info("[%d/%d] address_id=%s cache_key=%r", idx, total, addr.id, cache_key)
            now = datetime.utcnow()

            # ── Cache lookup ──────────────────────────────────────────────
            with _cache_lock:
                geo = cache.get(cache_key)
            if geo and not _cached_geo_matches_query(geo, lat, lon):
                _log_warning(
                    "CACHE REJECT key=%r: cached result coordinates (%r,%r) are more than %.0fm from query; deleting",
                    cache_key,
                    geo.get("latitude"),
                    geo.get("longitude"),
                    _CACHE_RESULT_MAX_DISTANCE_M,
                )
                with _cache_lock:
                    cache.delete(cache_key)
                geo = None
            if geo:
                summary["cache_hits"] += 1
                _log_info(
                    "CACHE HIT key=%r source=%r location_type=%r place_rank=%s",
                    cache_key, geo.get("source"), geo.get("location_type"), geo.get("place_rank"),
                )
                location_type = _reverse_geo_location_type(geo)
                if location_type:
                    meta = dict(addr.raw_metadata or {})
                    meta["reverse_geocode_location_type"] = location_type
                    addr.raw_metadata = meta
                    flag_modified(addr, "raw_metadata")
            else:
                with _cache_lock:
                    miss_cached = cache.get(miss_key)
                if miss_cached:
                    _log_info("CACHE MISS-HIT (known-bad) key=%r — skipping API call", cache_key)
                    geo = None
                else:
                    _log_info("CACHE MISS key=%r — calling _reverse_geocode", cache_key)
                    geo = _reverse_geocode(lat, lon, addr=addr)
                    _log_info("geocode result for address_id=%s: %s", addr.id, geo)
                    if geo:
                        location_type = _reverse_geo_location_type(geo)
                        meta = dict(addr.raw_metadata or {})
                        meta["reverse_geocode_location_type"] = location_type
                        addr.raw_metadata = meta
                        flag_modified(addr, "raw_metadata")
                        # Only cache results that are reliable enough to reuse.
                        # place_rank, importance, location_type are already in the dict.
                        if _nominatim_result_is_reliable(geo) or geo.get("source") in ("google", "google_forward"):
                            with _cache_lock:
                                cache.set(cache_key, geo)
                            _log_debug("CACHE SET key=%r", cache_key)
                        else:
                            _log_warning(
                                "Not caching low-quality result for key=%r source=%r place_rank=%s",
                                cache_key, geo.get("source"), geo.get("place_rank"),
                            )
                    else:
                        # Short-lived miss entry so the next job can retry after 1 hour.
                        with _cache_lock:
                            cache.set(miss_key, {"miss": True}, ttl=_CACHE_MISS_TTL)
                        _log_debug("CACHE MISS-SET key=%r ttl=%ds", miss_key, _CACHE_MISS_TTL)
            if False:
                _log_info("CACHE MISS-HIT (known-bad) key=%r — skipping API call", cache_key)
                geo = geo
            elif False:
                _log_info("CACHE MISS key=%r — calling _reverse_geocode", cache_key)
                geo = _reverse_geocode(lat, lon, addr=addr)
                _log_info("geocode result for address_id=%s: %s", addr.id, geo)
                if geo:
                    location_type = _reverse_geo_location_type(geo)
                    meta = dict(addr.raw_metadata or {})
                    meta["reverse_geocode_location_type"] = location_type
                    addr.raw_metadata = meta
                    flag_modified(addr, "raw_metadata")
                    # Only cache results that are reliable enough to reuse.
                    # place_rank, importance, location_type are already in the dict.
                    if _nominatim_result_is_reliable(geo) or geo.get("source") in ("google", "google_forward"):
                        cache.set(cache_key, geo)
                        _log_debug("CACHE SET key=%r", cache_key)
                    else:
                        _log_warning(
                            "Not caching low-quality result for key=%r source=%r place_rank=%s",
                            cache_key, geo.get("source"), geo.get("place_rank"),
                        )
                else:
                    # Short-lived miss entry so the next job can retry after 1 hour.
                    cache.set(miss_key, {"miss": True}, ttl=_CACHE_MISS_TTL)
                    _log_debug("CACHE MISS-SET key=%r ttl=%ds", miss_key, _CACHE_MISS_TTL)

            # ── Build result fields ────────────────────────────────────────
            if geo:
                standardized = _build_standardized(geo)
                _log_info("standardized address_id=%s: %r", addr.id, standardized)
                display_name = geo.get("display_name", "")
                provider = geo.get("source", "nominatim")
                # Pass match_status / distance when validation ran before this pass.
                match_status = (addr.coord_address_match_status or "").strip()
                distance_m = addr.coord_address_distance_m
                if distance_m is not None:
                    try:
                        distance_m = float(distance_m)
                    except (TypeError, ValueError):
                        distance_m = None
                upload_line = _resolve_upload_address(addr, addr.raw_metadata if isinstance(addr.raw_metadata, dict) else {})
                match_pct = (
                    address_match_percent_for_geo(
                        upload_line,
                        geo,
                        city=addr.city,
                        state=addr.state,
                        zip_code=addr.zip_code,
                        coord_distance_m=distance_m,
                    )
                    if upload_line
                    else 100
                )
                reverse_loc = _reverse_geo_location_type(geo)
                precise_house_number_match = _reverse_precise_house_number_match(
                    upload_line,
                    geo,
                    distance_m=distance_m,
                )
                if precise_house_number_match:
                    match_pct = 100
                confidence = _compute_confidence(
                    geo,
                    distance_m=distance_m,
                    match_status=match_status,
                    address_match_percent=match_pct,
                )
                confidence = _finalize_reverse_confidence(
                    confidence,
                    location_type=reverse_loc,
                    reverse_address_match_percent=match_pct,
                )
                if precise_house_number_match:
                    confidence = max(confidence, 100)
                    _log_debug(
                        "reverse confidence overridden to 100: precise house-number match "
                        "address_id=%s upload_line=%r display_name=%r location_type=%r distance_m=%s",
                        addr.id,
                        upload_line,
                        geo.get("display_name") or geo.get("formatted_address"),
                        reverse_loc,
                        distance_m,
                    )
                elif match_status == "MATCH":
                    confidence = 100
                    _log_debug(
                        "reverse confidence overridden to 100: stored coord/address "
                        "match_status is MATCH address_id=%s",
                        addr.id,
                    )
                elif upload_line and match_pct < _ADDRESS_MATCH_REQUIRED:
                    confidence = min(confidence, _LOW_MATCH_CONFIDENCE_CAP)
                if geo:
                    geo_block = dict(geo)
                    geo_block["address_match_percent"] = match_pct
                    geo_block["confidence"] = confidence
                    persist_provider_result(addr, "reverse_geocoding", geo_block)
                exc_reason = None
                summary["geocoded"] += 1
            else:
                standardized = ""
                display_name = ""
                provider = ""
                confidence = 0
                match_status = ""
                exc_reason = "Reverse geocoding failed — no result from any provider"
                addr.reverse_geocode_confidence_score = 0
                persist_provider_result(addr, "reverse_geocoding", None)
                summary["failed"] += 1

            # ── Upsert into agent1_results ─────────────────────────────────
            existing = session.scalar(
                _sel(Agent1Result).where(Agent1Result.address_id == addr.id)
            )
            if existing:
                row = existing
                row.updated_at = now
            else:
                row = Agent1Result(
                    job_id=job_id,
                    address_id=addr.id,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)

            row.raw_address = f"{lat}, {lon}"
            row.canonical_address = display_name
            row.chosen_standardized_address = standardized
            row.chosen_provider = provider
            row.validation_status = "REVERSE_GEOCODED"
            row.confidence_score = confidence
            row.exception_reason = exc_reason
            addr.reverse_geocode_confidence_score = confidence
            _sync_confidence_in_raw_metadata(addr, confidence)
            _log_info(
                "Agent1Result upsert: address_id=%s canonical=%r standardized=%r provider=%r "
                "confidence=%s addresses.reverse_geocode_confidence_score=%s "
                "validation_status=REVERSE_GEOCODED exc=%r match_status=%r",
                addr.id,
                display_name,
                standardized,
                provider,
                confidence,
                confidence,
                exc_reason,
                match_status,
            )

            # ── Backfill addresses table (additive only) ────────────────────
            # Upload columns (raw_address, city, lat, etc.) are frozen at ingest.
            # Agent output is stored in validated_* columns and raw_metadata only.
            if geo:
                _log_debug(
                    "reverse geocode address_id=%s: upload columns unchanged (validated via coord validation)",
                    addr.id,
                )

            session.flush()
            _log_debug("session.flush address_id=%s", addr.id)

            if progress_callback:
                progress_callback(idx, total)

        session.commit()
        _log_info("session.commit job_id=%r", job_id)
        _log_info("run_reverse_geocoder_for_job OUT: summary=%s", summary)
        _log_info("=" * 72)
        return summary

    except Exception:
        session.rollback()
        _log_error("run_reverse_geocoder_for_job FAILED job_id=%r — rolled back", job_id, exc_info=True)
        raise
    finally:
        session.close()
        _log_debug("session.close job_id=%r", job_id)

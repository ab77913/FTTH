"""
Agent 3 — Parcel & Land-Use Lookup
====================================
Workflow (aligned with reference Regrid-Tigeline sqlmain.py):

  1. Regrid API v2 (primary) — parcel, owner, land use, county when token is set
  2. TIGERLINE fallback — county polygon match (local shapefile if configured,
     else US Census Geocoder counties layer for point-in-county)
  3. Nominatim — last resort for county name only when both above fail

Set REGRID_API_TOKEN (or REGRID_API_KEY) for step 1.
Optional TIGER_SHAPEFILE_PATH → tl_2025_us_county.shp for full ST_WITHIN /
ST_DWITHIN / NEAREST behaviour like sqlmain.py.

Results stored in agent_results with agent_name="agent3_parcel".

Logging:
  All activity is written to ``logs/agent3_parcel.log`` (rotating).
  Override path: ``FTTH_AGENT3_LOG_FILE``. Disable: ``FTTH_AGENT3_LOG=0``.
"""
from __future__ import annotations
from data_ingestion.config.paths import PROJECT_ROOT
from data_ingestion.config.log_paths import agent_log_path as _default_agent_log_path

import json
import logging
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import select as _sel, text as _txt

from data_ingestion.database.db import get_session_factory
from data_ingestion.database.models import Address, Agent1Result, AgentResult, AgentTable
from data_ingestion.utils.agent_logging import log_api_call

logger = logging.getLogger(__name__)

_PROJECT_ROOT = PROJECT_ROOT
_LOG_CONFIGURED = False
_LOG_CONFIGURED_PID: int = -1
_LOG_LOCK = threading.Lock()

try:
    from dotenv import load_dotenv
    load_dotenv(_PROJECT_ROOT / ".env")
except ImportError:
    pass


class _FlushingFileHandler(logging.FileHandler):
    """Ensure each log line is written immediately (helps on Windows / Celery solo)."""

    def emit(self, record: logging.LogRecord) -> None:
        super().emit(record)
        self.flush()


def _resolve_log_file() -> Path:
    """Always return an absolute path under project root unless env gives absolute path."""
    raw = os.environ.get("FTTH_AGENT3_LOG_FILE", "").strip()
    if raw:
        p = Path(raw)
        if not p.is_absolute():
            p = _PROJECT_ROOT / p
        return p.resolve()
    return _default_agent_log_path("agent3_parcel")


_LOG_FILE = _resolve_log_file()


def get_agent3_log_path() -> Path:
    """Return the absolute path to the Agent 3 log file."""
    return _resolve_log_file()


def _logging_enabled() -> bool:
    return os.environ.get("FTTH_AGENT3_LOG", "1").lower() not in ("0", "false", "no", "off")


def _flush_log_handlers() -> None:
    for handler in logger.handlers:
        if hasattr(handler, "flush"):
            handler.flush()


def _configure_file_logging() -> None:
    global _LOG_CONFIGURED, _LOG_CONFIGURED_PID, _LOG_FILE
    if not _logging_enabled():
        return
    _LOG_FILE = _resolve_log_file()
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
        handler = _FlushingFileHandler(
            str(_LOG_FILE),
            encoding="utf-8",
            delay=False,
        )
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(fmt)
        logger.handlers.clear()
        logger.addHandler(handler)
        _LOG_CONFIGURED = True
        _LOG_CONFIGURED_PID = current_pid
        # Visible in API/Celery terminal when Agent 3 module loads
        print(f"[agent3_parcel] Logging to: {_LOG_FILE}", flush=True)
        logger.info(
            "[A3] Log file ready: %s (disable with FTTH_AGENT3_LOG=0)",
            _LOG_FILE,
        )
        _flush_log_handlers()


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

_CENSUS_URL = "https://geocoding.geo.census.gov/geocoder/geographies/coordinates"
_NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
_REGRID_URL = "https://api.regrid.com/api/v2/parcels/point"
_USER_AGENT = "FTTH-DataIngestion/1.0"
_AGENT_NAME = "agent3_parcel"
_AGENT2_NAME = "agent2_geocoding"
_DISPLAY_NAME = "Agent 3: Parcel & Land Use"

# sqlmain.py spatial tolerances (~15 m)
_TIGER_DWITHIN_DEG = 0.00015

_STREET_SUFFIX_RE = re.compile(
    r"\b("
    r"st|street|rd|road|ave|avenue|blvd|boulevard|dr|drive|ln|lane|"
    r"ct|court|cir|circle|pl|place|way|pkwy|parkway|ter|terrace|trl|trail"
    r")\b",
    re.IGNORECASE,
)


def _regrid_token() -> str:
    """sqlmain.py uses REGRID_API_TOKEN; accept REGRID_API_KEY as alias."""
    return (
        os.environ.get("REGRID_API_TOKEN", "").strip()
        or os.environ.get("REGRID_API_KEY", "").strip()
    )


def _call_regrid(lat: float, lon: float, addr: Address | None = None) -> dict[str, Any] | None:
    """Regrid v2 point lookup (primary). Returns None when token missing or no match."""
    token = _regrid_token()
    if not token:
        _log_info("[A3][STEP-1 REGRID] skipped — no REGRID_API_TOKEN / REGRID_API_KEY (%.5f, %.5f)", lat, lon)
        return None

    request_payload = {"lat": lat, "lon": lon}
    params = urllib.parse.urlencode(request_payload)
    req = urllib.request.Request(
        f"{_REGRID_URL}?{params}",
        headers={"Authorization": f"Bearer {token}", "User-Agent": _USER_AGENT},
    )
    _log_debug("Regrid request (%.5f, %.5f) url=%s", lat, lon, f"{_REGRID_URL}?{params}")
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            status = resp.status
            if resp.status != 200:
                _log_warning("Regrid HTTP %s (%.5f, %.5f)", resp.status, lat, lon)
                return None
            data = json.loads(resp.read().decode())
        log_api_call(
            logger,
            "regrid_parcels_point",
            request={"url": f"{_REGRID_URL}?{params}", "lat": lat, "lon": lon},
            response=data,
            status=status,
        )
        features = data.get("features", [])
        if not features:
            _log_info("[A3][STEP-1 REGRID] NO_MATCH (%.5f, %.5f)", lat, lon)
            return None
        props = features[0].get("properties", {}) or {}
        result = {
            "parcel_id": props.get("parcelnumb", "") or "",
            "owner": props.get("owner", "") or "",
            "land_use": props.get("usedesc", "") or "",
            "land_use_code": props.get("usecode", "") or "",
            "county_name": props.get("county", "") or "",
            "state": props.get("state2", "") or "",
            "state_fips": props.get("state_fips", "") or "",
            "county_fips": props.get("county_fips", "") or "",
            "source": "regrid",
            "match_type": "REGRID",
        }
        _log_info(
            "[A3][STEP-1 REGRID] SUCCESS (%.5f, %.5f) county=%r land_use=%r parcel_id=%r confidence=95",
            lat, lon, result["county_name"], result["land_use"], result["parcel_id"],
        )
        return result
    except Exception as exc:
        _log_warning("Regrid error (%.5f, %.5f): %s", lat, lon, exc)
        return None


@lru_cache(maxsize=1)
def _load_tiger_gdf():
    """Load county shapefile once when TIGER_SHAPEFILE_PATH is set (optional)."""
    shp = os.environ.get("TIGER_SHAPEFILE_PATH", "").strip()
    if not shp or not Path(shp).is_file():
        return None
    try:
        import geopandas as gpd  # optional — only when shapefile path is configured
        from shapely.geometry import Point
    except ImportError:
        _log_warning("geopandas/shapely not installed; cannot use TIGER shapefile fallback")
        return None

    _log_info("Loading TIGER shapefile: %s", shp)
    gdf = gpd.read_file(shp).to_crs(epsg=4326)
    return gdf, Point


def _call_tigerline_shapefile(lat: float, lon: float) -> dict[str, Any] | None:
    """Local TIGER county polygons — ST_WITHIN, ST_DWITHIN, NEAREST (sqlmain.py)."""
    loaded = _load_tiger_gdf()
    if not loaded:
        return None
    gdf, Point = loaded
    point = Point(lon, lat)

    inside = gdf[gdf.geometry.contains(point)]
    if not inside.empty:
        row = inside.iloc[0]
        match_type = "ST_WITHIN"
        confidence = 85
    else:
        gdf_copy = gdf.copy()
        gdf_copy["_dist"] = gdf_copy.geometry.distance(point)
        near = gdf_copy[gdf_copy["_dist"] < _TIGER_DWITHIN_DEG]
        if not near.empty:
            row = near.sort_values("_dist").iloc[0]
            match_type = "ST_DWITHIN"
            confidence = 80
        else:
            row = gdf_copy.sort_values("_dist").iloc[0]
            match_type = "NEAREST"
            confidence = 75

    result = {
        "parcel_id": str(row.get("GEOID", "") or ""),
        "county_name": str(row.get("NAME") or row.get("NAMELSAD") or ""),
        "state_fips": str(row.get("STATEFP", "") or ""),
        "county_fips": str(row.get("COUNTYFP", "") or ""),
        "land_use": "County Boundary",
        "source": f"tigerline_{match_type.lower()}",
        "match_type": match_type,
        "confidence": confidence,
    }
    _log_info(
        "[A3][STEP-2 TIGERLINE] shapefile %s (%.5f, %.5f) county=%r confidence=%s",
        match_type, lat, lon, result["county_name"], confidence,
    )
    return result


def _call_tigerline_census(lat: float, lon: float, addr: Address | None = None) -> dict[str, Any] | None:
    """County lookup via Census Geocoder (layer 82) when no local shapefile."""
    request_payload = {
        "x": lon,
        "y": lat,
        "benchmark": "Public_AR_Current",
        "vintage": "Current_Current",
        "layers": "82",
        "format": "json",
    }
    params = urllib.parse.urlencode(request_payload)
    req = urllib.request.Request(
        f"{_CENSUS_URL}?{params}",
        headers={"User-Agent": _USER_AGENT},
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            status = resp.status
            data = json.loads(resp.read().decode())
        log_api_call(
            logger,
            "census_geocoder_counties",
            request={"url": f"{_CENSUS_URL}?{params}", "params": request_payload},
            response=data,
            status=status,
        )
        counties = data.get("result", {}).get("geographies", {}).get("Counties", [])
        if not counties:
            _log_info("[A3][STEP-2 TIGERLINE] Census NO_MATCH (%.5f, %.5f)", lat, lon)
            return None
        county = counties[0]
        result = {
            "parcel_id": county.get("GEOID", "") or "",
            "county_name": county.get("NAME", "") or "",
            "state_fips": county.get("STATE", "") or "",
            "county_fips": county.get("COUNTY", "") or "",
            "land_use": "County Boundary",
            "source": "tigerline_st_within",
            "match_type": "ST_WITHIN",
            "confidence": 85,
        }
        _log_info(
            "[A3][STEP-2 TIGERLINE] Census ST_WITHIN (%.5f, %.5f) county=%r geoid=%r confidence=85",
            lat, lon, result["county_name"], result["parcel_id"],
        )
        return result
    except Exception as exc:
        _log_warning("TIGERLINE (Census counties) error (%.5f, %.5f): %s", lat, lon, exc)
        return None


def _call_census(lat: float, lon: float) -> dict[str, Any] | None:
    """Legacy unit-test helper for the older Census geocoder response shape."""
    params = urllib.parse.urlencode({
        "x": lon,
        "y": lat,
        "benchmark": "Public_AR_Current",
        "vintage": "Current_Current",
        "format": "json",
    })
    req = urllib.request.Request(f"{_CENSUS_URL}?{params}", headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode())
    except Exception:
        return None

    geographies = data.get("result", {}).get("geographies", {})
    tracts = geographies.get("Census Tracts", []) or [{}]
    counties = geographies.get("Counties", []) or [{}]
    tract = tracts[0]
    county = counties[0]
    return {
        "state_fips": tract.get("STATE", ""),
        "county_fips": tract.get("COUNTY", ""),
        "tract": tract.get("TRACT", ""),
        "geoid": tract.get("GEOID", ""),
        "county_name": county.get("NAME", ""),
        "source": "census",
    }


def _call_tigerline(lat: float, lon: float, addr: Address | None = None) -> dict[str, Any] | None:
    """TIGERLINE fallback: shapefile if configured, else Census county layer."""
    shp = os.environ.get("TIGER_SHAPEFILE_PATH", "").strip()
    if shp and Path(shp).is_file():
        _log_debug("TIGERLINE try shapefile first: %s", shp)
    else:
        _log_debug("TIGERLINE no shapefile; will use Census counties layer (%.5f, %.5f)", lat, lon)
    return _call_tigerline_shapefile(lat, lon) or _call_tigerline_census(lat, lon, addr=addr)


def _call_nominatim_details(lat: float, lon: float, addr: Address | None = None) -> dict[str, Any] | None:
    request_payload = {"format": "json", "lat": lat, "lon": lon, "addressdetails": 1}
    params = urllib.parse.urlencode(request_payload)
    req = urllib.request.Request(
        f"{_NOMINATIM_URL}?{params}",
        headers={"User-Agent": _USER_AGENT},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            status = resp.status
            data = json.loads(resp.read().decode())
        log_api_call(
            logger,
            "nominatim_reverse",
            request={"url": f"{_NOMINATIM_URL}?{params}", "params": request_payload},
            response=data,
            status=status,
        )
        if "error" in data:
            return None
        addr = data.get("address", {})
        return {
            "county_name": addr.get("county", ""),
            "state": addr.get("state", ""),
            "postcode": addr.get("postcode", ""),
            "country": addr.get("country_code", ""),
            "osm_type": data.get("type", ""),
            "source": "nominatim",
        }
    except Exception as exc:
        _log_warning("Nominatim error (%.5f, %.5f): %s", lat, lon, exc)
        return None
    finally:
        time.sleep(1.0)


def _is_valid_lat(lat: Any) -> bool:
    try:
        v = float(lat)
        return v != 0 and -90 <= v <= 90
    except (TypeError, ValueError):
        return False


def _is_valid_lon(lon: Any) -> bool:
    try:
        v = float(lon)
        return v != 0 and -180 <= v <= 180
    except (TypeError, ValueError):
        return False


def _resolve_coordinates(
    a2: dict[str, Any] | None,
    a1: Agent1Result | None,
    addr: Address,
) -> tuple[float | None, float | None, str]:
    """
  Coordinate priority for parcel lookup (matches pipeline order Agent 1 → 2 → 3):

    1. Agent 2 — Google geocode when status is geocoded
    2. Agent 1 — Smarty lat/lon
    3. addresses — uploaded / backfilled lat/lon
    """
    if a2 and a2.get("status") == "geocoded":
        lat, lon = a2.get("latitude"), a2.get("longitude")
        if _is_valid_lat(lat) and _is_valid_lon(lon):
            return float(lat), float(lon), "agent2_geocoding"

    if a1 and _is_valid_lat(a1.smarty_lat) and _is_valid_lon(a1.smarty_lon):
        return float(a1.smarty_lat), float(a1.smarty_lon), "agent1_smarty"

    if _is_valid_lat(addr.latitude) and _is_valid_lon(addr.longitude):
        return float(addr.latitude), float(addr.longitude), "addresses"

    return None, None, "none"


def _address_text_for_land_use(raw_address: str | None, validated_address: str | None = None) -> str:
    for candidate in (validated_address, raw_address):
        if candidate and str(candidate).strip():
            return str(candidate).strip()
    return ""


def _classify_land_use(
    raw_address: str | None,
    parcel_data: dict | None,
    *,
    validated_address: str | None = None,
) -> str:
    if parcel_data and parcel_data.get("land_use"):
        return parcel_data["land_use"]

    text = _address_text_for_land_use(raw_address, validated_address).lower()
    if not text:
        return "Unknown"
    if any(kw in text for kw in ["apt", "apartment", "unit #", "ste ", "suite", " #"]):
        return "Multi-Family Residential"
    if any(kw in text for kw in ["hwy", "highway", "industrial", "warehouse", "park"]):
        return "Commercial/Industrial"
    if _STREET_SUFFIX_RE.search(text):
        return "Single-Family Residential"
    return "Unknown"


def _resolve_parcel(
    lat: float,
    lon: float,
    raw_address: str | None,
    validated_line: str | None,
    parcel_options: dict[str, bool] | None = None,
    addr: Address | None = None,
) -> dict[str, Any]:
    """
    Regrid → TIGERLINE → Nominatim (sqlmain order for steps 1–2; step 3 is extra safety).
    """
    from data_ingestion.utils.pipeline_options import DEFAULT_AGENT3_OPTIONS

    opts = {**DEFAULT_AGENT3_OPTIONS, **(parcel_options or {})}
    _log_debug(
        "_resolve_parcel IN lat=%.5f lon=%.5f raw_address=%r validated_line=%r opts=%s",
        lat, lon, (raw_address or "")[:120], (validated_line or "")[:120], opts,
    )
    regrid = _call_regrid(lat, lon, addr=addr) if opts.get("regrid", True) else None
    if regrid:
        land_use = regrid.get("land_use") or _classify_land_use(
            raw_address, regrid, validated_address=validated_line,
        )
        return {
            "status": "found",
            "latitude": lat,
            "longitude": lon,
            "land_use": land_use,
            "confidence": 95,
            "parcel_id": regrid.get("parcel_id", ""),
            "owner": regrid.get("owner", ""),
            "land_use_code": regrid.get("land_use_code", ""),
            "county_name": regrid.get("county_name", ""),
            "state": regrid.get("state", ""),
            "state_fips": regrid.get("state_fips", ""),
            "county_fips": regrid.get("county_fips", ""),
            "source": "regrid",
            "match_type": regrid.get("match_type", "REGRID"),
        }

    _log_info("[A3][STEP-2 TIGERLINE] Regrid unavailable — trying TIGERLINE (%.5f, %.5f)", lat, lon)
    tiger = _call_tigerline(lat, lon, addr=addr) if opts.get("tigerline", True) else None
    if tiger:
        return {
            "status": "found",
            "latitude": lat,
            "longitude": lon,
            "land_use": tiger.get("land_use", "County Boundary"),
            "confidence": tiger.get("confidence", 85),
            "parcel_id": tiger.get("parcel_id", ""),
            "county_name": tiger.get("county_name", ""),
            "state_fips": tiger.get("state_fips", ""),
            "county_fips": tiger.get("county_fips", ""),
            "source": tiger.get("source", "tigerline"),
            "match_type": tiger.get("match_type", ""),
        }

    _log_info("[A3][STEP-3 NOMINATIM] TIGERLINE failed — trying Nominatim (%.5f, %.5f)", lat, lon)
    nom = _call_nominatim_details(lat, lon, addr=addr) if opts.get("nominatim", True) else None
    if nom:
        _log_info(
            "Nominatim partial (%.5f, %.5f) county=%r",
            lat, lon, nom.get("county_name"),
        )
    else:
        _log_warning("All parcel sources failed (%.5f, %.5f)", lat, lon)
    return {
        "status": "partial",
        "latitude": lat,
        "longitude": lon,
        "land_use": _classify_land_use(raw_address, None, validated_address=validated_line),
        "county_name": (nom or {}).get("county_name", ""),
        "state": (nom or {}).get("state", ""),
        "source": "nominatim" if nom else "none",
        "confidence": 30 if nom else 0,
    }


def _ensure_table(session) -> None:
    if not session.execute(_sel(AgentTable).where(AgentTable.agent_name == _AGENT_NAME)).scalar_one_or_none():
        session.add(AgentTable(
            agent_name=_AGENT_NAME,
            display_name=_DISPLAY_NAME,
            owner="system",
            description="Parcel lookup: Regrid → TIGERLINE → Nominatim (sqlmain workflow)",
            color_rules=[],
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


def run_agent3_for_job(
    job_id: str,
    address_ids: list[int] | None = None,
    progress_callback=None,
    agent_options: dict[str, bool] | None = None,
) -> dict:
    """Run parcel lookup for all addresses in the job."""
    _configure_file_logging()
    _log_info("=" * 72)
    _log_info("[A3][JOB START] agent3_parcel")
    _log_info("[A3] Log file: %s", _LOG_FILE.resolve())
    _log_info("[A3] Module: %s", Path(__file__).resolve())
    _log_info(
        "[A3][JOB START] job_id=%r addresses_filter=%s | REGRID token=%s | TIGER file=%s",
        job_id,
        address_ids or "all",
        "YES" if _regrid_token() else "NO (will use TIGERLINE)",
        os.environ.get("TIGER_SHAPEFILE_PATH", "").strip() or "not set (Census counties API)",
    )
    _log_info(
        "[A3] Flow per address: STEP-1 Regrid -> STEP-2 TIGERLINE -> STEP-3 Nominatim",
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
        a2_map: dict[int, dict[str, Any]] = {}
        if addr_ids:
            a1_map = {r.address_id: r for r in session.scalars(
                _sel(Agent1Result).where(Agent1Result.address_id.in_(addr_ids))
            ).all()}
            a2_rows = session.scalars(
                _sel(AgentResult).where(
                    AgentResult.agent_name == _AGENT2_NAME,
                    AgentResult.address_id.in_(addr_ids),
                )
            ).all()
            a2_map = {r.address_id: (r.data or {}) for r in a2_rows}

        total = len(addresses)
        _log_info(
            "loaded %d addresses for job_id=%r (agent1=%d agent2=%d)",
            total, job_id, len(a1_map), len(a2_map),
        )
        summary = {"total": total, "found": 0, "partial": 0, "failed": 0, "skipped": 0}

        for idx, addr in enumerate(addresses, 1):
            try:
                a1 = a1_map.get(addr.id)
                a2 = a2_map.get(addr.id)
                lat, lon, coord_source = _resolve_coordinates(a2, a1, addr)
                _log_info(
                    "[A3][ADDR %d/%d] address_id=%s | coords from %s | lat=%r lon=%r | "
                    "agent2_status=%r",
                    idx, total, addr.id, coord_source, lat, lon,
                    (a2 or {}).get("status"),
                )
                validated_line = (
                    addr.validated_street_line
                    or addr.validated_raw_address
                    or (a2 or {}).get("formatted_address")
                )

                if lat is None or lon is None:
                    summary["skipped"] += 1
                    _log_warning(
                        "[A3][ADDR %d/%d] SKIPPED address_id=%s — no coordinates (run Agent 2 first)",
                        idx, total, addr.id,
                    )
                    _upsert(session, job_id, addr.id, {
                        "status": "skipped",
                        "reason": "no coordinates available (run Agent 2 geocoding first)",
                        "land_use": "Unknown",
                        "confidence": 0,
                        "agent2_status": (a2 or {}).get("status", ""),
                    })
                    if progress_callback:
                        progress_callback(idx, total)
                    continue

                result_data = _resolve_parcel(
                    lat, lon, addr.raw_address, validated_line,
                    parcel_options=agent_options,
                    addr=addr,
                )
                result_data["coord_source"] = coord_source
                if a2:
                    result_data["agent2_status"] = a2.get("status", "")
                    result_data["agent2_confidence"] = a2.get("confidence", "")
                if result_data.get("status") == "found":
                    summary["found"] += 1
                elif result_data.get("status") == "partial":
                    summary["partial"] += 1
                else:
                    summary["failed"] += 1

                _log_info(
                    "[A3][ADDR %d/%d] RESULT address_id=%s | status=%s | source=%s | "
                    "confidence=%s | county=%r | land_use=%r | match_type=%r",
                    idx, total, addr.id,
                    result_data.get("status"),
                    result_data.get("source"),
                    result_data.get("confidence"),
                    result_data.get("county_name"),
                    result_data.get("land_use"),
                    result_data.get("match_type"),
                )
                _upsert(session, job_id, addr.id, result_data)

            except Exception as exc:
                _log_warning("Agent3 error on address_id=%s: %s", addr.id, exc, exc_info=True)
                summary["failed"] += 1

            if progress_callback:
                progress_callback(idx, total)

        session.commit()
        _log_info(
            "[A3][JOB END] job_id=%r | found=%s partial=%s skipped=%s failed=%s total=%s",
            job_id,
            summary["found"],
            summary["partial"],
            summary["skipped"],
            summary["failed"],
            summary["total"],
        )
        _log_info("[A3] Log file: %s", _LOG_FILE)
        _log_info("=" * 72)
        _flush_log_handlers()
        return summary
    except Exception as exc:
        session.rollback()
        _log_error("Agent3 failed for job %s: %s", job_id, exc, exc_info=True)
        _flush_log_handlers()
        raise
    finally:
        session.close()
        _flush_log_handlers()

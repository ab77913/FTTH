"""Agent 0 - House Discovery from uploaded polygon map layers.

This agent reads polygon rows that ingestion already stored as map-only KML/KMZ
layers, samples safe candidate points inside those polygons, reverse geocodes
the candidates, deduplicates them, and accepts unique households into
``addresses`` so the normal downstream agents can process them.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from threading import Lock
from typing import Any, Callable

from sqlalchemy import select as _sel, text as _txt
from sqlalchemy.dialects.postgresql import insert as _pg_insert
from sqlalchemy.orm.attributes import flag_modified

from data_ingestion.database.db import get_session_factory
from data_ingestion.database.geo import set_address_geom
from data_ingestion.database.models import (
    Address,
    Agent0HouseDiscoveryResult,
    AgentResult,
    AgentTable,
)
from data_ingestion.utils.agent_logging import configure_agent_logger, log_api_call, log_payload
from data_ingestion.utils.strings import normalize_address_key

try:
    from shapely.geometry import Point, Polygon
except Exception:  # pragma: no cover - surfaced as a skipped summary at runtime.
    Point = None
    Polygon = None

logger = logging.getLogger(__name__)

AGENT_NAME = "agent0_house_discovery"
GOOGLE_GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
ATTOM_PROPERTY_DETAIL_URL = "https://api.gateway.attomdata.com/propertyapi/v1.0.0/property/detail"

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_ENV_FILE = os.path.join(_PROJECT_ROOT, ".env")
if os.path.exists(_ENV_FILE):
    try:
        from dotenv import load_dotenv
        load_dotenv(_ENV_FILE, override=False)
    except Exception:
        pass

DEFAULT_AGENT0_OPTIONS: dict[str, Any] = {
    "enabled": True,
    "reverse_geocode": True,
    "grid_step": 0.00025,
    # 0 means unlimited. The grid step controls scan density.
    "max_candidates_per_polygon": 0,
    "max_candidates_per_job": 0,
    "dedup_distance_m": 20,
    "multi_unit_suffixing": True,
    "max_units_per_base_address": 4,
    "validate_discovered": False,
    "validate_with_smarty": True,
    "validate_with_regrid": False,
    "validation_threshold": 90,
    "exclude_discovered_from_agent1": True,
    "include_existing_kml_points": True,
    "reverse_workers": 8,
    "reverse_geocode_cache": True,
}


ProgressCallback = Callable[[int, int], None] | None


def _dotenv_values() -> dict[str, str]:
    if not os.path.exists(_ENV_FILE):
        return {}
    try:
        from dotenv import dotenv_values
        return {
            str(key): str(value).strip()
            for key, value in dotenv_values(_ENV_FILE).items()
            if value is not None and str(value).strip()
        }
    except Exception:
        logger.warning("Agent0 could not read .env values from %s", _ENV_FILE, exc_info=True)
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


def _google_api_key() -> str:
    return _config_value("GOOGLE_GEOCODING_API_KEY", "GOOGLE_MAPS_API_KEY")


def _attom_api_key() -> str:
    return _config_value("ATTOM_API_KEY")


def _merge_options(raw: dict[str, Any] | None) -> dict[str, Any]:
    opts = dict(DEFAULT_AGENT0_OPTIONS)
    if not isinstance(raw, dict):
        return opts
    for key, default in DEFAULT_AGENT0_OPTIONS.items():
        if key not in raw:
            continue
        value = raw[key]
        try:
            if isinstance(default, bool):
                if isinstance(value, str):
                    opts[key] = value.strip().lower() not in {"0", "false", "no", "off"}
                else:
                    opts[key] = bool(value)
            elif isinstance(default, int):
                if key in {"max_candidates_per_polygon", "max_candidates_per_job"}:
                    opts[key] = max(0, int(value))
                elif key == "reverse_workers":
                    opts[key] = max(1, min(32, int(value)))
                else:
                    opts[key] = max(1, int(value))
            elif isinstance(default, float):
                opts[key] = max(0.00001, float(value))
            else:
                opts[key] = value
        except (TypeError, ValueError):
            logger.warning("Ignoring invalid Agent 0 option %s=%r", key, value)
    return opts


def _distance_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(max(0.0, min(1.0, a))))


_LEADING_STREET_NUMBER_RE = re.compile(r"^\s*(\d+[A-Za-z]?)(\s+)")


def _suffix_address(address: str, unit_index: int) -> str:
    """Return an address with a generated unit suffix on the leading house number."""
    text = str(address or "").strip()
    match = _LEADING_STREET_NUMBER_RE.match(text)
    if not match:
        return text
    number = match.group(1)
    if "-" in number:
        return text
    return f"{number}-{unit_index}{text[match.end(1):]}"


def _first_number(*values: Any) -> float | None:
    for value in values:
        try:
            if value not in (None, ""):
                return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _infer_unit_count_from_attom_property(prop: dict[str, Any]) -> int:
    building = prop.get("building") or {}
    building_summary = building.get("summary") or {}
    summary = prop.get("summary") or {}
    units = _first_number(
        building_summary.get("unitsCount"),
        building_summary.get("units"),
        summary.get("unitsCount"),
    )
    if units:
        return int(units)

    text = " ".join(
        str(value or "")
        for value in (
            summary.get("propertyType"),
            summary.get("propLandUse"),
            summary.get("propSubType"),
            summary.get("propertyUse"),
        )
    ).upper()
    explicit = re.search(r"\b(\d+)\s*(?:UNIT|UNITS|FAMILY|FAMILIES)\b", text)
    if explicit:
        return max(0, int(explicit.group(1)))
    if "DUPLEX" in text:
        return 2
    if "TRIPLEX" in text:
        return 3
    if "QUAD" in text or "FOURPLEX" in text or "4PLEX" in text:
        return 4
    if "MULTI" in text or "APARTMENT" in text:
        return 2
    return 0


def _attom_units_for_point(lat: float, lon: float, *, radius_m: float = 100.0) -> dict[str, Any]:
    """Fetch ATTOM property data near a point and return the nearest unit count."""
    api_key = _attom_api_key()
    if not api_key:
        return {"units_count": 0, "reason": "ATTOM_API_KEY not configured"}
    radius_miles = max(0.001, radius_m / 1609.344)
    params = urllib.parse.urlencode({
        "latitude": f"{lat:.7f}",
        "longitude": f"{lon:.7f}",
        "radius": f"{radius_miles:.4f}",
    })
    req = urllib.request.Request(
        f"{os.environ.get('ATTOM_BUILDING_API_URL', ATTOM_PROPERTY_DETAIL_URL)}?{params}",
        headers={"accept": "application/json", "apikey": api_key},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    log_api_call(
        logger,
        "attom_property_detail",
        request={"url": req.full_url, "lat": lat, "lon": lon, "radius_m": radius_m},
        response=payload,
        status="ok",
    )

    best_property: dict[str, Any] | None = None
    best_distance_m = float("inf")
    for prop in payload.get("property") or []:
        loc = prop.get("location") or {}
        try:
            prop_lat = float(loc.get("latitude"))
            prop_lon = float(loc.get("longitude"))
        except (TypeError, ValueError):
            continue
        dist = _distance_meters(lat, lon, prop_lat, prop_lon)
        if dist < best_distance_m:
            best_distance_m = dist
            best_property = prop

    if not best_property:
        return {"units_count": 0, "reason": "ATTOM returned no nearby property"}

    units = _infer_unit_count_from_attom_property(best_property)
    identifier = best_property.get("identifier") or {}
    address = best_property.get("address") or {}
    location = best_property.get("location") or {}
    summary = best_property.get("summary") or {}
    return {
        "units_count": int(units or 0),
        "attom_id": identifier.get("attomId") or identifier.get("Id"),
        "attom_address": address.get("oneLine"),
        "attom_property_type": summary.get("propertyType"),
        "attom_land_use": summary.get("propLandUse"),
        "latitude": location.get("latitude"),
        "longitude": location.get("longitude"),
        "distance_m": best_distance_m,
        "reason": "attom_units_count" if units else "ATTOM property has no unitsCount",
        "raw": best_property,
    }


def _attom_geocode_for_point(lat: float, lon: float) -> dict[str, Any] | None:
    """Return a Google-like geocode payload from ATTOM's nearest property."""
    info = _attom_units_for_point(lat, lon)
    address = str(info.get("attom_address") or "").strip()
    if not address or address.startswith(","):
        return None
    try:
        out_lat = float(info.get("latitude") or lat)
        out_lon = float(info.get("longitude") or lon)
    except (TypeError, ValueError):
        out_lat, out_lon = lat, lon
    return {
        "address": address,
        "latitude": out_lat,
        "longitude": out_lon,
        "confidence": 88 if info.get("distance_m", 9999) <= 50 else 78,
        "location_type": "ATTOM_PROPERTY",
        "place_id": info.get("attom_id"),
        "provider": "attom",
        "attom_info": {k: v for k, v in info.items() if k != "raw"},
        "raw": info.get("raw"),
    }


def parse_polygon_coordinates(value: Any) -> Any | None:
    """Parse stored KML coordinate text into a Shapely Polygon."""
    if Polygon is None or not value:
        return None
    coords: list[tuple[float, float]] = []
    text = str(value).strip()
    for token in text.replace("\n", " ").replace("\t", " ").split():
        parts = token.split(",")
        if len(parts) < 2:
            continue
        try:
            lon = float(parts[0])
            lat = float(parts[1])
        except (TypeError, ValueError):
            continue
        if -180 <= lon <= 180 and -90 <= lat <= 90:
            coords.append((lon, lat))
    if len(coords) < 3:
        return None
    if coords[0] != coords[-1]:
        coords.append(coords[0])
    try:
        polygon = Polygon(coords)
        if not polygon.is_valid:
            polygon = polygon.buffer(0)
        return polygon if polygon and not polygon.is_empty else None
    except Exception:
        logger.debug("Failed to parse polygon coordinates", exc_info=True)
        return None


def generate_candidate_points(polygon: Any, *, grid_step: float, max_points: int | None = None) -> list[dict[str, float]]:
    """Generate bounded candidate points strictly inside a polygon."""
    if Point is None or polygon is None or polygon.is_empty:
        return []
    min_lon, min_lat, max_lon, max_lat = polygon.bounds
    if min_lon == max_lon or min_lat == max_lat:
        return []

    points: list[dict[str, float]] = []
    limit = int(max_points or 0)
    offsets = (0.5, 0.25, 0.75)
    lat = min_lat
    while lat <= max_lat and (limit <= 0 or len(points) < limit):
        lon = min_lon
        while lon <= max_lon and (limit <= 0 or len(points) < limit):
            for off_x in offsets:
                for off_y in offsets:
                    cand_lon = lon + (grid_step * off_x)
                    cand_lat = lat + (grid_step * off_y)
                    if cand_lon > max_lon or cand_lat > max_lat:
                        continue
                    point = Point(cand_lon, cand_lat)
                    if polygon.contains(point):
                        points.append({"lat": cand_lat, "lon": cand_lon})
                        if limit > 0 and len(points) >= limit:
                            break
                if limit > 0 and len(points) >= limit:
                    break
            lon += grid_step
        lat += grid_step
    return points


def _deduplicate_points(points: list[dict[str, Any]], threshold_m: float = 20.0) -> list[dict[str, Any]]:
    """Return points deduplicated by coordinate distance, preserving input order."""
    unique: list[dict[str, Any]] = []
    for point in points:
        try:
            lat = float(point["lat"])
            lon = float(point["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        duplicate = False
        for existing in unique:
            if _distance_meters(lat, lon, float(existing["lat"]), float(existing["lon"])) < threshold_m:
                duplicate = True
                break
        if not duplicate:
            unique.append({**point, "lat": lat, "lon": lon})
    return unique


def _address_key_from_text(value: Any) -> str:
    return normalize_address_key(str(value or ""))


def _existing_kml_point_candidates(session, job_id: str) -> list[dict[str, Any]]:
    """Return uploaded KML/KMZ point rows that should filter generated results."""
    rows = session.scalars(
        _sel(Address)
        .where(
            Address.job_id == job_id,
            _txt("COALESCE(raw_metadata->>'geometry_type','') = 'Point'"),
            _txt("COALESCE(raw_metadata->>'map_layer_only','false') != 'true'"),
        )
        .order_by(Address.id)
    ).all()
    candidates: list[dict[str, Any]] = []
    for row in rows:
        meta = row.raw_metadata if isinstance(row.raw_metadata, dict) else {}
        source_format = str(meta.get("source_format") or "").strip().lower()
        if source_format not in {"kml", "kmz"} and not str(row.source_file or "").lower().endswith((".kml", ".kmz")):
            continue
        lat = row.latitude if row.latitude is not None else meta.get("latitude")
        lon = row.longitude if row.longitude is not None else meta.get("longitude")
        try:
            lat_f = float(lat)
            lon_f = float(lon)
        except (TypeError, ValueError):
            continue
        candidates.append({
            "lat": lat_f,
            "lon": lon_f,
            "source": "existing_kml_point",
            "address_id": row.id,
            "raw_address": row.raw_address,
            "address_key": _address_key_from_text(row.raw_address),
        })
    return candidates


def _cached_reverse_geocoder(api_key: str, *, enabled: bool = True):
    """Return a per-run cached reverse geocoder keyed by rounded coordinates."""
    cache: dict[tuple[float, float], dict[str, Any] | None] = {}
    lock = Lock()

    def geocode(lat: float, lon: float) -> dict[str, Any] | None:
        key = (round(float(lat), 6), round(float(lon), 6))
        if enabled:
            with lock:
                if key in cache:
                    return cache[key]
        result = _reverse_geocode(float(lat), float(lon), api_key)
        if enabled:
            with lock:
                cache[key] = result
        return result

    return geocode


def _parallel_reverse_geocode_candidates(
    candidates: list[dict[str, Any]],
    *,
    api_key: str,
    workers: int,
    use_cache: bool,
    progress_callback: ProgressCallback,
    processed_start: int,
    progress_total: int,
    geocode_fn=None,
) -> list[tuple[dict[str, Any], dict[str, Any] | None, str | None]]:
    """Reverse-geocode candidates concurrently while keeping DB writes sequential."""
    if not candidates:
        return []
    geocode = geocode_fn or _cached_reverse_geocoder(api_key, enabled=use_cache)
    worker_count = max(1, min(int(workers or 1), len(candidates)))

    def task(candidate: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None, str | None]:
        try:
            return candidate, geocode(float(candidate["lat"]), float(candidate["lon"])), None
        except Exception as exc:  # network/parser failure for one candidate must not stop the polygon
            logger.warning("Agent 0 reverse geocode failed for %s: %s", candidate, exc)
            return candidate, None, str(exc)

    results: list[tuple[int, dict[str, Any], dict[str, Any] | None, str | None]] = []
    completed = 0
    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="agent0-reverse") as executor:
        future_to_index = {executor.submit(task, candidate): idx for idx, candidate in enumerate(candidates)}
        for future in as_completed(future_to_index):
            idx = future_to_index[future]
            candidate, geocode_result, error = future.result()
            completed += 1
            if progress_callback:
                progress_callback(processed_start + completed, progress_total)
            results.append((idx, candidate, geocode_result, error))
    results.sort(key=lambda item: item[0])
    return [(candidate, geocode_result, error) for _, candidate, geocode_result, error in results]

def _score_google_result(result: dict[str, Any]) -> int:
    types = {str(t).lower() for t in (result.get("types") or [])}
    geometry = result.get("geometry") or {}
    loc_type = str(geometry.get("location_type") or "").upper()
    formatted = str(result.get("formatted_address") or "")
    score = 0
    if types & {"street_address", "premise", "subpremise"}:
        score += 100
    if types & {"establishment", "point_of_interest"}:
        score += 25
    if types & {"route", "neighborhood", "locality", "postal_code", "administrative_area_level_1"}:
        score -= 75
    score += {
        "ROOFTOP": 40,
        "RANGE_INTERPOLATED": 30,
        "GEOMETRIC_CENTER": 10,
        "APPROXIMATE": 0,
    }.get(loc_type, 0)
    if re.match(r"^\s*\d+", formatted):
        score += 25
    return score


def _best_google_reverse_result(results: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not results:
        return None
    ranked = sorted(results, key=_score_google_result, reverse=True)
    best = ranked[0]
    if _score_google_result(best) <= 0:
        return None
    return best


def _reverse_geocode(lat: float, lon: float, api_key: str) -> dict[str, Any] | None:
    if not api_key:
        return None
    params = urllib.parse.urlencode({"latlng": f"{lat},{lon}", "key": api_key})
    req = urllib.request.Request(f"{GOOGLE_GEOCODE_URL}?{params}", headers={"User-Agent": "FTTH-Agent0/1.0"})
    with urllib.request.urlopen(req, timeout=12) as resp:  # noqa: S310 - fixed Google API endpoint.
        payload = json.loads(resp.read().decode("utf-8"))
    log_api_call(
        logger,
        "google_reverse_geocode",
        request={"url": f"{GOOGLE_GEOCODE_URL}?{params}", "lat": lat, "lon": lon},
        response=payload,
        status=payload.get("status"),
    )
    if payload.get("status") != "OK" or not payload.get("results"):
        return None
    result = _best_google_reverse_result(payload.get("results") or [])
    if not result:
        return None
    formatted = str(result.get("formatted_address") or "").strip()
    location = ((result.get("geometry") or {}).get("location") or {})
    loc_type = str((result.get("geometry") or {}).get("location_type") or "").upper()
    confidence = {
        "ROOFTOP": 95,
        "RANGE_INTERPOLATED": 88,
        "GEOMETRIC_CENTER": 78,
        "APPROXIMATE": 65,
    }.get(loc_type, 70)
    try:
        out_lat = float(location.get("lat", lat))
        out_lon = float(location.get("lng", lon))
    except (TypeError, ValueError):
        out_lat, out_lon = lat, lon
    return {
        "address": formatted,
        "latitude": out_lat,
        "longitude": out_lon,
        "confidence": confidence,
        "location_type": loc_type or None,
        "place_id": result.get("place_id"),
        "raw": result,
    }


def _ensure_agent_table(session) -> None:
    if session.execute(_sel(AgentTable).where(AgentTable.agent_name == AGENT_NAME)).scalar_one_or_none():
        return
    session.add(AgentTable(
        agent_name=AGENT_NAME,
        display_name="Agent 0: House Discovery",
        owner="system",
        description="Discovers household addresses inside uploaded KML/KMZ polygon map layers.",
        color_rules=[
            {"field": "status", "value": "accepted", "color": "#9C6500", "label": "New Address"},
            {"field": "status", "value": "duplicate", "color": "#ffffff", "label": "Duplicate Address"},
            {"field": "status", "value": "geocode_failed", "color": "#C00000", "label": "Geocode Failed"},
        ],
    ))
    session.flush()


def _polygon_rows(session, job_id: str) -> list[Address]:
    return session.scalars(
        _sel(Address)
        .where(
            Address.job_id == job_id,
            _txt("raw_metadata->>'geometry_type' = 'Polygon'"),
            _txt("COALESCE(raw_metadata->>'map_layer_only','false') = 'true'"),
        )
        .order_by(Address.id)
    ).all()


def _existing_address_keys(session, job_id: str) -> tuple[set[str], list[tuple[float, float]]]:
    keys: set[str] = set()
    coords: list[tuple[float, float]] = []
    rows = session.execute(
        _sel(Address.normalized_key, Address.raw_address, Address.latitude, Address.longitude)
        .where(
            Address.job_id == job_id,
            _txt("COALESCE(raw_metadata->>'map_layer_only','false') != 'true'"),
        )
    ).all()
    for normalized_key, raw_address, lat, lon in rows:
        key = normalized_key or normalize_address_key(raw_address)
        if key:
            keys.add(key)
        if lat is not None and lon is not None:
            try:
                coords.append((float(lat), float(lon)))
            except (TypeError, ValueError):
                pass
    return keys, coords


def _record_discovery(
    session,
    *,
    job_id: str,
    polygon_row: Address,
    candidate: dict[str, float],
    status: str,
    geocode: dict[str, Any] | None = None,
    address_id: int | None = None,
    dedupe_reason: str | None = None,
) -> Agent0HouseDiscoveryResult:
    result = Agent0HouseDiscoveryResult(
        job_id=job_id,
        address_id=address_id,
        polygon_address_id=polygon_row.id,
        polygon_source_ref=f"{polygon_row.source_file}#{polygon_row.id}",
        candidate_latitude=candidate.get("lat"),
        candidate_longitude=candidate.get("lon"),
        reverse_geocoded_address=(geocode or {}).get("address"),
        reverse_geocoded_latitude=(geocode or {}).get("latitude"),
        reverse_geocoded_longitude=(geocode or {}).get("longitude"),
        status=status,
        confidence=(geocode or {}).get("confidence"),
        dedupe_reason=dedupe_reason,
        raw_data={
            "candidate": candidate,
            "geocode": {k: v for k, v in (geocode or {}).items() if k != "raw"},
            "google_raw": (geocode or {}).get("raw"),
            "polygon_metadata": {
                "address_id": polygon_row.id,
                "source_file": polygon_row.source_file,
                "source_layer": polygon_row.source_layer,
            },
        },
    )
    session.add(result)
    session.flush()
    return result


def _upsert_agent_result(session, *, job_id: str, address_id: int, data: dict[str, Any]) -> None:
    now = datetime.utcnow()
    stmt = _pg_insert(AgentResult).values(
        agent_name=AGENT_NAME,
        job_id=job_id,
        address_id=address_id,
        data=data,
        created_at=now,
        updated_at=now,
    ).on_conflict_do_update(
        constraint="uq_agent_results_agent_address",
        set_={"data": data, "job_id": job_id, "updated_at": now},
    )
    session.execute(stmt)


def _create_address_from_geocode(
    session,
    *,
    job_id: str,
    polygon_row: Address,
    geocode: dict[str, Any],
    address_override: str | None = None,
    extra_metadata: dict[str, Any] | None = None,
) -> Address:
    address_text = str(address_override or geocode.get("address") or "").strip()
    lat = float(geocode["latitude"])
    lon = float(geocode["longitude"])
    meta = {
        "agent0_discovered": True,
        "agent0_polygon_address_id": polygon_row.id,
        "agent0_source": "house_discovery",
        "geometry_type": "Point",
        "category": "household",
        "map_layer_only": False,
        "file_role": "geospatial",
        "merge_status": "new",
        "merge_color": "yellow",
        "rule_status": "new",
        "merge_reason": "New address discovered by Agent 0 inside polygon",
        "source_format": "agent0",
        "original_source_file": polygon_row.source_file,
    }
    if extra_metadata:
        meta.update(extra_metadata)
    row = Address(
        job_id=job_id,
        customer_id=polygon_row.customer_id,
        raw_address=address_text,
        latitude=lat,
        longitude=lon,
        source_raw_address=address_text,
        source_latitude=lat,
        source_longitude=lon,
        normalized_key=normalize_address_key(address_text),
        source_file=polygon_row.source_file or "agent0_house_discovery",
        source_sheet=None,
        source_layer="Agent 0 House Discovery",
        source_row_number=None,
        raw_metadata=meta,
    )
    set_address_geom(row, lon, lat)
    session.add(row)
    session.flush()
    return row


def _is_near_existing(
    coords: list[tuple[float, float]],
    lat: float,
    lon: float,
    threshold_m: float,
) -> bool:
    return any(_distance_meters(lat, lon, old_lat, old_lon) <= threshold_m for old_lat, old_lon in coords)


def _create_multi_unit_suffix_rows(
    session,
    *,
    job_id: str,
    polygon_row: Address,
    candidate: dict[str, float],
    geocode: dict[str, Any],
    unit_count: int,
    max_units: int,
    existing_keys: set[str],
    existing_coords: list[tuple[float, float]],
    inserted_ids: list[int],
    attom_info: dict[str, Any],
) -> int:
    base_address = str(geocode.get("address") or "").strip()
    if not _LEADING_STREET_NUMBER_RE.match(base_address):
        return 0
    created = 0
    capped_units = max(2, min(int(unit_count), int(max_units)))
    for unit_index in range(1, capped_units + 1):
        suffixed = _suffix_address(base_address, unit_index)
        if not suffixed or suffixed == base_address:
            continue
        key = normalize_address_key(suffixed)
        if key and key in existing_keys:
            continue
        extra = {
            "agent0_multi_unit_generated": True,
            "agent0_base_address": base_address,
            "agent0_unit_suffix": str(unit_index),
            "agent0_unit_count_source": "attom",
            "agent0_unit_count": int(unit_count),
            "agent0_multi_unit_reason": attom_info.get("reason") or "attom_units_count",
            "attom_id": attom_info.get("attom_id"),
            "attom_address": attom_info.get("attom_address"),
            "attom_property_type": attom_info.get("attom_property_type"),
            "merge_reason": f"New unit address generated by Agent 0 from ATTOM unit count ({unit_index}/{unit_count})",
        }
        address_row = _create_address_from_geocode(
            session,
            job_id=job_id,
            polygon_row=polygon_row,
            geocode=geocode,
            address_override=suffixed,
            extra_metadata=extra,
        )
        discovery = _record_discovery(
            session,
            job_id=job_id,
            polygon_row=polygon_row,
            candidate=candidate,
            status="accepted",
            geocode={**geocode, "address": suffixed},
            address_id=address_row.id,
            dedupe_reason="multi-unit suffix generated from ATTOM",
        )
        data = {
            "status": "accepted",
            "match_status": "new_address",
            "confidence": geocode.get("confidence"),
            "discovered_address": address_row.raw_address,
            "base_address": base_address,
            "unit_suffix": str(unit_index),
            "unit_count": int(unit_count),
            "latitude": address_row.latitude,
            "longitude": address_row.longitude,
            "polygon_address_id": polygon_row.id,
            "discovery_result_id": discovery.id,
            "source": "attom_multi_unit_suffix",
        }
        _upsert_agent_result(session, job_id=job_id, address_id=address_row.id, data=data)
        if key:
            existing_keys.add(key)
        existing_coords.append((float(geocode["latitude"]), float(geocode["longitude"])))
        inserted_ids.append(address_row.id)
        created += 1
    return created


def run_agent0_for_job(
    job_id: str,
    *,
    progress_callback: ProgressCallback = None,
    agent_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run Agent 0 for one ingestion job and insert unique discovered households."""
    log_path = configure_agent_logger(logger, AGENT_NAME)
    opts = _merge_options(agent_options)
    logger.info("=" * 72)
    logger.info("Agent0 START: job_id=%r options=%s log=%s", job_id, opts, log_path)
    if not opts.get("enabled", True):
        logger.info("Agent0 SKIP: agent disabled")
        return {"skipped": True, "reason": "agent disabled"}
    if Polygon is None or Point is None:
        logger.info("Agent0 SKIP: shapely is not installed")
        return {"skipped": True, "reason": "shapely is not installed"}
    if not opts.get("reverse_geocode", True):
        logger.info("Agent0 SKIP: reverse geocode disabled")
        return {"skipped": True, "reason": "reverse geocode disabled"}

    api_key = _google_api_key()
    if not api_key:
        logger.info("Agent0 SKIP: missing Google geocoding API key")
        return {"skipped": True, "reason": "GOOGLE_GEOCODING_API_KEY or GOOGLE_MAPS_API_KEY is not set"}

    max_job = max(0, int(opts["max_candidates_per_job"]))
    max_poly = max(0, int(opts["max_candidates_per_polygon"]))
    grid_step = float(opts["grid_step"])
    dedup_distance_m = float(opts["dedup_distance_m"])
    multi_unit_suffixing = bool(opts.get("multi_unit_suffixing", True))
    max_units_per_base = int(opts.get("max_units_per_base_address", 4))
    counts = {
        "polygons": 0,
        "candidates_generated": 0,
        "reverse_geocoded": 0,
        "accepted": 0,
        "multi_unit_generated": 0,
        "duplicate": 0,
        "outside_polygon": 0,
        "geocode_failed": 0,
    }
    inserted_ids: list[int] = []

    session = get_session_factory()()
    try:
        _ensure_agent_table(session)
        polygons = _polygon_rows(session, job_id)
        counts["polygons"] = len(polygons)
        if not polygons:
            session.commit()
            logger.info("Agent0 SKIP: no polygon map layers")
            return {**counts, "inserted_address_ids": inserted_ids, "skipped": True, "reason": "no polygon map layers"}

        existing_keys, existing_coords = _existing_address_keys(session, job_id)
        existing_kml_points = (
            _existing_kml_point_candidates(session, job_id)
            if opts.get("include_existing_kml_points", True)
            else []
        )
        kml_reverse_address_keys = {p.get("address_key") for p in existing_kml_points if p.get("address_key")}
        original_coord_keys = {(round(float(p["lat"]), 6), round(float(p["lon"]), 6)) for p in existing_kml_points}
        logger.info(
            "Agent0 INPUT: polygons=%d existing_addresses=%d existing_coords=%d existing_kml_points=%d",
            len(polygons), len(existing_keys), len(existing_coords), len(existing_kml_points),
        )
        processed = 0
        reverse_workers = int(opts.get("reverse_workers", 8) or 1)
        use_reverse_cache = bool(opts.get("reverse_geocode_cache", True))
        reverse_geocode_fn = _cached_reverse_geocoder(api_key, enabled=use_reverse_cache)
        for polygon_row in polygons:
            if max_job > 0 and processed >= max_job:
                break
            meta = polygon_row.raw_metadata if isinstance(polygon_row.raw_metadata, dict) else {}
            polygon = parse_polygon_coordinates(meta.get("coordinates"))
            if polygon is None:
                continue

            remaining: int | None = None
            if max_job > 0:
                remaining = max(0, max_job - processed)
            if max_poly > 0:
                remaining = min(remaining, max_poly) if remaining is not None else max_poly

            generated_candidates = [
                {**candidate, "source": "generated_candidate"}
                for candidate in generate_candidate_points(polygon, grid_step=grid_step, max_points=remaining)
            ]
            polygon_kml_points = [
                point for point in existing_kml_points
                if polygon.contains(Point(float(point["lon"]), float(point["lat"])))
            ]
            combined_candidates = _deduplicate_points(
                [*polygon_kml_points, *generated_candidates],
                threshold_m=dedup_distance_m,
            )
            if max_job > 0:
                combined_candidates = combined_candidates[: max(0, max_job - processed)]

            counts["candidates_generated"] += len(generated_candidates)
            counts["existing_kml_points"] = counts.get("existing_kml_points", 0) + len(polygon_kml_points)
            counts["candidates_after_dedup"] = counts.get("candidates_after_dedup", 0) + len(combined_candidates)
            log_payload(
                logger,
                f"Agent0 POLYGON address_id={polygon_row.id} candidates",
                {
                    "polygon_address_id": polygon_row.id,
                    "source_file": polygon_row.source_file,
                    "generated_candidate_count": len(generated_candidates),
                    "existing_kml_point_count": len(polygon_kml_points),
                    "deduped_candidate_count": len(combined_candidates),
                    "sample": combined_candidates[:10],
                },
            )

            geocode_results = _parallel_reverse_geocode_candidates(
                combined_candidates,
                api_key=api_key,
                workers=reverse_workers,
                use_cache=use_reverse_cache,
                progress_callback=progress_callback,
                processed_start=processed,
                progress_total=max_job or processed + len(combined_candidates) or 1,
                geocode_fn=reverse_geocode_fn,
            )
            processed += len(combined_candidates)

            for candidate_idx, (candidate, geocode, error) in enumerate(geocode_results, start=1):
                candidate_source = str(candidate.get("source") or "generated_candidate")

                if not geocode or not geocode.get("address"):
                    try:
                        geocode = _attom_geocode_for_point(candidate["lat"], candidate["lon"])
                    except Exception as exc:
                        logger.warning("Agent 0 ATTOM fallback lookup failed for %s: %s", candidate, exc)
                        geocode = None

                if not geocode or not geocode.get("address"):
                    if candidate_source != "existing_kml_point":
                        _record_discovery(
                            session,
                            job_id=job_id,
                            polygon_row=polygon_row,
                            candidate=candidate,
                            status="geocode_failed",
                            geocode=geocode,
                            dedupe_reason=error,
                        )
                        counts["geocode_failed"] += 1
                    else:
                        counts["existing_kml_geocode_failed"] = counts.get("existing_kml_geocode_failed", 0) + 1
                    continue

                counts["reverse_geocoded"] += 1
                log_payload(
                    logger,
                    f"Agent0 GEOCODE polygon={polygon_row.id} candidate={candidate_idx} result",
                    {k: v for k, v in geocode.items() if k != "raw"},
                )
                out_lat = float(geocode["latitude"])
                out_lon = float(geocode["longitude"])
                geocode_key = normalize_address_key(geocode.get("address"))

                if candidate_source == "existing_kml_point":
                    if geocode_key:
                        kml_reverse_address_keys.add(geocode_key)
                        existing_keys.add(geocode_key)
                    counts["existing_kml_reverse_geocoded"] = counts.get("existing_kml_reverse_geocoded", 0) + 1
                    continue

                if not polygon.contains(Point(out_lon, out_lat)):
                    _record_discovery(
                        session,
                        job_id=job_id,
                        polygon_row=polygon_row,
                        candidate=candidate,
                        status="outside_polygon",
                        geocode=geocode,
                        dedupe_reason="reverse geocode point outside source polygon",
                    )
                    counts["outside_polygon"] += 1
                    continue

                coord_key = (round(out_lat, 6), round(out_lon, 6))
                if coord_key in original_coord_keys:
                    _record_discovery(
                        session,
                        job_id=job_id,
                        polygon_row=polygon_row,
                        candidate=candidate,
                        status="duplicate",
                        geocode=geocode,
                        dedupe_reason="reverse geocode coordinate matches uploaded KML point",
                    )
                    counts["duplicate"] += 1
                    continue

                if geocode_key and geocode_key in kml_reverse_address_keys:
                    _record_discovery(
                        session,
                        job_id=job_id,
                        polygon_row=polygon_row,
                        candidate=candidate,
                        status="duplicate",
                        geocode=geocode,
                        dedupe_reason="reverse geocoded address already represented by uploaded KML point",
                    )
                    counts["duplicate"] += 1
                    continue

                attom_units: dict[str, Any] = {"units_count": 0}
                if multi_unit_suffixing:
                    try:
                        attom_units = geocode.get("attom_info") or _attom_units_for_point(out_lat, out_lon)
                    except Exception as exc:
                        logger.warning("Agent 0 ATTOM multi-unit lookup failed for %s: %s", geocode.get("address"), exc)
                        attom_units = {"units_count": 0, "reason": str(exc)[:200]}
                    unit_count = int(attom_units.get("units_count") or 0)
                    if unit_count >= 2:
                        created_units = _create_multi_unit_suffix_rows(
                            session,
                            job_id=job_id,
                            polygon_row=polygon_row,
                            candidate=candidate,
                            geocode=geocode,
                            unit_count=unit_count,
                            max_units=max_units_per_base,
                            existing_keys=existing_keys,
                            existing_coords=existing_coords,
                            inserted_ids=inserted_ids,
                            attom_info=attom_units,
                        )
                        if created_units:
                            counts["accepted"] += created_units
                            counts["multi_unit_generated"] += created_units
                            continue

                key = geocode_key
                if key and key in existing_keys:
                    _record_discovery(
                        session,
                        job_id=job_id,
                        polygon_row=polygon_row,
                        candidate=candidate,
                        status="duplicate",
                        geocode=geocode,
                        dedupe_reason="normalized address already exists in this job",
                    )
                    counts["duplicate"] += 1
                    continue

                if _is_near_existing(existing_coords, out_lat, out_lon, dedup_distance_m):
                    _record_discovery(
                        session,
                        job_id=job_id,
                        polygon_row=polygon_row,
                        candidate=candidate,
                        status="duplicate",
                        geocode=geocode,
                        dedupe_reason=f"coordinate within {dedup_distance_m:g}m of existing household",
                    )
                    counts["duplicate"] += 1
                    continue

                address_row = _create_address_from_geocode(
                    session,
                    job_id=job_id,
                    polygon_row=polygon_row,
                    geocode=geocode,
                )
                discovery = _record_discovery(
                    session,
                    job_id=job_id,
                    polygon_row=polygon_row,
                    candidate=candidate,
                    status="accepted",
                    geocode=geocode,
                    address_id=address_row.id,
                )
                discovery.address_id = address_row.id
                flag_modified(discovery, "raw_data")
                data = {
                    "status": "accepted",
                    "match_status": "new_address",
                    "confidence": geocode.get("confidence"),
                    "discovered_address": address_row.raw_address,
                    "latitude": address_row.latitude,
                    "longitude": address_row.longitude,
                    "polygon_address_id": polygon_row.id,
                    "discovery_result_id": discovery.id,
                    "source": geocode.get("provider") or "google_reverse_geocode",
                }
                _upsert_agent_result(session, job_id=job_id, address_id=address_row.id, data=data)
                if key:
                    existing_keys.add(key)
                existing_coords.append((out_lat, out_lon))
                inserted_ids.append(address_row.id)
                counts["accepted"] += 1
        session.commit()
        summary = {**counts, "total": counts["candidates_generated"], "inserted_address_ids": inserted_ids}
        logger.info("Agent0 COMPLETE: %s", summary)
        logger.info("=" * 72)
        return summary
    except Exception:
        session.rollback()
        logger.exception("Agent 0 failed for job %s", job_id)
        raise
    finally:
        session.close()





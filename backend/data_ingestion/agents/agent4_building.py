"""
Agent 4 - Building Classification
=================================
Uses ATTOM Data API as the **primary** classifier for SFU / MDU / Commercial
granularity.  If ATTOM returns no usable result for an address, the pipeline
falls back to the existing Microsoft-footprint reference agent.

Input  : ``addresses`` table (DB columns + ``raw_metadata``)
Output : ``agent_results`` (agent_name = "agent4_building")
         ``addresses.raw_metadata['buildings_address']``  (Agent 4 only)

Environment variables
---------------------
ATTOM_API_KEY          ATTOM property-detail API key  (required for primary path)
ATTOM_API_BASE_URL     Override base URL              (default: https://api.gateway.attomdata.com)
AGENT4_FOOTPRINTS_DIR  Override footprints cache dir
AGENT4_MAX_WORKERS     Worker count for reference agent (default: 1)
"""
from __future__ import annotations
from data_ingestion.config.paths import BUILDING_AGENT_VENDOR, PROJECT_ROOT
 
from data_ingestion.utils.agent_logging import configure_agent_logger, log_payload
import hashlib
import json
import logging
import os
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from contextlib import contextmanager
from threading import RLock
from datetime import datetime
from pathlib import Path
from typing import Any
 
from sqlalchemy import select as _sel
 
from data_ingestion.database.db import get_session_factory
from data_ingestion.database.models import Address, Agent1Result, AgentResult, AgentTable
from data_ingestion.utils.address_metadata import persist_buildings_address_in_raw_metadata
from data_ingestion.utils.agent_db_input import db_input_bundle
from data_ingestion.utils.agent1_input import resolve_agent1_input
 
logger = logging.getLogger(__name__)
 
_AGENT_NAME = "agent4_building"
_DISPLAY_NAME = "Agent 4: Building Classification"
_PROJECT_ROOT = PROJECT_ROOT
_REFERENCE_ROOT = BUILDING_AGENT_VENDOR
_REFERENCE_CONFIG = _REFERENCE_ROOT / "config" / "settings.example.yaml"
_SV_META_URL = "https://maps.googleapis.com/maps/api/streetview/metadata"

_env_file = _PROJECT_ROOT / ".env"
if _env_file.exists():
    from dotenv import load_dotenv
    load_dotenv(_env_file, override=False)

# ---------------------------------------------------------------------------
# ATTOM Data API constants
# ---------------------------------------------------------------------------

# ⚠️  Replace this placeholder with your real key or set ATTOM_API_KEY in .env
_ATTOM_API_KEY_PLACEHOLDER = "YOUR_ATTOM_API_KEY_HERE"

_ATTOM_GATEWAY_URL = "https://api.gateway.attomdata.com"
_ATTOM_PROPERTY_DETAIL_PATH = "/propertyapi/v1.0.0/property/detail"
_ATTOM_LATLON_RADIUS_MI = float(os.getenv("ATTOM_LATLON_RADIUS_MI", "0.02"))
_ATTOM_LATLON_MAX_DISTANCE_MI = float(os.getenv("ATTOM_LATLON_MAX_DISTANCE_MI", "0.015"))

# ATTOM land-use codes that map to MDU (multi-family / apartment)
_ATTOM_MDU_LAND_USE_CODES = frozenset({
    "DUPLEX", "TRIPLEX", "QUADRUPLEX",
    "APARTMENT", "MULTI-FAMILY", "MULTIFAMILY",
    "CONDOMINIUM", "CONDO",
    "TOWNHOUSE", "TOWNHOME",
    "COOPERATIVE", "CO-OP",
    "MOBILE HOME PARK", "MANUFACTURED HOUSING",
    "PLANNED UNIT DEVELOPMENT",
})

# ATTOM land-use codes that map to Commercial
_ATTOM_COMMERCIAL_LAND_USE_CODES = frozenset({
    "COMMERCIAL", "OFFICE", "RETAIL", "INDUSTRIAL",
    "WAREHOUSE", "MIXED USE", "HOTEL", "MOTEL",
    "RESTAURANT", "STORE", "SHOPPING CENTER",
    "GOVERNMENT", "INSTITUTIONAL", "RELIGIOUS",
    "SCHOOL", "HOSPITAL", "AGRICULTURAL",
    "VACANT LAND", "VACANT COMMERCIAL",
})

# ---------------------------------------------------------------------------
# Module-level cache state (Microsoft reference agent)
# ---------------------------------------------------------------------------
_REFERENCE_AGENT_CACHE: Any | None = None
_REFERENCE_AGENT_STATES: set[str] = set()
_REFERENCE_AGENT_LOCK = RLock()
_REFERENCE_AGENT_BBOX: tuple[float, float, float, float] | None = None
_BBOX_REUSE_THRESHOLD_DEG: float = 0.5

_AGENT4_LOADER_MODULE: Any | None = None
_AGENT4_LOADER_ORIGINAL: Any | None = None
_AGENT4_JOB_BBOX: tuple[float, float, float, float] | None = None
_AGENT4_DOWNLOAD_MODULE: Any | None = None
_AGENT4_DOWNLOAD_ORIGINAL: Any | None = None
_AGENT4_DATASET_LINKS_CACHE: Any | None = None

_FOOTPRINTS_DIR = _PROJECT_ROOT / "data" / "footprints"
_AGENT4_SMALL_BATCH_SIZE = int(os.getenv("AGENT4_SMALL_BATCH_SIZE", "25"))
_AGENT4_SMALL_BBOX_BUFFER_DEG = float(os.getenv("AGENT4_SMALL_BBOX_BUFFER_DEG", "0.003"))
_AGENT4_BBOX_BUFFER_DEG = float(os.getenv("AGENT4_BBOX_BUFFER_DEG", "0.01"))
_AGENT4_TILE_LEVEL = int(os.getenv("AGENT4_TILE_LEVEL", "9"))


# ===========================================================================
# ██████████████████████  ATTOM DATA API  ███████████████████████████████████
# ===========================================================================

def _attom_api_key() -> str:
    """Return the ATTOM API key from environment, falling back to placeholder."""
    return os.getenv("ATTOM_API_KEY", _ATTOM_API_KEY_PLACEHOLDER)


def _attom_base_url() -> str:
    """ATTOM gateway host (read at call time so .env / workers see updates)."""
    return os.getenv("ATTOM_API_BASE_URL", _ATTOM_GATEWAY_URL).rstrip("/")


def _attom_ssl_context() -> ssl.SSLContext:
    if os.environ.get("FTTH_SSL_VERIFY", "1").strip().lower() in {"0", "false", "no", "off"}:
        return ssl._create_unverified_context()
    return ssl.create_default_context()


def _attom_headers() -> dict[str, str]:
    return {
        "Accept": "application/json",
        "apikey": _attom_api_key(),
    }


def _attom_get(path: str, params: dict[str, str], timeout: int = 15) -> dict[str, Any]:
    """
    Perform a GET request against the ATTOM API.

    Returns the parsed JSON body.
    Raises ``RuntimeError`` on HTTP or network errors.
    """
    qs = urllib.parse.urlencode(params)
    url = f"{_attom_base_url()}{path}?{qs}"
    req = urllib.request.Request(url, headers=_attom_headers())
    try:
        with urllib.request.urlopen(
            req, timeout=timeout, context=_attom_ssl_context()
        ) as resp:
            body = resp.read().decode("utf-8")
        return json.loads(body)
    except urllib.error.HTTPError as exc:
        body_text = ""
        try:
            body_text = exc.read().decode("utf-8")[:200]
        except Exception:
            pass
        raise RuntimeError(
            f"ATTOM HTTP {exc.code} for {path}: {body_text}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"ATTOM network error for {path}: {exc.reason}") from exc
    except Exception as exc:
        raise RuntimeError(f"ATTOM unexpected error for {path}: {exc}") from exc


# ---------------------------------------------------------------------------
# ATTOM result parsing helpers
# ---------------------------------------------------------------------------

def _attom_classify_land_use(land_use_raw: str) -> str:
    """
    Map a raw ATTOM landUse string to one of: SFH | MDU | Commercial | Unknown.

    SFH tokens are checked first so explicit single-family propType/propLandUse
    values are never overridden by MDU substring matches (e.g. "CO" inside "SFR").
    """
    upper = land_use_raw.upper().strip()

    sfh_tokens = (
        "SINGLE FAMILY", "SINGLE-FAMILY", "SFR", "SFH",
        "RESIDENTIAL", "RURAL RESIDENCE", "SINGLE FAMILY RESIDENCE",
    )
    if any(token in upper for token in sfh_tokens):
        return "SFH"

    for code in _ATTOM_MDU_LAND_USE_CODES:
        if re.search(r"\b" + re.escape(code) + r"\b", upper):
            return "MDU"

    for code in _ATTOM_COMMERCIAL_LAND_USE_CODES:
        if re.search(r"\b" + re.escape(code) + r"\b", upper):
            return "Commercial"

    return "Unknown"


def _attom_house_number(address: str) -> str | None:
    match = re.match(r"\s*(\d+)", address or "")
    return match.group(1) if match else None


def _attom_address_tokens(address: str) -> set[str]:
    return set(re.findall(r"[A-Z0-9]+", (address or "").upper()))


_STREET_STOPWORDS = frozenset({
    "REDDICK", "FL", "FLORIDA", "USA", "US", "MARION", "GA", "GEORGIA",
    "ST", "STREET", "RD", "ROAD", "DR", "DRIVE", "AVE", "AVENUE", "TER",
    "TERRACE", "LN", "LANE", "CT", "COURT", "BLVD", "BOULEVARD", "HWY",
    "HIGHWAY", "NW", "NE", "SW", "SE", "N", "S", "E", "W",
})


def _attom_address_matches_strict(target: str, attom_one_line: str) -> bool:
    """Require exact house number when target has one; reject neighbor parcels."""
    if not target or not attom_one_line:
        return True

    target_u = target.upper()
    attom_u = attom_one_line.upper()
    tnum = _attom_house_number(target_u)
    anum = _attom_house_number(attom_u)
    if tnum and anum and tnum != anum:
        return False

    t_tokens = _attom_address_tokens(target_u)
    a_tokens = _attom_address_tokens(attom_u)
    if tnum:
        t_tokens.discard(tnum)
    if anum:
        a_tokens.discard(anum)

    t_street = {tok for tok in t_tokens if tok not in _STREET_STOPWORDS and not tok.isdigit()}
    a_street = {tok for tok in a_tokens if tok not in _STREET_STOPWORDS and not tok.isdigit()}
    if t_street and a_street and not (t_street & a_street):
        return False
    return True


def _attom_city_state_zip_line(
    addr: Address | None,
    a1: Agent1Result | None = None,
    a2: dict[str, Any] | None = None,
) -> str:
    a2 = a2 or {}
    if addr and getattr(addr, "validated_city_state", None):
        zipc = getattr(addr, "validated_postcode", None) or getattr(addr, "zip_code", None)
        cs = str(addr.validated_city_state).strip()
        return f"{cs} {zipc}".strip() if zipc else cs
    city = (getattr(addr, "city", None) if addr else None) or a2.get("city")
    state = (getattr(addr, "state", None) if addr else None) or a2.get("state")
    zipc = (
        (getattr(addr, "zip_code", None) if addr else None)
        or (getattr(addr, "validated_postcode", None) if addr else None)
        or a2.get("postal_code")
    )
    if city and state:
        return f"{city}, {state} {zipc or ''}".strip()
    return ""


def _attom_address_candidates(
    addr: Address | None,
    a1: Agent1Result | None,
    a2: dict[str, Any] | None,
    primary: str,
) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []

    def add(value: str | None) -> None:
        text = (value or "").strip()
        key = text.upper()
        if text and key not in seen:
            seen.add(key)
            out.append(text)

    add(primary)
    if addr:
        add(getattr(addr, "validated_raw_address", None))
        add(getattr(addr, "raw_address", None))
    if a2:
        add(a2.get("formatted_address"))
    if a1:
        add(a1.chosen_standardized_address)

    if addr:
        csz = _attom_city_state_zip_line(addr, a1, a2)
        street = getattr(addr, "validated_street_line", None) or getattr(addr, "raw_address", None)
        if street and csz:
            add(f"{street}, {csz}")
        if primary and csz and "," not in primary:
            add(f"{primary}, {csz}")
    return out


def _attom_address_param_variants(
    address: str,
    addr: Address | None = None,
    a1: Agent1Result | None = None,
    a2: dict[str, Any] | None = None,
) -> list[tuple[dict[str, str], str]]:
    variants: list[tuple[dict[str, str], str]] = []
    seen: set[tuple[tuple[str, str], ...]] = set()

    def add(params: dict[str, str], label: str) -> None:
        key = tuple(sorted(params.items()))
        if key not in seen:
            seen.add(key)
            variants.append((params, label))

    parts = [p.strip() for p in address.split(",", 1)]
    street = parts[0]
    tail = parts[1] if len(parts) > 1 else ""

    if street and tail:
        add({"address1": street, "address2": tail}, "address1+address2")

    csz = _attom_city_state_zip_line(addr, a1, a2) if addr else ""
    if street and csz:
        add({"address1": street, "address2": csz}, "address1+address2+csz")

    add({"address": address}, "address")
    return variants


def _attom_address_match_score(target: str, prop_one_line: str) -> int:
    target_u = (target or "").upper()
    prop_u = (prop_one_line or "").upper()
    if not target_u or not prop_u:
        return 0

    score = 0
    tnum = _attom_house_number(target_u)
    pnum = _attom_house_number(prop_u)
    if tnum and pnum:
        if tnum == pnum:
            score += 100
        else:
            return -1

    t_tokens = _attom_address_tokens(target_u)
    p_tokens = _attom_address_tokens(prop_u)
    score += min(len(t_tokens & p_tokens) * 5, 40)
    if target_u in prop_u or prop_u in target_u:
        score += 20
    return score


def _attom_pick_best_property(
    prop_list: list[dict[str, Any]],
    target_address: str,
    max_distance_mi: float | None = None,
) -> dict[str, Any] | None:
    """Pick the parcel whose house number and street match the target address."""
    if not prop_list:
        return None

    target_num = _attom_house_number(target_address)
    ranked: list[tuple[int, float, dict[str, Any]]] = []

    for prop in prop_list:
        one_line = (prop.get("address") or {}).get("oneLine") or ""
        if not _attom_address_matches_strict(target_address, one_line):
            continue

        location = prop.get("location") or prop.get("Location") or {}
        try:
            distance_mi = float(location.get("distance")) if location.get("distance") is not None else 0.0
        except (TypeError, ValueError):
            distance_mi = 0.0

        if max_distance_mi is not None and distance_mi > max_distance_mi:
            continue

        score = _attom_address_match_score(target_address, one_line)
        if score < 0:
            continue
        ranked.append((score, -distance_mi, prop))

    if not ranked:
        if target_num:
            logger.debug(
                "ATTOM no parcel with house number %s near target %r",
                target_num,
                target_address,
            )
        return None

    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return ranked[0][2]


def _parse_attom_property_record(prop: dict[str, Any]) -> dict[str, Any] | None:
    """Parse a single ATTOM property record."""
    try:
        summary_block = prop.get("summary") or prop.get("Summary") or {}
        propclass = str(summary_block.get("propclass") or summary_block.get("propertyType") or "")
        land_use_raw = (
            summary_block.get("propLandUse")
            or summary_block.get("proptype")
            or summary_block.get("propSubType")
            or prop.get("landUse")
            or prop.get("LandUse")
            or ""
        )
        structure_type = _attom_classify_land_use(str(land_use_raw))
        if structure_type == "Unknown" and "SINGLE FAMILY" in propclass.upper():
            structure_type = "SFH"

        building = prop.get("building") or prop.get("Building") or {}
        rooms = building.get("rooms") or building.get("Rooms") or {}
        lot = prop.get("lot") or prop.get("Lot") or {}
        building_summary = building.get("summary") or building.get("Summary") or {}

        unit_count: int | None = None
        for src, key in (
            (rooms, "unitCount"),
            (rooms, "unitsCount"),
            (summary_block, "unitsCount"),
            (building, "unitsCount"),
            (building_summary, "unitsCount"),
            (building_summary, "bldgsNum"),
        ):
            raw = src.get(key)
            if raw is not None:
                try:
                    unit_count = int(float(raw))
                    break
                except (TypeError, ValueError):
                    continue

        if structure_type == "Unknown" and unit_count is not None and unit_count >= 2:
            structure_type = "MDU"

        if structure_type == "MDU" and unit_count is None:
            upper_lu = str(land_use_raw).upper()
            if "DUPLEX" in upper_lu:
                unit_count = 2
            elif "TRIPLEX" in upper_lu:
                unit_count = 3
            elif "QUADPLEX" in upper_lu or "QUADRUPLEX" in upper_lu:
                unit_count = 4
            else:
                raw_bldgs = building_summary.get("bldgsNum")
                if raw_bldgs is not None:
                    try:
                        unit_count = int(float(raw_bldgs))
                    except (TypeError, ValueError):
                        pass

        if structure_type == "SFH" and unit_count is None:
            unit_count = 1

        lot_size_sqft: float | None = None
        raw_lot = lot.get("lotSize1") or lot.get("lotSizeAcres") or lot.get("lotsize1")
        if raw_lot is not None:
            try:
                lot_size_sqft = float(raw_lot)
            except (TypeError, ValueError):
                pass

        building_sqft: float | None = None
        size_block = building.get("size") or {}
        raw_bldg = (
            size_block.get("livingSize")
            or size_block.get("livingsize")
            or building.get("buildingSize")
            or size_block.get("bldgsize")
        )
        if raw_bldg is not None:
            try:
                building_sqft = float(raw_bldg)
            except (TypeError, ValueError):
                pass

        floor_count: int | None = None
        construction = building.get("construction") or {}
        for src, key in (
            (building, "stories"),
            (building, "storyCount"),
            (construction, "floors"),
            (construction, "stories"),
            (building_summary, "levels"),
            (summary_block, "levels"),
        ):
            raw = src.get(key)
            if raw is not None:
                try:
                    floor_count = int(float(raw))
                    break
                except (TypeError, ValueError):
                    continue

        if floor_count is None and structure_type == "SFH":
            floor_count = 1

        confidence = 90 if structure_type not in ("Unknown",) else 60
        attom_address = (prop.get("address") or {}).get("oneLine") or ""

        return {
            "source": "attom",
            "structure_type": structure_type,
            "is_mdu": structure_type == "MDU",
            "land_use_raw": str(land_use_raw),
            "unit_count": unit_count,
            "floor_count": floor_count,
            "lot_size_sqft": lot_size_sqft,
            "building_sqft": building_sqft,
            "confidence": confidence,
            "building_matched": structure_type != "Unknown",
            "attom_matched_address": attom_address,
        }
    except Exception as exc:
        logger.debug("ATTOM record parse error: %s", exc)
        return None


def _parse_attom_property(
    data: dict[str, Any],
    max_distance_mi: float | None = None,
    target_address: str | None = None,
) -> dict[str, Any] | None:
    """Parse ATTOM response; pick the parcel matching *target_address* when given."""
    try:
        prop_list = data.get("property") or data.get("Property") or []
        if not prop_list:
            return None
        if not isinstance(prop_list, list):
            prop_list = [prop_list]

        if target_address:
            prop = _attom_pick_best_property(prop_list, target_address, max_distance_mi)
        else:
            prop = prop_list[0]
            if max_distance_mi is not None:
                location = prop.get("location") or prop.get("Location") or {}
                try:
                    distance_mi = float(location.get("distance")) if location.get("distance") is not None else None
                except (TypeError, ValueError):
                    distance_mi = None
                if distance_mi is None or distance_mi > max_distance_mi:
                    return None

        if prop is None:
            return None
        return _parse_attom_property_record(prop)
    except Exception as exc:
        logger.debug("ATTOM response parse error: %s", exc)
        return None


# ---------------------------------------------------------------------------
# ATTOM address lookup  (primary entry point)
# ---------------------------------------------------------------------------

def _attom_try_detail(
    params: dict[str, str],
    label: str,
    max_distance_mi: float | None = None,
    target_address: str | None = None,
) -> dict[str, Any] | None:
    """Call ``/property/detail`` and return a parsed property, or None."""
    try:
        logger.debug("ATTOM detail lookup (%s): %s", label, params)
        data = _attom_get(_ATTOM_PROPERTY_DETAIL_PATH, params)
        return _parse_attom_property(
            data,
            max_distance_mi=max_distance_mi,
            target_address=target_address,
        )
    except RuntimeError as exc:
        logger.warning("ATTOM detail miss (%s): %s", label, exc)
        return None


def _attom_lookup_by_address(
    address: str,
    lat: float,
    lon: float,
    addr: Address | None = None,
    a1: Agent1Result | None = None,
    a2: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """
    Query ATTOM for property details at *address*.

    Never accepts a neighboring parcel with a different house number when
    lat/lon radius fallback is used (e.g. 15050 for target 15060).
    """
    result: dict[str, Any] | None = None
    candidates = (
        _attom_address_candidates(addr, a1, a2, address)
        if addr is not None
        else [address]
    )
    target = address or (candidates[0] if candidates else "")

    for candidate in candidates:
        if not candidate:
            continue
        for params, label in _attom_address_param_variants(candidate, addr, a1, a2):
            result = _attom_try_detail(params, label, target_address=target)
            if result is not None:
                break
        if result is not None:
            break

    if result is None and target:
        result = _attom_lookup_by_latlon(lat, lon, target_address=target)

    if result is not None:
        logger.info(
            "ATTOM hit: structure=%s confidence=%s address=%r matched=%r lat=%.6f lon=%.6f",
            result.get("structure_type"),
            result.get("confidence"),
            address,
            result.get("attom_matched_address"),
            lat,
            lon,
        )
    return result


def _attom_lookup_by_latlon(
    lat: float,
    lon: float,
    target_address: str | None = None,
) -> dict[str, Any] | None:
    """
    Secondary ATTOM lookup using lat/lon on ``/property/detail``.

    ATTOM requires a ``radius`` (miles) with latitude/longitude; results are
    sorted nearest-first. The nearest result is rejected if it's farther than
    _ATTOM_LATLON_MAX_DISTANCE_MI away - this prevents matching a neighboring
    parcel (e.g. an adjacent duplex/mobile home) when the target building
    itself has no separate ATTOM record at that exact point.
    """
    params = {
        "latitude": str(round(lat, 6)),
        "longitude": str(round(lon, 6)),
        "radius": str(_ATTOM_LATLON_RADIUS_MI),
    }
    return _attom_try_detail(
        params,
        "lat/lon+radius",
        max_distance_mi=_ATTOM_LATLON_MAX_DISTANCE_MI,
        target_address=target_address,
    )


def _attom_is_available() -> bool:
    """Return False when the placeholder key is still in place."""
    key = _attom_api_key()
    return bool(key) and key != _ATTOM_API_KEY_PLACEHOLDER


# ---------------------------------------------------------------------------
# Human-readable classification justification
# ---------------------------------------------------------------------------

_HINT_SIGNAL_LABELS: dict[str, str] = {
    "attom_land_use_classification": "ATTOM land-use classification",
    "invalid_area": "footprint area below minimum valid threshold",
    "far_from_building": "coordinates too far from nearest building footprint",
    "assessor_anchor_signal": "assessor anchor / institutional land-use signal",
    "mxu_land_use": "mixed-use land-use code",
    "unit_count_commercial_mix": "multiple units with commercial space",
    "unit_count_1": "unit count is 1",
    "unit_count_2_to_4": "unit count is 2–4",
    "unit_count_gte_5": "unit count is 5 or more",
    "unit_count_fallback": "unit-count fallback rule",
    "large_area_low_unit_density": "large footprint with low unit density",
    "land_use_sfu": "assessor land-use indicates single-family",
    "land_use_mdu_small": "assessor land-use indicates small multi-family",
    "land_use_mdu_large": "assessor land-use indicates large multi-family",
    "large_footprint_no_units": "very large footprint without unit data",
    "geometry_large_elongated": "large elongated footprint geometry",
    "geometry_large_moderate_elongation": "large footprint with moderate elongation",
    "geometry_large_compact": "large compact footprint geometry",
    "geometry_medium_elongated": "medium elongated footprint geometry",
    "geometry_moderate_elongation": "moderate elongation footprint geometry",
    "geometry_strip_block": "strip-block footprint geometry",
    "geometry_small_compact": "small compact footprint geometry",
    "geometry_default_sfu": "default single-family geometry rule",
    "coord_mismatch_footprint_rejected": "footprint rejected due to coordinate mismatch",
    "matched_residential_footprint_fallback": "residential footprint fallback match",
    "confidence_calibrated": "confidence calibrated from address/footprint quality",
}


def _format_hint_signals(signals: Any) -> str:
    if not signals:
        return ""
    labels: list[str] = []
    for signal in signals if isinstance(signals, (list, tuple)) else [signals]:
        if not isinstance(signal, str):
            continue
        labels.append(_HINT_SIGNAL_LABELS.get(signal, signal.replace("_", " ")))
    return "; ".join(labels)


def build_agent4_justification(data: dict[str, Any]) -> str:
    """Build a human-readable justification string from an Agent 4 result payload."""
    structure = data.get("structure_type") or data.get("structure_hint") or "Unknown"
    class_source = str(data.get("class_source") or data.get("source_agent") or "").strip()
    confidence = data.get("confidence")
    if confidence is None:
        confidence = data.get("hint_confidence")

    parts: list[str] = []
    is_attom = (
        class_source in ("ATTOM_API", "attom_data_api")
        or data.get("imagery_source") == "attom_property_api"
    )

    if is_attom:
        land_use = str(data.get("attom_land_use_raw") or "").strip()
        if data.get("building_matched"):
            base = f"Classified as {structure} via ATTOM property API"
            if land_use:
                base += f" (land use: {land_use})"
            parts.append(base)
        else:
            parts.append("No ATTOM property match; structure unresolved")
    elif not data.get("building_matched"):
        signals = _format_hint_signals(data.get("hint_signals"))
        if signals:
            parts.append(f"Building not matched — {signals}")
        else:
            parts.append("Building footprint not matched; structure unresolved")
    else:
        parts.append(f"Classified as {structure} via building footprint analysis")
        if class_source:
            parts.append(f"source: {class_source}")

        details: list[str] = []
        unit_count = data.get("unit_count")
        if unit_count is not None:
            details.append(f"unit count {unit_count}")
        area = data.get("footprint_area_m2")
        if area is not None:
            try:
                details.append(f"footprint area {float(area):.0f} m²")
            except (TypeError, ValueError):
                pass
        fp_dist = data.get("footprint_match_distance_m")
        if fp_dist is not None:
            try:
                details.append(f"match distance {float(fp_dist):.0f} m")
            except (TypeError, ValueError):
                pass
        if details:
            parts.append("based on " + ", ".join(details))

        signals = _format_hint_signals(data.get("hint_signals"))
        if signals:
            parts.append(f"signals: {signals}")

    if confidence is not None:
        try:
            parts.append(f"confidence {int(round(float(confidence)))}%")
        except (TypeError, ValueError):
            pass

    text = ". ".join(parts)
    return text if text.endswith(".") or not text else f"{text}."


# ---------------------------------------------------------------------------
# Convert ATTOM result → Agent 4 payload (same schema as Microsoft path)
# ---------------------------------------------------------------------------

def _attom_result_to_payload(
    attom: dict[str, Any],
    addr: Address,
    a1: Agent1Result | None,
    a2: dict[str, Any] | None = None,
    lat: float | None = None,
    lon: float | None = None,
) -> dict[str, Any]:
    """Normalise an ATTOM classification to the standard Agent 4 payload schema."""
    structure_type = attom.get("structure_type") or "Unknown"
    lookup_lat = lat if lat is not None else getattr(addr, "latitude", None)
    lookup_lon = lon if lon is not None else getattr(addr, "longitude", None)
    payload = {
        "status": "classified" if attom.get("building_matched") else "unmatched",
        "structure_type": structure_type,
        "is_mdu": structure_type == "MDU",
        "confidence": int(attom.get("confidence") or 0),
        "imagery_source": "attom_property_api",
        "latitude": lookup_lat,
        "longitude": lookup_lon,
        "address": _best_address(addr, a1, a2),
        "source_agent": "attom_data_api",
        "building_matched": bool(attom.get("building_matched")),
        "structure_hint": structure_type,
        "hint_confidence": float(attom.get("confidence") or 0),
        "hint_signals": ["attom_land_use_classification"],
        "class_source": "ATTOM_API",
        "matched_radius_m": None,
        "footprint_area_m2": None,
        "elongation_ratio": None,
        "perimeter_complexity": None,
        "footprint_source": "attom",
        "footprint_match_distance_m": None,
        "floor_count_est": attom.get("floor_count"),
        "unit_count": attom.get("unit_count"),
        "attom_land_use_raw": attom.get("land_use_raw"),
        "attom_lot_size_sqft": attom.get("lot_size_sqft"),
        "attom_building_sqft": attom.get("building_sqft"),
        "attom_matched_address": attom.get("attom_matched_address"),
    }
    payload["justification"] = build_agent4_justification(payload)
    return payload


# ===========================================================================
# ████████████████  MICROSOFT FOOTPRINT REFERENCE AGENT  ████████████████████
# ===========================================================================
# (unchanged from original — kept as fallback)
 
def _import_reference_agent():
    if not _REFERENCE_ROOT.exists():
        raise RuntimeError(f"Reference building agent not found: {_REFERENCE_ROOT}")
    ref_root = str(_REFERENCE_ROOT)
    if ref_root not in sys.path:
        sys.path.insert(0, ref_root)
    for module_name in list(sys.modules):
        if module_name == "src" or module_name.startswith("src."):
            module = sys.modules.get(module_name)
            module_file = Path(getattr(module, "__file__", "") or "")
            if _REFERENCE_ROOT not in module_file.parents:
                sys.modules.pop(module_name, None)
    from src.agent import BuildingDataAgent  # type: ignore
    return BuildingDataAgent
 
 
def _reference_state_from_lat_lon(lat: float, lon: float) -> str | None:
    ref_root = str(_REFERENCE_ROOT)
    if ref_root not in sys.path:
        sys.path.insert(0, ref_root)
    try:
        from scripts.download_footprints import _state_from_lat_lon  # type: ignore
        return str(_state_from_lat_lon(lat, lon))
    except Exception as exc:
        logger.debug("Unable to resolve footprint state for %.6f, %.6f: %s", lat, lon, exc)
        return None
 
 
def _states_for_records(records: list[dict[str, Any]]) -> set[str]:
    states: set[str] = set()
    for rec in records:
        try:
            lat = float(rec["lat"])
            lon = float(rec["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        state = _reference_state_from_lat_lon(lat, lon)
        if state:
            states.add(state)
    return states
 
 
def _bbox_for_records(
    records: list[dict[str, Any]],
    buffer_deg: float | None = None,
) -> tuple[float, float, float, float] | None:
    lats, lons = [], []
    for rec in records:
        try:
            lats.append(float(rec["lat"]))
            lons.append(float(rec["lon"]))
        except (KeyError, TypeError, ValueError):
            continue
    if not lats:
        return None
    if buffer_deg is None:
        buffer_deg = (
            _AGENT4_SMALL_BBOX_BUFFER_DEG
            if len(lats) <= _AGENT4_SMALL_BATCH_SIZE
            else _AGENT4_BBOX_BUFFER_DEG
        )
    return (
        min(lons) - buffer_deg,
        min(lats) - buffer_deg,
        max(lons) + buffer_deg,
        max(lats) + buffer_deg,
    )
 
 
def _bbox_within_cached(new_bbox: tuple[float, float, float, float] | None) -> bool:
    if _REFERENCE_AGENT_CACHE is None or _REFERENCE_AGENT_BBOX is None:
        return False
    if new_bbox is None:
        return True
    c_minx, c_miny, c_maxx, c_maxy = _REFERENCE_AGENT_BBOX
    n_minx, n_miny, n_maxx, n_maxy = new_bbox
    return n_minx >= c_minx and n_miny >= c_miny and n_maxx <= c_maxx and n_maxy <= c_maxy


def _agent4_footprints_dir() -> Path:
    custom = os.getenv("AGENT4_FOOTPRINTS_DIR", "").strip()
    return Path(custom) if custom else _FOOTPRINTS_DIR


def _ensure_reference_scripts_importable() -> None:
    ref_root = str(_REFERENCE_ROOT)
    if ref_root not in sys.path:
        sys.path.insert(0, ref_root)


def _quadkeys_for_bbox(bbox, level: int = 9) -> set[str]:
    import math
    _ensure_reference_scripts_importable()
    from scripts.download_footprints import lat_lon_to_quadkey  # type: ignore
    minx, miny, maxx, maxy = bbox
    n = 2 ** level
    lon_step = 360.0 / n
    lat_mid = (miny + maxy) / 2.0
    lat_step = 360.0 / n * max(math.cos(math.radians(lat_mid)), 0.05)
    keys: set[str] = set()
    lat = miny
    while lat <= maxy + 1e-9:
        lon = minx
        while lon <= maxx + 1e-9:
            keys.add(lat_lon_to_quadkey(lat, lon, level))
            lon += lon_step * 0.5
        lat += lat_step * 0.5
    return keys


def _quadkeys_for_records(records, level: int = 9) -> set[str]:
    _ensure_reference_scripts_importable()
    from scripts.download_footprints import lat_lon_to_quadkey  # type: ignore
    keys: set[str] = set()
    for rec in records:
        try:
            keys.add(lat_lon_to_quadkey(float(rec["lat"]), float(rec["lon"]), level))
        except (KeyError, TypeError, ValueError):
            continue
    if len(keys) == 0:
        bbox = _bbox_for_records(records)
    else:
        bbox = None
    if bbox is not None:
        keys |= _quadkeys_for_bbox(bbox, level=level)
    return keys


def _records_by_quadkey(records, level: int = 9) -> dict[str, list[dict[str, Any]]]:
    _ensure_reference_scripts_importable()
    from scripts.download_footprints import lat_lon_to_quadkey, _normalize_quadkey  # type: ignore
    groups: dict[str, list[dict[str, Any]]] = {}
    for rec in records or []:
        try:
            quadkey = lat_lon_to_quadkey(float(rec["lat"]), float(rec["lon"]), level)
            norm = _normalize_quadkey(quadkey)
        except (KeyError, TypeError, ValueError):
            continue
        groups.setdefault(norm, []).append(rec)
    return groups


def _geojson_has_footprints_in_bbox(path: Path, bbox) -> bool:
    if not path.exists() or path.stat().st_size <= 64:
        return False
    try:
        import geopandas as gpd
        minx, miny, maxx, maxy = bbox
        gdf = gpd.read_file(path, bbox=(minx, miny, maxx, maxy))
        return not gdf.empty
    except Exception:
        return False


def _tile_url_for_quadkey(us_df: Any, quadkey: str) -> str | None:
    _ensure_reference_scripts_importable()
    from scripts.download_footprints import _normalize_quadkey  # type: ignore
    norm = _normalize_quadkey(quadkey)
    matched = us_df[us_df["QuadKey"].map(_normalize_quadkey) == norm]
    if matched.empty:
        return None
    return str(matched.iloc[0]["Url"])


def _agent4_us_dataset_links() -> Any:
    global _AGENT4_DATASET_LINKS_CACHE
    if _AGENT4_DATASET_LINKS_CACHE is not None:
        return _AGENT4_DATASET_LINKS_CACHE

    _ensure_reference_scripts_importable()
    from scripts.download_footprints import _fetch_dataset_links, _filter_us_rows  # type: ignore

    _AGENT4_DATASET_LINKS_CACHE = _filter_us_rows(_fetch_dataset_links())
    return _AGENT4_DATASET_LINKS_CACHE


def _geojson_cache_ready(path: Path) -> bool:
    return path.exists() and path.stat().st_size > 64


def _convert_tile_gz_to_geojson(content: bytes, dest: Path) -> int:
    """
    Decompress a Microsoft building-footprint NDJSON.gz tile into a single
    GeoJSON FeatureCollection — with NO per-job bbox filtering.

    IMPORTANT (perf fix): this output is cached per-quadkey (see
    ``_download_footprints_for_bbox``), not per-job. The previous
    implementation ("_clip_tile_gz_to_geojson") re-ran this decompress +
    json-parse + Shapely ``intersects()`` pass for *every job* whose bbox
    touched the tile, even though the tile's raw bytes were already cached
    on disk — because the *output* filename embedded the job-specific bbox.
    That made the (often multi-second) decompress/parse step repeat on
    every single pipeline run that touched a given quadkey, which is the
    main reason "Microsoft footprints" looked slow.

    Converting the whole tile once and filtering later via
    ``gpd.read_file(path, bbox=...)`` (cheap, done by GDAL/fiona) turns
    every subsequent job that touches this quadkey into a cache hit instead
    of a full re-process. The trade-off: the *first* job to touch a brand
    new quadkey converts the whole tile (slightly more than the old
    job-clipped slice), but every later job/run reusing that quadkey is
    effectively free.
    """
    import gzip
    features: list[dict[str, Any]] = []
    for line in gzip.decompress(content).decode("utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            feat = json.loads(line)
            if feat.get("geometry"):
                features.append(feat)
        except Exception:
            continue
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}),
        encoding="utf-8",
    )
    return len(features)


@contextmanager
def _agent4_file_lock(lock_path: Path, timeout_s: float = 180.0):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    start = time.time()
    fd: int | None = None
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
            os.write(fd, str(os.getpid()).encode("ascii", errors="ignore"))
            break
        except FileExistsError:
            try:
                age_s = time.time() - lock_path.stat().st_mtime
                if age_s > timeout_s:
                    lock_path.unlink(missing_ok=True)
                    continue
            except FileNotFoundError:
                continue
            if time.time() - start > timeout_s:
                logger.warning("Agent4 cache lock timeout, continuing without lock: %s", lock_path)
                yield
                return
            time.sleep(0.25)
    try:
        yield
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except Exception:
                pass
        try:
            lock_path.unlink(missing_ok=True)
        except Exception:
            pass


def _raw_tile_cache_path(raw_dir: Path, norm_quadkey: str, url: str) -> Path:
    lower = url.lower()
    if lower.endswith(".csv.gz"):
        suffix = ".csv.gz"
    elif lower.endswith(".zip"):
        suffix = ".zip"
    else:
        suffix = ".bin"
    return raw_dir / f"{norm_quadkey}{suffix}"


def _read_or_download_tile_content(
    raw_dir: Path,
    norm_quadkey: str,
    url: str,
    download_fn: Any,
) -> bytes:
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_path = _raw_tile_cache_path(raw_dir, norm_quadkey, url)
    if _geojson_cache_ready(raw_path):
        logger.info("Agent4 using cached raw Microsoft tile: %s", raw_path)
        return raw_path.read_bytes()

    lock_path = raw_path.with_suffix(raw_path.suffix + ".lock")
    with _agent4_file_lock(lock_path):
        if _geojson_cache_ready(raw_path):
            logger.info("Agent4 using cached raw Microsoft tile: %s", raw_path)
            return raw_path.read_bytes()
        content = download_fn(url)
        tmp_path = raw_path.with_suffix(raw_path.suffix + ".tmp")
        tmp_path.write_bytes(content)
        tmp_path.replace(raw_path)
        return content


def _download_footprints_for_bbox(bbox, save_dir=None, records=None) -> str:
    import geopandas as gpd
    import pandas as pd

    save_path = Path(save_dir or _agent4_footprints_dir())
    save_path.mkdir(parents=True, exist_ok=True)
    tiles_dir = save_path / "tiles"
    tiles_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = save_path / "raw_tiles"
    raw_dir.mkdir(parents=True, exist_ok=True)

    minx, miny, maxx, maxy = bbox
    bbox_tag = (
        f"{minx:.4f}_{miny:.4f}_{maxx:.4f}_{maxy:.4f}"
        .replace("-", "m").replace(".", "p")
    )
    if records:
        coord_parts: list[str] = []
        for rec in records:
            try:
                coord_parts.append(f"{float(rec['lat']):.5f},{float(rec['lon']):.5f}")
            except (KeyError, TypeError, ValueError):
                continue
        if coord_parts:
            digest = hashlib.sha1("|".join(sorted(coord_parts)).encode("utf-8")).hexdigest()[:12]
            bbox_tag = f"{bbox_tag}_{digest}"
    merged_path = save_path / f"bbox_{bbox_tag}.geojson"
    if _geojson_cache_ready(merged_path):
        logger.info("Agent4 using cached bbox footprints: %s", merged_path)
        return str(merged_path)

    build_lock = save_path / "locks" / f"bbox_{bbox_tag}.lock"
    with _agent4_file_lock(build_lock):
        if _geojson_cache_ready(merged_path):
            logger.info("Agent4 using cached bbox footprints: %s", merged_path)
            return str(merged_path)

        _ensure_reference_scripts_importable()
        from scripts.download_footprints import (  # type: ignore
            _download_bytes,
            _normalize_quadkey,
            _save_geojson_from_zip,
        )

        us_df = _agent4_us_dataset_links()
        records_by_quadkey = (
            _records_by_quadkey(records, level=_AGENT4_TILE_LEVEL)
            if records else {}
        )
        quadkeys = set(records_by_quadkey) or _quadkeys_for_bbox(bbox, level=_AGENT4_TILE_LEVEL)
        if not quadkeys:
            quadkeys = _quadkeys_for_bbox(bbox, level=_AGENT4_TILE_LEVEL)

        logger.info("Agent4 (fallback) preparing %d Microsoft tile(s)", len(quadkeys))

        gdfs: list[Any] = []
        for quadkey in sorted(quadkeys):
            norm = _normalize_quadkey(quadkey)
            tile_records = records_by_quadkey.get(norm)
            tile_bbox = _bbox_for_records(tile_records) if tile_records else bbox
            tile_minx, tile_miny, tile_maxx, tile_maxy = tile_bbox

            # ── PERF FIX ────────────────────────────────────────────────
            # Cache key is the quadkey ONLY — not the per-job bbox. The
            # expensive decompress/convert step in _convert_tile_gz_to_geojson
            # is therefore a one-time cost per quadkey and is reused by every
            # future job/run that touches this tile, instead of being redone
            # on every single pipeline run (which is what was making the
            # Microsoft-footprint fallback look slow). Per-job spatial
            # filtering still happens below via gpd.read_file(..., bbox=...),
            # which is cheap.
            tile_path = tiles_dir / f"{norm}.geojson"
            if not _geojson_cache_ready(tile_path):
                tile_lock_path = tile_path.with_suffix(tile_path.suffix + ".lock")
                with _agent4_file_lock(tile_lock_path):
                    if not _geojson_cache_ready(tile_path):
                        url = _tile_url_for_quadkey(us_df, quadkey)
                        if not url:
                            logger.warning("Agent4 no Microsoft tile URL for quadkey=%s", quadkey)
                            continue
                        content = _read_or_download_tile_content(raw_dir, norm, url, _download_bytes)
                        lower = url.lower()
                        if lower.endswith(".csv.gz"):
                            _convert_tile_gz_to_geojson(content, tile_path)
                        elif lower.endswith(".zip"):
                            _save_geojson_from_zip(content, tile_path)
                        else:
                            tile_path.write_bytes(content)

            if not _geojson_cache_ready(tile_path):
                continue

            tile_gdf = gpd.read_file(tile_path, bbox=(tile_minx, tile_miny, tile_maxx, tile_maxy))
            if not tile_gdf.empty:
                gdfs.append(tile_gdf)

        if not gdfs:
            raise RuntimeError(
                "Agent4 could not download Microsoft footprint tiles. "
                "Check network access to minedbuildings.z5.web.core.windows.net."
            )

        combined = gpd.GeoDataFrame(pd.concat(gdfs, ignore_index=True), crs=gdfs[0].crs)
        combined = combined[combined.geometry.notna()].copy()
        combined.to_file(merged_path, driver="GeoJSON")
        logger.info("Agent4 built bbox footprint cache: %d footprints -> %s", len(combined), merged_path)
        return str(merged_path)


def _apply_agent4_download_patch(bbox) -> None:
    global _AGENT4_JOB_BBOX, _AGENT4_DOWNLOAD_MODULE, _AGENT4_DOWNLOAD_ORIGINAL
    _AGENT4_JOB_BBOX = bbox
    if bbox is None or _AGENT4_DOWNLOAD_ORIGINAL is not None:
        return
    _ensure_reference_scripts_importable()
    import scripts.download_footprints as dl_mod  # type: ignore
    _AGENT4_DOWNLOAD_ORIGINAL = dl_mod.download_by_lat_lon
    footprints_dir = str(_agent4_footprints_dir())

    def _bbox_aware_download(lat: float, lon: float, save_dir: str = footprints_dir) -> str:
        job_bbox = _AGENT4_JOB_BBOX
        if job_bbox is not None:
            return _download_footprints_for_bbox(job_bbox, save_dir or footprints_dir)
        return _AGENT4_DOWNLOAD_ORIGINAL(lat, lon, save_dir)

    dl_mod.download_by_lat_lon = _bbox_aware_download
    _AGENT4_DOWNLOAD_MODULE = dl_mod


def _restore_agent4_download_patch() -> None:
    global _AGENT4_JOB_BBOX, _AGENT4_DOWNLOAD_MODULE, _AGENT4_DOWNLOAD_ORIGINAL
    try:
        if _AGENT4_DOWNLOAD_MODULE is not None and _AGENT4_DOWNLOAD_ORIGINAL is not None:
            _AGENT4_DOWNLOAD_MODULE.download_by_lat_lon = _AGENT4_DOWNLOAD_ORIGINAL
    except Exception:
        pass
    finally:
        _AGENT4_JOB_BBOX = None
        _AGENT4_DOWNLOAD_MODULE = None
        _AGENT4_DOWNLOAD_ORIGINAL = None


def _configure_agent_footprints_dir(agent: Any) -> None:
    config = getattr(agent, "config", None)
    if isinstance(config, dict):
        config.setdefault("data", {})["footprints_dir"] = str(_agent4_footprints_dir())


def _resolve_reference_loader_module() -> Any | None:
    ref_root = str(_REFERENCE_ROOT)
    if ref_root not in sys.path:
        sys.path.insert(0, ref_root)
    for mod_name, mod in sys.modules.items():
        if mod_name not in ("src.loader", "loader"):
            continue
        mod_file = Path(getattr(mod, "__file__", "") or "")
        if ref_root in str(mod_file):
            return mod
    try:
        import importlib
        return importlib.import_module("src.loader")
    except ImportError:
        return None


def _apply_agent4_bbox_loader_patch(bbox) -> None:
    global _AGENT4_LOADER_MODULE, _AGENT4_LOADER_ORIGINAL
    if bbox is None or (_AGENT4_LOADER_MODULE is not None and _AGENT4_LOADER_ORIGINAL is not None):
        return
    try:
        import geopandas as gpd
        from shapely.geometry import box as _shapely_box

        loader_module = _resolve_reference_loader_module()
        if loader_module is None:
            return

        minx, miny, maxx, maxy = bbox
        read_bbox = (minx, miny, maxx, maxy)
        clip_geom = _shapely_box(minx, miny, maxx, maxy)

        if hasattr(loader_module, "FootprintLoader"):
            _original_loader = loader_module.FootprintLoader.load_ms_footprints

            def _clipped_loader(self, path, *args, **kwargs):
                try:
                    gdf = gpd.read_file(path, bbox=read_bbox)
                    gdf = gdf[gdf.geometry.notna()].copy()
                    gdf["source"] = "ms"
                    gdf = gdf.to_crs(loader_module.FootprintLoader.CRS_WGS84)
                    return gdf
                except Exception:
                    gdf = _original_loader(self, path, *args, **kwargs)
                    return gdf[gdf.geometry.intersects(clip_geom)].reset_index(drop=True)

            loader_module.FootprintLoader.load_ms_footprints = _clipped_loader
            _AGENT4_LOADER_MODULE = loader_module
            _AGENT4_LOADER_ORIGINAL = _original_loader
            return

        if hasattr(loader_module, "load_ms_footprints"):
            _original_loader = loader_module.load_ms_footprints

            def _clipped_loader(path, *args, **kwargs):
                try:
                    gdf = gpd.read_file(path, bbox=read_bbox)
                    return gdf
                except Exception:
                    gdf = _original_loader(path, *args, **kwargs)
                    return gdf[gdf.geometry.intersects(clip_geom)].reset_index(drop=True)

            loader_module.load_ms_footprints = _clipped_loader
            _AGENT4_LOADER_MODULE = loader_module
            _AGENT4_LOADER_ORIGINAL = _original_loader
    except Exception as patch_exc:
        logger.warning("Agent4 loader patch skipped: %s", patch_exc)


def _restore_agent4_loader_patch() -> None:
    global _AGENT4_LOADER_MODULE, _AGENT4_LOADER_ORIGINAL
    try:
        if _AGENT4_LOADER_MODULE is not None and _AGENT4_LOADER_ORIGINAL is not None:
            if hasattr(_AGENT4_LOADER_MODULE, "FootprintLoader"):
                _AGENT4_LOADER_MODULE.FootprintLoader.load_ms_footprints = _AGENT4_LOADER_ORIGINAL
            elif hasattr(_AGENT4_LOADER_MODULE, "load_ms_footprints"):
                _AGENT4_LOADER_MODULE.load_ms_footprints = _AGENT4_LOADER_ORIGINAL
    except Exception:
        pass
    finally:
        _AGENT4_LOADER_MODULE = None
        _AGENT4_LOADER_ORIGINAL = None


def _reset_reference_agent_stats(agent: Any) -> None:
    agent.total = 0
    agent.matched = 0
    agent.distance_sum = 0
    agent.class_counts = {}


def _invalidate_reference_agent_index(agent: Any) -> None:
    agent._index_ready = False
    spatial = getattr(agent, "spatial", None)
    if spatial is not None:
        spatial._gdf = None
        spatial._tree = None


def _spatial_index_ready(agent: Any) -> bool:
    spatial = getattr(agent, "spatial", None)
    if spatial is None:
        return False
    tree = getattr(spatial, "_tree", None)
    gdf = getattr(spatial, "_gdf", None)
    try:
        return tree is not None and gdf is not None and len(gdf) > 0
    except Exception:
        return False


def _reference_agent_cache_reusable(agent: Any) -> bool:
    if getattr(agent, "_index_ready", None) is False:
        return False
    spatial = getattr(agent, "spatial", None)
    if spatial is None:
        return True
    return _spatial_index_ready(agent) or getattr(agent, "_index_ready", None) is not False


def _build_agent4_spatial_index(agent: Any, records, job_bbox) -> None:
    if _spatial_index_ready(agent):
        return
    _invalidate_reference_agent_index(agent)
    footprint_path = _download_footprints_for_bbox(job_bbox, records=records)
    gdf = agent.loader.load_ms_footprints(footprint_path)
    if gdf.empty:
        raise RuntimeError("Agent4 loaded zero footprints from Microsoft tiles.")
    agent.spatial.build_sqlite(gdf)
    if not _spatial_index_ready(agent):
        raise RuntimeError("Agent4 footprint index was not built — STRtree creation failed.")
    agent._index_ready = True
    logger.info("Agent4 Microsoft spatial index built with %d footprints", len(agent.spatial._gdf))


def _get_reference_agent(records) -> Any:
    global _REFERENCE_AGENT_CACHE, _REFERENCE_AGENT_STATES, _REFERENCE_AGENT_BBOX
    requested_states = _states_for_records(records)
    bbox = _bbox_for_records(records)
    with _REFERENCE_AGENT_LOCK:
        if (
            _REFERENCE_AGENT_CACHE is not None
            and (not requested_states or requested_states.issubset(_REFERENCE_AGENT_STATES))
            and _bbox_within_cached(bbox)
            and _reference_agent_cache_reusable(_REFERENCE_AGENT_CACHE)
        ):
            _reset_reference_agent_stats(_REFERENCE_AGENT_CACHE)
            return _REFERENCE_AGENT_CACHE

        BuildingDataAgent = _import_reference_agent()
        agent = BuildingDataAgent(config_path=str(_REFERENCE_CONFIG))
        _configure_agent_footprints_dir(agent)
        _REFERENCE_AGENT_CACHE = agent
        _REFERENCE_AGENT_STATES = requested_states
        _REFERENCE_AGENT_BBOX = bbox
        return agent


# ===========================================================================
# ████████████████  SHARED HELPERS  █████████████████████████████████████████
# ===========================================================================
 
def _sv_metadata(lat: float, lon: float) -> bool:
    """Legacy helper kept for unit tests."""
    params = urllib.parse.urlencode({
        "location": f"{lat},{lon}",
        "key": os.environ.get("GOOGLE_API_KEY", "test"),
    })
    try:
        with urllib.request.urlopen(
            urllib.request.Request(f"{_SV_META_URL}?{params}"), timeout=10
        ) as resp:
            data = json.loads(resp.read().decode())
        return data.get("status") == "OK"
    except Exception:
        return False
 
 
def _coord_pair(lat: Any, lon: Any) -> tuple[float | None, float | None]:
    try:
        flat, flon = float(lat), float(lon)
    except (TypeError, ValueError):
        return None, None
    if flat == 0 or flon == 0 or not (-90 <= flat <= 90) or not (-180 <= flon <= 180):
        return None, None
    return flat, flon
 
 
def _as_percent(confidence: Any) -> int:
    try:
        value = float(confidence)
    except (TypeError, ValueError):
        return 0
    if value <= 1:
        value *= 100
    return int(round(max(0, min(value, 100))))


def _agent_coord_confidence_threshold() -> int:
    raw = os.getenv(
        "AGENT4_COORD_CONFIDENCE_THRESHOLD",
        os.getenv("AGENT2_CONFIDENCE_THRESHOLD", "90"),
    )
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 90


def _agent2_is_high_confidence(a2: dict[str, Any] | None, threshold: int) -> bool:
    a2 = a2 or {}
    status = str(a2.get("status") or "").strip().lower()
    if status in {"failed", "error", "skipped"}:
        return False
    return _as_percent(a2.get("confidence")) >= threshold


def _agent3_regrid_is_high_confidence(a3: dict[str, Any] | None, threshold: int) -> bool:
    a3 = a3 or {}
    source = str(a3.get("source") or "").strip().lower()
    status = str(a3.get("status") or "").strip().lower()
    if source != "regrid" or status in {"failed", "error", "skipped"}:
        return False
    return _as_percent(a3.get("confidence")) >= threshold


def _smarty_confidence(addr: Address | None, a1: Agent1Result | None) -> int:
    scores: list[int] = []
    if a1:
        data = a1.data if isinstance(a1.data, dict) else {}
        for key in ("smarty_confidence_score", "smarty_score"):
            if data.get(key) is not None:
                scores.append(_as_percent(data.get(key)))
        if str(a1.chosen_provider or "").strip().lower() == "smarty":
            scores.append(_as_percent(a1.confidence_score))

    meta = addr.raw_metadata if addr is not None and isinstance(addr.raw_metadata, dict) else {}
    block = meta.get("Smarty_Street") if isinstance(meta.get("Smarty_Street"), dict) else {}
    smarty = block.get("smarty") if isinstance(block.get("smarty"), dict) else {}
    for key in ("confidence_score", "score"):
        if smarty.get(key) is not None:
            scores.append(_as_percent(smarty.get(key)))

    return max(scores) if scores else 0


def _smarty_is_high_confidence(addr: Address | None, a1: Agent1Result | None, threshold: int) -> bool:
    return _smarty_confidence(addr, a1) >= threshold


def _agent4_metadata_allows_expansion(addr: Address) -> bool:
    meta = addr.raw_metadata if isinstance(addr.raw_metadata, dict) else {}
    if str(meta.get("map_layer_only") or "").strip().lower() == "true":
        return False
    if str(meta.get("geometry_type") or "").strip() in {"Polygon", "LineString"}:
        return False
    file_role = str(meta.get("file_role") or "").strip().lower()
    category = str(meta.get("category") or "").strip().lower()
    return file_role != "geospatial" or category in {"household", "households"}


def _expand_agent4_address_ids(
    session,
    job_id: str,
    address_ids: list[int],
    threshold: int,
) -> list[int]:
    """
    Keep Agent 4's handoff inclusive when a caller passes only unresolved rows.

    Besides the supplied ids, Agent 4 should still validate rows confirmed by
    high-confidence Agent 2 geocoding, Smarty, or Regrid evidence.
    """
    selected = {int(addr_id) for addr_id in address_ids if addr_id is not None}
    if not selected:
        return address_ids

    job_addresses = session.scalars(
        _sel(Address).where(Address.job_id == job_id).order_by(Address.id)
    ).all()
    if not job_addresses:
        return sorted(selected)

    all_ids = [addr.id for addr in job_addresses]
    a1_map = {
        r.address_id: r
        for r in session.scalars(
            _sel(Agent1Result).where(Agent1Result.address_id.in_(all_ids))
        ).all()
    }
    agent_rows = session.scalars(
        _sel(AgentResult).where(
            AgentResult.agent_name.in_(("agent2_geocoding", "agent3_parcel")),
            AgentResult.address_id.in_(all_ids),
        )
    ).all()
    a2_map = {
        r.address_id: r.data or {}
        for r in agent_rows
        if r.agent_name == "agent2_geocoding"
    }
    a3_map = {
        r.address_id: r.data or {}
        for r in agent_rows
        if r.agent_name == "agent3_parcel"
    }

    added = 0
    for addr in job_addresses:
        if addr.id in selected or not _agent4_metadata_allows_expansion(addr):
            continue
        if (
            _agent2_is_high_confidence(a2_map.get(addr.id), threshold)
            or _smarty_is_high_confidence(addr, a1_map.get(addr.id), threshold)
            or _agent3_regrid_is_high_confidence(a3_map.get(addr.id), threshold)
        ):
            selected.add(addr.id)
            added += 1

    if added:
        logger.info(
            "Agent4 expanded handoff by %d high-confidence row(s) "
            "(Agent2/Smarty/Regrid threshold=%d)",
            added,
            threshold,
        )
    return sorted(selected)


def _best_lat_lon(addr: Address, a1: Agent1Result | None, a2=None, a3=None):
    """
    Resolve coordinates for Agent 4.

    Prefer upstream agent outputs when confidence meets the configured threshold
    (default 90). Raw upload pins (``source_latitude``/``source_longitude``) are
    used only as a last resort so ATTOM / footprint lookup aligns with trusted
    Agent 2, Smarty, or Regrid evidence.
    """
    a2 = a2 or {}
    a3 = a3 or {}
    meta = addr.raw_metadata or {}
    final = meta.get("final_resolution") or {}
    threshold = _agent_coord_confidence_threshold()

    if _agent2_is_high_confidence(a2, threshold):
        lat, lon = _coord_pair(a2.get("latitude"), a2.get("longitude"))
        if lat is not None:
            return lat, lon

    final_conf = _as_percent(meta.get("final_confidence") or final.get("confidence"))
    if final_conf >= threshold:
        lat, lon = _coord_pair(final.get("latitude"), final.get("longitude"))
        if lat is not None:
            return lat, lon
        lat, lon = _coord_pair(meta.get("final_latitude"), meta.get("final_longitude"))
        if lat is not None:
            return lat, lon

    if _smarty_is_high_confidence(addr, a1, threshold):
        lat, lon = _coord_pair(a1.smarty_lat if a1 else None, a1.smarty_lon if a1 else None)
        if lat is not None:
            return lat, lon

    if a1 and _as_percent(a1.confidence_score) >= threshold:
        lat, lon = _coord_pair(a1.smarty_lat, a1.smarty_lon)
        if lat is not None:
            return lat, lon

    if _agent3_regrid_is_high_confidence(a3, threshold):
        lat, lon = _coord_pair(a3.get("latitude"), a3.get("longitude"))
        if lat is not None:
            return lat, lon

    for lat, lon in (
        (addr.validated_latitude, addr.validated_longitude),
        (addr.latitude, addr.longitude),
        (meta.get("final_latitude"), meta.get("final_longitude")),
        (final.get("latitude"), final.get("longitude")),
        (addr.source_latitude, addr.source_longitude),
    ):
        best_lat, best_lon = _coord_pair(lat, lon)
        if best_lat is not None and best_lon is not None:
            return best_lat, best_lon
    return None, None
 
 
def _best_address(addr: Address, a1: Agent1Result | None, a2=None) -> str:
    a2 = a2 or {}
    meta = addr.raw_metadata or {}
    final = meta.get("final_resolution") or {}
    return (
        final.get("address")
        or meta.get("final_address")
        or a2.get("formatted_address")
        or (a1.chosen_standardized_address if a1 else None)
        or addr.validated_raw_address
        or addr.raw_address
        or ""
    )
 
 
def _normalize_structure(structure_hint: str | None) -> str:
    hint = (structure_hint or "").upper()
    if hint == "SFU":
        return "SFH"
    if hint.startswith("MDU"):
        return "MDU"
    if hint == "ANCHOR":
        return "Commercial"
    return structure_hint or "Unknown"
 
 
# ---------------------------------------------------------------------------
# Coordinate-mismatch guard (Microsoft fallback path only)
# ---------------------------------------------------------------------------
 
_RESIDENTIAL_FOOTPRINT_MAX_M2 = 500.0
_COORD_MISMATCH_STATUSES = frozenset({"MISMATCH", "MISMATCH_WARN", "NO_COORDS"})
_STRICT_FOOTPRINT_MATCH_M = 15.0
_COORD_UNRELIABLE_DISTANCE_M = 30.0
_COORD_REJECT_ALL_DISTANCE_M = 100.0


def _coord_validation_context(addr: Address, a2=None) -> dict[str, Any]:
    a2 = a2 or {}
    meta = addr.raw_metadata or {}
    av = meta.get("address_validation") or {}
    status = (
        str(getattr(addr, "coord_address_match_status", None) or "")
        or str(av.get("match_status") or "")
        or str(a2.get("match_status") or "")
    ).strip().upper()
    distance_m = getattr(addr, "coord_address_distance_m", None)
    if not isinstance(distance_m, (int, float)):
        raw = av.get("distance_m") or a2.get("coord_distance_m")
        distance_m = raw if isinstance(raw, (int, float)) else None
    try:
        distance_m = float(distance_m) if distance_m is not None else None
    except (TypeError, ValueError):
        distance_m = None
    return {"match_status": status, "distance_m": distance_m}


def _coords_unreliable(context: dict[str, Any]) -> bool:
    status = str(context.get("match_status") or "").upper()
    distance_m = context.get("distance_m")
    if status in _COORD_MISMATCH_STATUSES:
        return True
    return distance_m is not None and distance_m > _COORD_UNRELIABLE_DISTANCE_M


def _max_footprint_match_m(context: dict[str, Any]) -> float | None:
    if not _coords_unreliable(context):
        return None
    status = str(context.get("match_status") or "").upper()
    distance_m = context.get("distance_m")
    if status == "MISMATCH" or (distance_m is not None and distance_m >= _COORD_REJECT_ALL_DISTANCE_M):
        return 0.0
    if status == "MISMATCH_WARN" and (distance_m is None or distance_m >= _COORD_UNRELIABLE_DISTANCE_M):
        return 0.0
    if status in _COORD_MISMATCH_STATUSES:
        return min(_STRICT_FOOTPRINT_MATCH_M, 10.0)
    return _STRICT_FOOTPRINT_MATCH_M


def _footprint_match_distance_m(enriched: dict[str, Any]) -> float | None:
    try:
        return float(enriched.get("footprint_match_distance_m"))
    except (TypeError, ValueError):
        return None


def _reject_footprint_match(enriched: dict[str, Any], reason_signal: str) -> dict[str, Any]:
    out = dict(enriched)
    out.update({"building_matched": False, "structure_hint": "UNRESOLVED",
                "hint_confidence": 0, "class_source": "COORD_MISMATCH_GUARD"})
    signals = list(out.get("hint_signals") or [])
    if reason_signal not in signals:
        signals.append(reason_signal)
    out["hint_signals"] = signals
    return out


def _apply_coord_mismatch_guard(enriched, addr, a2=None):
    if not enriched.get("building_matched"):
        return enriched
    context = _coord_validation_context(addr, a2)
    max_dist = _max_footprint_match_m(context)
    if max_dist is None:
        return enriched
    fp_dist = _footprint_match_distance_m(enriched)
    if fp_dist is None or fp_dist > max_dist:
        return _reject_footprint_match(enriched, "coord_mismatch_footprint_rejected")
    return enriched


def _matched_footprint_fallback(enriched, addr=None, a2=None):
    out = dict(enriched)
    if not out.get("building_matched"):
        return out
    if addr is not None and _coords_unreliable(_coord_validation_context(addr, a2)):
        fp_dist = _footprint_match_distance_m(out)
        if fp_dist is None or fp_dist > _STRICT_FOOTPRINT_MATCH_M:
            return out
    hint = str(out.get("structure_hint") or "").upper()
    if hint != "UNRESOLVED":
        return out
    try:
        area = float(out.get("footprint_area_m2"))
    except (TypeError, ValueError):
        return out
    if area <= 0 or area > _RESIDENTIAL_FOOTPRINT_MAX_M2:
        return out
    out["structure_hint"] = "SFU"
    out["hint_confidence"] = max(_as_percent(out.get("hint_confidence")), 80)
    out["class_source"] = "FOOTPRINT_FALLBACK"
    signals = list(out.get("hint_signals") or [])
    if "matched_residential_footprint_fallback" not in signals:
        signals.append("matched_residential_footprint_fallback")
    out["hint_signals"] = signals
    return out


def _address_validation_score(addr, a2=None) -> int | None:
    a2 = a2 or {}
    for key in ("validation_score", "confidence", "match_score"):
        raw = a2.get(key)
        if isinstance(raw, (int, float)):
            return int(round(float(raw)))
    meta = addr.raw_metadata or {}
    av = meta.get("address_validation") or {}
    for key in ("validation_score", "confidence_score", "score"):
        raw = av.get(key)
        if isinstance(raw, (int, float)):
            return int(round(float(raw)))
    return None


def _calibrate_agent4_confidence(enriched, addr, a2=None):
    out = dict(enriched)
    if not out.get("building_matched"):
        return out
    hint = str(out.get("structure_hint") or "").upper()
    if hint not in ("SFU", "SFH"):
        return out
    current = _as_percent(out.get("hint_confidence"))
    floor = current
    context = _coord_validation_context(addr, a2)
    status = str(context.get("match_status") or "").upper()
    coord_dist = context.get("distance_m")
    fp_dist = _footprint_match_distance_m(out)
    if status == "MATCH":
        floor = max(floor, 92 if (coord_dist or 999) <= 5 else 88 if (coord_dist or 999) <= 15 else 85)
    val_score = _address_validation_score(addr, a2)
    if val_score and val_score >= 99:
        floor = max(floor, 90)
    elif val_score and val_score >= 90:
        floor = max(floor, 85)
    if fp_dist is not None:
        if fp_dist <= 10:
            floor = max(floor, 90)
        elif fp_dist <= 20:
            floor = max(floor, 85)
        elif fp_dist <= 35:
            floor = max(floor, 80)
        elif fp_dist <= 50:
            floor = max(floor, 75)
    if floor <= current:
        return out
    out["hint_confidence"] = floor
    signals = list(out.get("hint_signals") or [])
    if "confidence_calibrated" not in signals:
        signals.append("confidence_calibrated")
    out["hint_signals"] = signals
    return out


def _record_from_db_metadata(addr, a1=None, a2=None, a3=None):
    lat, lon = _best_lat_lon(addr, a1, a2, a3)
    if lat is None or lon is None or lat == 0 or lon == 0:
        return None
    fields = resolve_agent1_input(addr)
    address = _best_address(addr, a1, a2) or fields.raw_address
    return {
        "address_id": addr.id,
        "address": address,
        "lat": float(lat),
        "lon": float(lon),
        "_input_source": "database",
    }


def _record_for_reference(addr, a1, a2=None, a3=None):
    return _record_from_db_metadata(addr, a1, a2, a3)
 
 
def _to_agent4_payload_from_ms(enriched, addr, a1, a2=None):
    """Convert a Microsoft-footprint enriched row to the standard Agent 4 payload."""
    enriched = _apply_coord_mismatch_guard(enriched, addr, a2)
    enriched = _matched_footprint_fallback(enriched, addr, a2)
    enriched = _calibrate_agent4_confidence(enriched, addr, a2)
    structure_hint = enriched.get("structure_hint") or "UNRESOLVED"
    structure_type = _normalize_structure(structure_hint)
    payload = {
        "status": "classified" if enriched.get("building_matched") else "unmatched",
        "structure_type": structure_type,
        "is_mdu": structure_type == "MDU",
        "confidence": _as_percent(enriched.get("hint_confidence")),
        "imagery_source": "building_footprint" if enriched.get("building_matched") else "none",
        "latitude": enriched.get("lat"),
        "longitude": enriched.get("lon"),
        "address": enriched.get("address") or _best_address(addr, a1, a2),
        "source_agent": "microsoft_footprint_fallback",
        "building_matched": bool(enriched.get("building_matched")),
        "structure_hint": structure_hint,
        "hint_confidence": enriched.get("hint_confidence"),
        "hint_signals": enriched.get("hint_signals", []),
        "class_source": enriched.get("class_source", ""),
        "matched_radius_m": enriched.get("matched_radius_m"),
        "footprint_area_m2": enriched.get("footprint_area_m2"),
        "elongation_ratio": enriched.get("elongation_ratio"),
        "perimeter_complexity": enriched.get("perimeter_complexity"),
        "footprint_source": enriched.get("footprint_source"),
        "footprint_match_distance_m": enriched.get("footprint_match_distance_m"),
        "floor_count_est": enriched.get("floor_count_est") or enriched.get("floor_count"),
        "unit_count": enriched.get("unit_count"),
    }
    payload["justification"] = build_agent4_justification(payload)
    return payload


def _to_agent4_payload(enriched, addr, a1, a2=None):
    """Legacy compatibility wrapper for the Microsoft-footprint payload path."""
    return _to_agent4_payload_from_ms(enriched, addr, a1, a2)
 
 
# ---------------------------------------------------------------------------
# DB persistence helpers
# ---------------------------------------------------------------------------

def _persist_buildings_address(addr, enriched, payload, *, input_source="database"):
    buildings_address = {
        "source": input_source,
        "input": db_input_bundle(addr),
        "classification": {
            "structure_type": payload.get("structure_type"),
            "is_mdu": payload.get("is_mdu"),
            "confidence": payload.get("confidence"),
            "imagery_source": payload.get("imagery_source"),
        },
        "structure_hint": payload.get("structure_hint"),
        "building_matched": payload.get("building_matched"),
        "footprint_area_m2": enriched.get("footprint_area_m2"),
        "footprint_match_distance_m": enriched.get("footprint_match_distance_m"),
        "matched_radius_m": enriched.get("matched_radius_m"),
        # ATTOM extras (populated on ATTOM path, None on Microsoft path)
        "attom_land_use_raw": payload.get("attom_land_use_raw"),
        "attom_lot_size_sqft": payload.get("attom_lot_size_sqft"),
        "attom_building_sqft": payload.get("attom_building_sqft"),
        "updated_at": datetime.utcnow().isoformat() + "Z",
    }
    persist_buildings_address_in_raw_metadata(addr, buildings_address)


def _ensure_table(session) -> None:
    table = session.execute(
        _sel(AgentTable).where(AgentTable.agent_name == _AGENT_NAME)
    ).scalar_one_or_none()
    color_rules = [
        {"field": "structure_type", "value": "SFH", "color": "#22c55e", "label": "SFH"},
        {"field": "structure_type", "value": "MDU", "color": "#3b82f6", "label": "MDU"},
        {"field": "structure_type", "value": "Commercial", "color": "#f59e0b", "label": "Commercial"},
        {"field": "building_matched", "value": True, "color": "#14b8a6", "label": "Footprint matched"},
        {"field": "source_agent", "value": "attom_data_api", "color": "#8b5cf6", "label": "ATTOM primary"},
        {"field": "source_agent", "value": "microsoft_footprint_fallback", "color": "#6b7280", "label": "MS fallback"},
    ]
    if table:
        table.display_name = _DISPLAY_NAME
        table.description = (
            "Building classification — ATTOM Data API (primary) "
            "with Microsoft footprint fallback"
        )
        table.color_rules = color_rules
    else:
        session.add(AgentTable(
            agent_name=_AGENT_NAME,
            display_name=_DISPLAY_NAME,
            owner="system",
            description=(
                "Building classification — ATTOM Data API (primary) "
                "with Microsoft footprint fallback"
            ),
            color_rules=color_rules,
        ))
    session.commit()
 
 
def _upsert(session, job_id: str, address_id: int, data: dict) -> None:
    from sqlalchemy.dialects.postgresql import insert as _pg_insert
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
 
 
# ===========================================================================
# ████████████████████████  MAIN ENTRY POINT  ████████████████████████████████
# ===========================================================================

def run_agent4_for_job(
    job_id: str,
    address_ids: list[int] | None = None,
    progress_callback=None,
    agent_options: dict[str, bool] | None = None,
) -> dict:
    """
    Classify all addresses for *job_id*.

    For each address the pipeline tries:
      1. ATTOM Data API  (primary — accurate SFU/MDU/Commercial)
      2. Microsoft building footprints  (fallback when ATTOM fails or key is missing)

    Results are written to ``agent_results`` and ``addresses.raw_metadata``.
    """
    from data_ingestion.utils.pipeline_options import DEFAULT_AGENT4_OPTIONS

    opts = {**DEFAULT_AGENT4_OPTIONS, **(agent_options or {})}
    log_path = configure_agent_logger(logger, _AGENT_NAME)
    logger.info(
        "Agent 4 start | job_id=%s | address_ids=%s | options=%s | log=%s",
        job_id,
        len(address_ids) if address_ids else "all",
        opts,
        log_path,
    )
    log_payload(logger, "agent4_options", opts)
    if not opts.get("building_footprint", True):
        return {"total": 0, "processed": 0, "skipped": True, "reason": "building_footprint disabled"}

    global _REFERENCE_AGENT_CACHE

    building_provider = str(opts.get("building_provider") or "microsoft").strip().lower()
    attom_available = building_provider == "attom" and _attom_is_available()
    if attom_available:
        logger.info(
            "Agent4 ATTOM primary enabled (base=%s, radius=%.3f mi)",
            _attom_base_url(),
            _ATTOM_LATLON_RADIUS_MI,
        )
    elif building_provider == "attom":
        logger.warning(
            "ATTOM_API_KEY not set or is placeholder — running Microsoft footprint fallback only. "
            "Set ATTOM_API_KEY in .env to enable primary classification."
        )
    else:
        logger.info("Agent4 Microsoft building provider selected; ATTOM lookup skipped")

    session = get_session_factory()()
    try:
        _ensure_table(session)
 
        threshold = _agent_coord_confidence_threshold()
        if address_ids:
            address_ids = _expand_agent4_address_ids(session, job_id, address_ids, threshold)

        stmt = _sel(Address).where(Address.job_id == job_id).order_by(Address.id)
        if address_ids:
            stmt = stmt.where(Address.id.in_(address_ids))
        addresses = session.scalars(stmt).all()
 
        addr_ids = [a.id for a in addresses]
        a1_map: dict[int, Agent1Result] = {}
        a2_map: dict[int, dict[str, Any]] = {}
        a3_map: dict[int, dict[str, Any]] = {}
        if addr_ids:
            a1_map = {
                r.address_id: r
                for r in session.scalars(
                    _sel(Agent1Result).where(Agent1Result.address_id.in_(addr_ids))
                ).all()
            }
            a2_map = {
                r.address_id: r.data or {}
                for r in session.scalars(
                    _sel(AgentResult).where(
                        AgentResult.agent_name == "agent2_geocoding",
                        AgentResult.address_id.in_(addr_ids),
                    )
                ).all()
            }
            a3_map = {
                r.address_id: r.data or {}
                for r in session.scalars(
                    _sel(AgentResult).where(
                        AgentResult.agent_name == "agent3_parcel",
                        AgentResult.address_id.in_(addr_ids),
                    )
                ).all()
            }
 
        address_by_id = {a.id: a for a in addresses}

        # ── Buckets ───────────────────────────────────────────────────────
        # attom_done   : addresses successfully classified by ATTOM
        # ms_needed    : addresses that need the Microsoft fallback
        attom_done: dict[int, dict[str, Any]] = {}   # addr_id -> payload
        ms_needed_recs: list[dict[str, Any]] = []

        summary = {
            "total": len(addresses),
            "classified": 0,
            "matched": 0,
            "unmatched": 0,
            "no_coordinates": 0,
            "failed": 0,
            "attom_hits": 0,
            "ms_fallback_hits": 0,
            "ms_fallback_attempted": 0,
        }

        total = len(addresses)

        # ── Phase 1: ATTOM primary pass ───────────────────────────────────
        for idx, addr in enumerate(addresses):
            a1 = a1_map.get(addr.id)
            a2 = a2_map.get(addr.id, {})
            a3 = a3_map.get(addr.id, {})

            lat, lon = _best_lat_lon(addr, a1, a2, a3)
            if lat is None or lon is None:
                # No coordinates at all — write immediately
                no_coord_payload = {
                    "status": "no_coordinates",
                    "structure_type": "Unknown",
                    "is_mdu": None,
                    "confidence": 0,
                    "imagery_source": "none",
                    "building_matched": False,
                    "structure_hint": "UNRESOLVED",
                    "hint_confidence": 0.0,
                    "address": _best_address(addr, a1, a2),
                    "source_agent": "agent4_building",
                }
                no_coord_payload["justification"] = build_agent4_justification(no_coord_payload)
                _upsert(session, job_id, addr.id, no_coord_payload)
                _persist_buildings_address(
                    addr,
                    {"building_matched": False, "structure_hint": "UNRESOLVED"},
                    no_coord_payload,
                )
                summary["no_coordinates"] += 1
                if progress_callback:
                    progress_callback(idx + 1, total)
                continue

            address_str = _best_address(addr, a1, a2)

            if attom_available:
                try:
                    attom_result = _attom_lookup_by_address(
                        address_str, lat, lon, addr=addr, a1=a1, a2=a2
                    )
                    if attom_result is not None and attom_result.get("building_matched"):
                        payload = _attom_result_to_payload(
                            attom_result, addr, a1, a2, lat=lat, lon=lon
                        )
                        _upsert(session, job_id, addr.id, payload)
                        _persist_buildings_address(addr, attom_result, payload)
                        attom_done[addr.id] = payload
                        summary["classified"] += 1
                        summary["matched"] += 1
                        summary["attom_hits"] += 1
                        if progress_callback:
                            progress_callback(idx + 1, total)
                        continue
                    elif attom_result is not None:
                        # ATTOM responded but could not match (e.g. Unknown land use)
                        logger.debug(
                            "ATTOM unmatched for address_id=%s land_use=%r — falling back to MS",
                            addr.id,
                            attom_result.get("land_use_raw"),
                        )
                except Exception as attom_exc:
                    logger.warning(
                        "ATTOM lookup failed for address_id=%s: %s — falling back to MS",
                        addr.id,
                        attom_exc,
                    )

            # Queue for Microsoft fallback
            rec = _record_from_db_metadata(addr, a1, a2, a3)
            if rec:
                ms_needed_recs.append(rec)

        session.commit()

        # ── Phase 2: Microsoft footprint fallback (batch) ─────────────────
        if ms_needed_recs:
            summary["ms_fallback_attempted"] = len(ms_needed_recs)
            logger.info(
                "Agent4 Microsoft fallback: %d address(es) (ATTOM unavailable or unmatched)",
                len(ms_needed_recs),
            )

            job_bbox = _bbox_for_records(ms_needed_recs)
            if job_bbox is None:
                # Degenerate: no valid coords in fallback set
                for rec in ms_needed_recs:
                    addr_id = int(rec["address_id"])
                    error_payload = {
                        "status": "error",
                        "error": "No valid bbox for Microsoft fallback",
                        "structure_type": "Unknown",
                        "is_mdu": None,
                        "confidence": 0,
                        "imagery_source": "none",
                        "building_matched": False,
                        "source_agent": "microsoft_footprint_fallback",
                    }
                    _upsert(session, job_id, addr_id, error_payload)
                    summary["failed"] += 1
            else:
                agent = _get_reference_agent(ms_needed_recs)
                _apply_agent4_bbox_loader_patch(job_bbox)
                try:
                    try:
                        _build_agent4_spatial_index(agent, ms_needed_recs, job_bbox)
                        _workers = int(os.getenv("AGENT4_MAX_WORKERS", "1"))
                        enriched_rows = agent.enrich_batch(ms_needed_recs, workers=_workers)
                    except RuntimeError as ms_exc:
                        msg = str(ms_exc)
                        no_footprints = (
                            "could not download Microsoft footprint tiles" in msg
                            or "loaded zero footprints" in msg
                        )
                        if not no_footprints:
                            raise
                        logger.warning(
                            "Agent4 Microsoft fallback found no nearby footprints for bbox=%s: %s",
                            job_bbox,
                            msg,
                        )
                        enriched_rows = [
                            {
                                **rec,
                                "building_matched": False,
                                "structure_hint": "UNRESOLVED",
                                "hint_confidence": 0.0,
                                "class_source": "NONE",
                                "matched_radius_m": None,
                            }
                            for rec in ms_needed_recs
                        ]
                finally:
                    _restore_agent4_loader_patch()
                    if not _spatial_index_ready(agent):
                        _invalidate_reference_agent_index(agent)
                        if _REFERENCE_AGENT_CACHE is agent:
                            _REFERENCE_AGENT_CACHE = None

                enriched_by_id = {int(r["address_id"]): r for r in enriched_rows}

                for ms_idx, rec in enumerate(ms_needed_recs):
                    addr_id = int(rec["address_id"])
                    addr = address_by_id[addr_id]
                    try:
                        enriched = enriched_by_id.get(
                            addr_id, {**rec, "building_matched": False}
                        )
                        payload = _to_agent4_payload_from_ms(
                            enriched,
                            addr,
                            a1_map.get(addr_id),
                            a2_map.get(addr_id),
                        )
                        _upsert(session, job_id, addr_id, payload)
                        _persist_buildings_address(addr, enriched, payload, input_source="database")
                        summary["classified"] += 1
                        if payload["building_matched"]:
                            summary["matched"] += 1
                            summary["ms_fallback_hits"] += 1
                        else:
                            summary["unmatched"] += 1
                    except Exception as exc:
                        logger.warning("Agent4 MS result error on address_id=%s: %s", addr_id, exc)
                        summary["failed"] += 1
                        _upsert(session, job_id, addr_id, {
                            "status": "error",
                            "error": str(exc)[:500],
                            "structure_type": "Unknown",
                            "is_mdu": None,
                            "confidence": 0,
                            "imagery_source": "none",
                            "building_matched": False,
                            "source_agent": "microsoft_footprint_fallback",
                        })

                    overall_done = len(attom_done) + summary["no_coordinates"] + ms_idx + 1
                    if progress_callback:
                        progress_callback(overall_done, total)
 
        session.commit()
        logger.info("Agent4 complete: %s", summary)
        return summary

    except Exception as exc:
        session.rollback()
        logger.error("Agent4 failed for job %s: %s", job_id, exc)
        raise
    finally:
        session.close()

"""Persist Agent 5 Street View results inside addresses.raw_metadata."""

from __future__ import annotations

import math
import os
from typing import Any

from sqlalchemy.orm.attributes import flag_modified

from data_ingestion.database.models import Address, Agent1Result
from data_ingestion.utils.agent5_input import resolve_agent5_coordinates

GOOGLE_STREET_VIEW_METADATA_KEY = "google street view addresses"
MIN_STREETVIEW_METADATA_CONFIDENCE = 90
_COORD_MATCH_THRESHOLD_M = float(os.environ.get("FTTH_COORD_MATCH_THRESHOLD_M", "100"))


def _optional_str(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _city_state(city: str | None, state: str | None) -> str:
    return ", ".join(filter(None, [_optional_str(city), _optional_str(state)]))


def _format_address_line(
    *,
    street_number_name: str = "",
    city_state: str = "",
    zip_postal_code: str = "",
    country_code: str = "",
) -> str:
    parts: list[str] = []
    if street_number_name:
        parts.append(street_number_name)
    if city_state:
        parts.append(city_state)
    if zip_postal_code:
        tail = zip_postal_code
        if country_code:
            tail = f"{tail}, {country_code}" if tail else country_code
        parts.append(tail)
    elif country_code:
        parts.append(country_code)
    return ", ".join(filter(None, parts))


def _address_block(
    *,
    street_number_name: str = "",
    zip_postal_code: str = "",
    latitude: float | None = None,
    longitude: float | None = None,
    city_state: str = "",
    country_code: str = "",
) -> dict[str, Any]:
    """Build old / google street view address bundle in the required shape."""
    lat = float(latitude) if latitude is not None else None
    lon = float(longitude) if longitude is not None else None
    address_line = _format_address_line(
        street_number_name=street_number_name,
        city_state=city_state,
        zip_postal_code=zip_postal_code,
        country_code=country_code,
    )
    return {
        "ADDRESS": address_line,
        "LATITUDE": "" if lat is None else str(lat),
        "LONGITUDE": "" if lon is None else str(lon),
        "street_number_name": (street_number_name or "").strip(),
        "zip_postal_code": (zip_postal_code or "").strip(),
        "latitude": lat,
        "longitude": lon,
        "city_state": (city_state or "").strip(),
        "country_code": (country_code or "").strip().upper()[:8],
    }


def _country_code(meta: dict[str, Any]) -> str:
    for key in ("country_code", "country", "Country Code"):
        value = meta.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().upper()[:8]
    old = meta.get("old")
    if isinstance(old, dict) and old.get("country_code"):
        return str(old["country_code"]).strip().upper()[:8]
    return ""


def _old_block_from_address(addr: Address, meta: dict[str, Any]) -> dict[str, Any]:
    """Preserve existing old block or derive it from the uploaded row."""
    existing = meta.get("old")
    if isinstance(existing, dict) and existing.get("street_number_name"):
        block = dict(existing)
    else:
        src_addr = _optional_str(addr.source_raw_address) or _optional_str(addr.raw_address)
        block = _address_block(
            street_number_name=src_addr,
            zip_postal_code=_optional_str(addr.zip_code),
            latitude=addr.source_latitude if addr.source_latitude is not None else addr.latitude,
            longitude=addr.source_longitude if addr.source_longitude is not None else addr.longitude,
            city_state=_city_state(addr.city, addr.state),
            country_code=_country_code(meta),
        )
    if not block.get("ADDRESS"):
        block["ADDRESS"] = meta.get("ADDRESS") or _format_address_line(
            street_number_name=str(block.get("street_number_name") or ""),
            city_state=str(block.get("city_state") or ""),
            zip_postal_code=str(block.get("zip_postal_code") or ""),
            country_code=str(block.get("country_code") or ""),
        )
    lat = block.get("latitude")
    lon = block.get("longitude")
    if lat is not None and not block.get("LATITUDE"):
        block["LATITUDE"] = str(lat)
    if lon is not None and not block.get("LONGITUDE"):
        block["LONGITUDE"] = str(lon)
    return block


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_m = 6_371_000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return radius_m * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _parse_coord(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    if value == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed == 0:
        return None
    return parsed


def _coords_from_block(block: dict[str, Any]) -> tuple[float | None, float | None]:
    lat = _parse_coord(block.get("latitude"))
    lon = _parse_coord(block.get("longitude"))
    if lat is None:
        lat = _parse_coord(block.get("LATITUDE"))
    if lon is None:
        lon = _parse_coord(block.get("LONGITUDE"))
    return lat, lon


def _coord_validation_context(addr: Address, meta: dict[str, Any]) -> dict[str, Any]:
    av = meta.get("address_validation")
    av = av if isinstance(av, dict) else {}
    status = (
        _optional_str(getattr(addr, "coord_address_match_status", None))
        or _optional_str(av.get("match_status"))
    ).upper()
    distance_m = getattr(addr, "coord_address_distance_m", None)
    if not isinstance(distance_m, (int, float)):
        distance_m = av.get("distance_m")
    if isinstance(distance_m, (int, float)):
        distance_m = float(distance_m)
    else:
        distance_m = None
    notes = (
        _optional_str(getattr(addr, "coord_address_validation_notes", None))
        or _optional_str(av.get("notes"))
    )
    return {"match_status": status, "distance_m": distance_m, "notes": notes}


def _coord_mismatch_info(
    addr: Address,
    meta: dict[str, Any],
    old_block: dict[str, Any],
    sv_lat: float | None,
    sv_lon: float | None,
) -> tuple[bool, str]:
    """Detect coordinate mismatch from Agent 0 validation and upload vs analyzed coords."""
    validation = _coord_validation_context(addr, meta)
    status = validation["match_status"]
    agent0_distance_m = validation["distance_m"]
    remarks: list[str] = []
    is_mismatch = False

    old_lat, old_lon = _coords_from_block(old_block)
    block_distance_m: float | None = None
    if (
        old_lat is not None
        and old_lon is not None
        and sv_lat is not None
        and sv_lon is not None
    ):
        block_distance_m = _haversine_m(old_lat, old_lon, sv_lat, sv_lon)

    if status in {"MISMATCH", "MISMATCH_WARN"}:
        is_mismatch = True
        distance_m = agent0_distance_m if agent0_distance_m is not None else block_distance_m
        if distance_m is not None:
            remarks.append(
                f"uploaded coordinates do not match validated address ({distance_m:.1f}m apart)"
            )
        else:
            remarks.append("uploaded coordinates do not match validated address")
    elif status == "NO_COORDS":
        is_mismatch = True
        remarks.append("missing or invalid coordinates")
    elif block_distance_m is not None and block_distance_m > _COORD_MATCH_THRESHOLD_M:
        is_mismatch = True
        remarks.append(
            "coordinates corrected from upload to validated location "
            f"({block_distance_m:.1f}m apart)"
        )

    return is_mismatch, "; ".join(remarks)


def _streetview_address_line(
    addr: Address,
    record: dict[str, Any],
    meta: dict[str, Any],
) -> str:
    final = meta.get("final_resolution")
    if isinstance(final, dict):
        final_addr = _optional_str(final.get("address"))
        if final_addr:
            return final_addr

    for candidate in (
        _optional_str(addr.validated_raw_address),
        _optional_str(record.get("address")),
        _optional_str(record.get("street_address")),
        _optional_str(addr.raw_address),
    ):
        if candidate:
            return candidate

    new_block = meta.get("new")
    if isinstance(new_block, dict):
        street = _optional_str(new_block.get("street_number_name"))
        if street:
            return street
    return ""


def _streetview_coords(
    addr: Address,
    record: dict[str, Any],
    result: dict[str, Any],
    meta: dict[str, Any],
    a1: Agent1Result | None,
) -> tuple[float | None, float | None, str]:
    lat, lon, source = resolve_agent5_coordinates(addr, a1)
    if lat is not None and lon is not None:
        return lat, lon, source

    fallback_lat = result.get("latitude", record.get("latitude"))
    fallback_lon = result.get("longitude", record.get("longitude"))
    if fallback_lat is not None and fallback_lon is not None:
        try:
            return float(fallback_lat), float(fallback_lon), "analysis_result"
        except (TypeError, ValueError):
            pass
    return None, None, "none"


def _streetview_remarks(result: dict[str, Any], *, coord_mismatch_remark: str = "") -> str:
    parts: list[str] = []
    if coord_mismatch_remark:
        parts.append(coord_mismatch_remark)

    reason = (result.get("reason") or "").strip()
    if reason:
        parts.append(reason)

    image_quality = (result.get("image_quality") or "").strip()
    if image_quality in {"obstructed", "partial", "no_structure"}:
        parts.append("street view imagery quality is limited")

    structure_detail = (result.get("structure_type_detail") or result.get("structure_type") or "").strip()
    if structure_detail and structure_detail.lower() != "unclear":
        parts.append(f"structure classified as {structure_detail.replace('_', ' ')}")

    if result.get("ocr_match_found") or result.get("high_conf_house_number"):
        parts.append("house number verified in street view imagery")
    elif (result.get("house_number") or "").strip():
        parts.append("house number not fully matched in street view imagery")

    imagery_source = (result.get("imagery_source") or "").strip()
    if imagery_source == "satellite":
        parts.append("satellite imagery used as street view fallback")

    if not parts:
        return "street view analysis completed"
    return "; ".join(parts)


def _streetview_status(
    result: dict[str, Any],
    confidence: int,
    *,
    coord_mismatch: bool = False,
) -> str:
    if result.get("status") not in {None, "", "analyzed"}:
        return str(result.get("status") or "UNKNOWN").upper()

    if coord_mismatch:
        return "MISMATCH"

    expected_house = (result.get("house_number") or "").strip()
    if expected_house:
        if result.get("ocr_match_found") or result.get("high_conf_house_number"):
            return "MATCH"
        return "MISMATCH"

    if confidence >= MIN_STREETVIEW_METADATA_CONFIDENCE:
        return "MATCH"
    return "MISMATCH"


def _streetview_block(
    addr: Address,
    record: dict[str, Any],
    result: dict[str, Any],
    meta: dict[str, Any],
    confidence: int,
    old_block: dict[str, Any],
    a1: Agent1Result | None = None,
) -> dict[str, Any]:
    sv_lat, sv_lon, coord_source = _streetview_coords(addr, record, result, meta, a1)
    coord_mismatch, coord_remark = _coord_mismatch_info(addr, meta, old_block, sv_lat, sv_lon)
    street = _streetview_address_line(addr, record, meta)
    block = _address_block(
        street_number_name=street,
        zip_postal_code=(
            _optional_str(record.get("zip_code"))
            or _optional_str(record.get("postal_code"))
            or _optional_str(addr.zip_code)
        ),
        latitude=sv_lat,
        longitude=sv_lon,
        city_state=_city_state(record.get("city") or addr.city, record.get("state") or addr.state),
        country_code=_country_code(meta),
    )
    block["confidence"] = str(confidence)
    block["remarks"] = _streetview_remarks({**record, **result}, coord_mismatch_remark=coord_remark)
    block["status"] = _streetview_status(
        {**record, **result},
        confidence,
        coord_mismatch=coord_mismatch,
    )
    if coord_source not in {"upload", "none", "analysis_result"} and not coord_mismatch:
        corrected = block["remarks"]
        block["remarks"] = f"street view analysis used validated coordinates; {corrected}"
    return block


def sync_agent5_streetview_in_raw_metadata(
    addr: Address,
    record: dict[str, Any],
    result: dict[str, Any],
    *,
    min_confidence: int = MIN_STREETVIEW_METADATA_CONFIDENCE,
    a1: Agent1Result | None = None,
) -> bool:
    """
    Merge Agent 5 output into addresses.raw_metadata when confidence >= min_confidence.

    Writes:
      - ``old`` (preserved when already present, otherwise derived from upload)
      - ``google street view addresses`` (Agent 5 bundle + confidence / remarks / status)

    Returns True when raw_metadata was updated.
    """
    try:
        confidence = int(round(float(result.get("confidence") or 0)))
    except (TypeError, ValueError):
        return False

    if confidence < min_confidence:
        return False

    meta = dict(addr.raw_metadata or {})
    old_block = _old_block_from_address(addr, meta)
    meta["old"] = old_block
    meta[GOOGLE_STREET_VIEW_METADATA_KEY] = _streetview_block(
        addr, record, result, meta, confidence, old_block, a1
    )
    addr.raw_metadata = meta
    flag_modified(addr, "raw_metadata")
    return True

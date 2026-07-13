"""Build Street View pipeline input records from Address + Agent 1 results."""

from __future__ import annotations

from typing import Any

from data_ingestion.database.models import Address, Agent1Result
from data_ingestion.utils.address_match import extract_house_number as _leading_house_number
from data_ingestion.utils.res_com_addressing import resolve_best_address


def _coords_valid(lat: Any, lon: Any) -> bool:
    if isinstance(lat, bool) or isinstance(lon, bool):
        return False
    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        return False
    flat, flon = float(lat), float(lon)
    return flat != 0 and flon != 0 and -90 <= flat <= 90 and -180 <= flon <= 180


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


def _coords_from_metadata_block(block: dict[str, Any]) -> tuple[float | None, float | None]:
    lat = _parse_coord(block.get("latitude"))
    lon = _parse_coord(block.get("longitude"))
    if lat is None:
        lat = _parse_coord(block.get("LATITUDE"))
    if lon is None:
        lon = _parse_coord(block.get("LONGITUDE"))
    return lat, lon


def resolve_agent5_coordinates(
    addr: Address,
    a1: Agent1Result | None = None,
) -> tuple[float | None, float | None, str]:
    """
    Best coordinates for Street View analysis and metadata.

    Priority:
      1. raw_metadata.final_resolution (pipeline consolidated)
      2. agent1_results.smarty_lat / smarty_lon
      3. addresses.validated_latitude / validated_longitude (Agent 0)
      4. raw_metadata.new (Agent 0 validated bundle)
      5. addresses.latitude / longitude (upload)
    """
    meta = addr.raw_metadata if isinstance(addr.raw_metadata, dict) else {}

    final = meta.get("final_resolution")
    if isinstance(final, dict):
        lat, lon = _coords_from_metadata_block(final)
        if _coords_valid(lat, lon):
            return float(lat), float(lon), "final_resolution"

    if a1 and _coords_valid(a1.smarty_lat, a1.smarty_lon):
        return float(a1.smarty_lat), float(a1.smarty_lon), "agent1_smarty"

    if _coords_valid(addr.validated_latitude, addr.validated_longitude):
        return float(addr.validated_latitude), float(addr.validated_longitude), "agent0_validated"

    new_block = meta.get("new")
    if isinstance(new_block, dict):
        lat, lon = _coords_from_metadata_block(new_block)
        if _coords_valid(lat, lon):
            return float(lat), float(lon), "agent0_validated_metadata"

    if _coords_valid(addr.latitude, addr.longitude):
        return float(addr.latitude), float(addr.longitude), "upload"

    return None, None, "none"


def extract_house_number(raw_address: str | None) -> str:
    return _leading_house_number(raw_address or "") or ""


def _address_type_from_metadata(raw_metadata: dict | None) -> str:
    if not raw_metadata:
        return "Residential"
    for key in ("address_type", "Address Type", "addr_type"):
        value = raw_metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "Residential"


def build_agent5_record(
    addr: Address,
    a1: Agent1Result | None,
    *,
    lat: float,
    lon: float,
) -> dict[str, Any]:
    """Map an ingestion Address row to the reference pipeline record shape."""
    raw_meta = addr.raw_metadata if isinstance(addr.raw_metadata, dict) else {}
    raw_address = resolve_best_address(
        raw_address=addr.raw_address,
        meta=raw_meta,
        city=addr.city,
        state=addr.state,
        zip_code=addr.zip_code,
        validated_address=addr.validated_raw_address,
        chosen_address=a1.chosen_standardized_address if a1 else None,
        final_address=(raw_meta.get("final_resolution") or {}).get("address")
        if isinstance(raw_meta.get("final_resolution"), dict)
        else None,
    ).strip()

    house_number = extract_house_number(raw_address)
    if not house_number and a1 and a1.chosen_standardized_address:
        house_number = extract_house_number(a1.chosen_standardized_address)
    if not house_number:
        for key in ("primary_number", "house_number", "House Number"):
            value = raw_meta.get(key)
            if value is not None and str(value).strip():
                candidate = str(value).strip().lstrip("0") or str(value).strip()
                if candidate.isdigit() or (len(candidate) <= 5 and any(c.isdigit() for c in candidate)):
                    house_number = str(value).strip()
                    break

    unit_number = ""
    for key in ("unit_number", "secondary_number", "Unit Number"):
        value = raw_meta.get(key)
        if isinstance(value, str) and value.strip():
            unit_number = value.strip()
            break

    return {
        "latitude": lat,
        "longitude": lon,
        "address": raw_address,
        "city": addr.city or "",
        "postal_code": addr.zip_code or "",
        "zip_code": addr.zip_code or "",
        "state": addr.state or "",
        "address_type": _address_type_from_metadata(raw_meta),
        "house_number": house_number,
        "street_address": raw_address,
        "street_direction": "",
        "street_suffix": "",
        "unit_number": unit_number,
    }


def simplify_structure_type(detail: str) -> tuple[str, bool]:
    """Map reference taxonomy → SFH / MDU / Commercial / Unknown for downstream agents."""
    mapping = {
        "detached_house": ("SFH", False),
        "townhouse": ("SFH", False),
        "low_rise_apt": ("MDU", True),
        "high_rise_apt": ("MDU", True),
        "commercial": ("Commercial", False),
        "industrial": ("Commercial", False),
        "mixed_use": ("MDU", True),
        "unclear": ("Unknown", False),
        "SFH": ("SFH", False),
        "MDU": ("MDU", True),
        "Commercial": ("Commercial", False),
        "Unknown": ("Unknown", False),
    }
    return mapping.get(detail, ("Unknown", False))

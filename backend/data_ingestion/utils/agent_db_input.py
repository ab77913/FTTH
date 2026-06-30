"""Database-first address and coordinate resolution for pipeline agents."""
from __future__ import annotations

from typing import Any

from data_ingestion.database.models import Address, Agent1Result
from data_ingestion.utils.agent1_input import resolve_agent1_input
from data_ingestion.utils.address_match import resolve_upload_address_line
from data_ingestion.utils.res_com_addressing import (
    ENABLE_RES_COM_ADDRESSING,
    resolve_best_address,
)

_DB_SOURCE = "database"


def _db_raw_address(addr: Address) -> str | None:
    """Read raw address from Address columns (ignore non-str mock attributes)."""
    for attr in ("source_raw_address", "raw_address"):
        val = getattr(addr, attr, None)
        if isinstance(val, str):
            s = val.strip()
            if s:
                return s
    return None


def _coords_valid(lat: Any, lon: Any) -> bool:
    if isinstance(lat, bool) or isinstance(lon, bool):
        return False
    try:
        lat_f = float(lat)
        lon_f = float(lon)
    except (TypeError, ValueError):
        return False
    if lat_f == 0.0 and lon_f == 0.0:
        return False
    return -90.0 <= lat_f <= 90.0 and -180.0 <= lon_f <= 180.0


def db_input_bundle(addr: Address) -> dict[str, Any]:
    """Snapshot of address fields stored on the Address row (for audit metadata)."""
    fields = resolve_agent1_input(addr)
    city_state = ", ".join(filter(None, [fields.city, fields.state]))
    return {
        "street_number_name": fields.raw_address,
        "city_state": city_state,
        "zip_postal_code": fields.zip_code or "",
        "latitude": fields.latitude,
        "longitude": fields.longitude,
        "country_code": fields.country,
    }


def resolve_db_address_line(
    addr: Address,
    a1: Agent1Result | None = None,
) -> tuple[str, str]:
    """Build geocoding input from upload address (+ city/state/zip), then Agent 1 fallback."""
    meta = addr.raw_metadata if isinstance(addr.raw_metadata, dict) else {}
    fields = resolve_agent1_input(addr)
    if ENABLE_RES_COM_ADDRESSING:
        best_upload = resolve_best_address(
            raw_address=_db_raw_address(addr),
            meta=meta,
            city=fields.city,
            state=fields.state,
            zip_code=fields.zip_code,
            validated_address=getattr(addr, "validated_street_line", None),
            chosen_address=(a1.chosen_standardized_address if a1 else None),
            final_address=(meta.get("final_resolution") or {}).get("address")
            if isinstance(meta.get("final_resolution"), dict)
            else None,
        )
        if best_upload:
            parts: list[str] = [best_upload]
            city_state = ", ".join(filter(None, [fields.city, fields.state]))
            if city_state and city_state.lower() not in best_upload.lower():
                parts.append(city_state)
            if fields.zip_code and str(fields.zip_code).strip()[:5] not in best_upload:
                parts.append(str(fields.zip_code).strip())
            return ", ".join(filter(None, parts)), "resolved_upload"

    upload = resolve_upload_address_line(_db_raw_address(addr), meta)
    if upload:
        parts: list[str] = [upload]
        city_state = ", ".join(filter(None, [fields.city, fields.state]))
        if city_state and city_state.lower() not in upload.lower():
            parts.append(city_state)
        if fields.zip_code and str(fields.zip_code).strip()[:5] not in upload:
            parts.append(str(fields.zip_code).strip())
        return ", ".join(filter(None, parts)), "upload"

    if a1 and a1.chosen_standardized_address and str(a1.chosen_standardized_address).strip():
        return str(a1.chosen_standardized_address).strip(), "agent1.chosen_standardized_address"
    if a1 and a1.canonical_address and str(a1.canonical_address).strip():
        return str(a1.canonical_address).strip(), "agent1.canonical_address"

    parts = []
    if fields.raw_address:
        parts.append(fields.raw_address)
    city_state = ", ".join(filter(None, [fields.city, fields.state]))
    if city_state:
        parts.append(city_state)
    if fields.zip_code:
        tail = fields.zip_code
        if fields.country and fields.country not in {"", "US"}:
            tail = f"{tail}, {fields.country}"
        parts.append(tail)
    elif fields.country and fields.country not in {"", "US"}:
        parts.append(fields.country)

    line = ", ".join(filter(None, parts))
    if line:
        return line, _DB_SOURCE
    raw = getattr(addr, "raw_address", None)
    if isinstance(raw, (str, int, float)) and str(raw).strip():
        return str(raw).strip(), "addresses.raw_address"
    return "", "none"


def resolve_db_hint_coordinates(
    addr: Address,
    a1: Agent1Result | None = None,
) -> tuple[float | None, float | None]:
    """Best-effort coordinates from Address DB columns (not upload file snapshots)."""
    fields = resolve_agent1_input(addr)
    if _coords_valid(fields.latitude, fields.longitude):
        return float(fields.latitude), float(fields.longitude)

    for lat, lon in (
        (getattr(addr, "source_latitude", None), getattr(addr, "source_longitude", None)),
        (getattr(addr, "latitude", None), getattr(addr, "longitude", None)),
        (getattr(addr, "validated_latitude", None), getattr(addr, "validated_longitude", None)),
        (getattr(a1, "smarty_lat", None), getattr(a1, "smarty_lon", None)) if a1 else (None, None),
    ):
        if _coords_valid(lat, lon):
            return float(lat), float(lon)
    return None, None

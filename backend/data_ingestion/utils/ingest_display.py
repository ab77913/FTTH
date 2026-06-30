"""Frozen upload-file values for Data Records table display (never agent output)."""
from __future__ import annotations

from typing import Any

from data_ingestion.database.models import Address


def _old_bundle(meta: dict[str, Any]) -> dict[str, Any]:
    old = meta.get("old")
    return old if isinstance(old, dict) else {}


def _split_city_state(city_state: str) -> tuple[str, str]:
    cs = (city_state or "").strip()
    if not cs:
        return "", ""
    if "," in cs:
        city, state = [p.strip() for p in cs.split(",", 1)]
        return city, state
    return cs, ""


def ingest_display_fields(addr: Address) -> dict[str, Any]:
    """
    Return upload-time field values for the Data Records table.

    Priority:
      1. ``addresses.source_*`` columns (frozen at ingest)
      2. ``raw_metadata['old']`` snapshot (written at ingest)
      3. Current ``addresses`` columns (legacy rows only)
    """
    meta = addr.raw_metadata if isinstance(addr.raw_metadata, dict) else {}
    old = _old_bundle(meta)
    old_city, old_state = _split_city_state(str(old.get("city_state") or ""))

    raw_address = (
        getattr(addr, "source_raw_address", None)
        or old.get("street_number_name")
        or addr.raw_address
    )

    city = old.get("city") or old_city or addr.city
    state = old.get("state") or old_state or addr.state
    zip_code = old.get("zip_postal_code") or addr.zip_code

    latitude = getattr(addr, "source_latitude", None)
    if latitude is None:
        latitude = old.get("latitude")
    if latitude is None:
        latitude = addr.latitude

    longitude = getattr(addr, "source_longitude", None)
    if longitude is None:
        longitude = old.get("longitude")
    if longitude is None:
        longitude = addr.longitude

    return {
        "source_row_number": addr.source_row_number,
        "raw_address": raw_address or "",
        "city": city or "",
        "state": state or "",
        "zip_code": zip_code or "",
        "latitude": latitude,
        "longitude": longitude,
    }

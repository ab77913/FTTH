"""Persist per-provider geocoder API results as sub-JSON in addresses.raw_metadata."""
from __future__ import annotations

from typing import Any

from sqlalchemy import inspect
from sqlalchemy.orm.attributes import flag_modified

from data_ingestion.database.models import Address

# Keys written under raw_metadata — kept out of table column expansion (api_server SKIP_KEYS).
PROVIDER_RESULT_KEYS = frozenset({
    "reverse_geocoding",
    "google_geocoding",
    "geocoding",  # legacy alias for google_geocoding
    "osm",
    "street_interpolated",
})


def _copy_json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _copy_json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_copy_json_safe(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def geo_dict_to_provider_block(geo: dict[str, Any] | None, *, ok: bool | None = None) -> dict[str, Any]:
    """Normalize internal geocoder dict → compact raw_metadata provider block."""
    if not geo:
        return {"ok": False, "source": "", "formatted_address": ""}

    formatted = (
        geo.get("formatted_address")
        or geo.get("display_name")
        or ""
    )
    block: dict[str, Any] = {
        "formatted_address": formatted,
        "display_name": geo.get("display_name") or formatted,
        "latitude": geo.get("latitude"),
        "longitude": geo.get("longitude"),
        "location_type": geo.get("location_type") or "",
        "address_type": geo.get("address_type") or "",
        "address_types": geo.get("address_types") or geo.get("types") or [],
        "place_id": geo.get("place_id") or "",
        "source": str(geo.get("source") or "").upper() or None,
        "house_number": geo.get("house_number") or "",
        "road": geo.get("road") or "",
        "city": geo.get("city") or "",
        "state": geo.get("state") or "",
        "postcode": geo.get("postcode") or "",
        "country": geo.get("country") or "",
        "country_code": geo.get("country_code") or "",
        "ok": geo.get("ok") if geo.get("ok") is not None else (ok if ok is not None else bool(formatted)),
    }
    for key in (
        "place_rank",
        "importance",
        "confidence",
        "_base_confidence",
        "address_match_percent",
        "address_accepted",
        "coord_distance_m",
        "fallback_used",
        "is_estimated",
        "_decision_reason",
        "_reject_reason",
        "pinned_to_source",
        "has_house_number",
        "api_status",
        "error_message",
    ):
        if key in geo and geo[key] is not None:
            block[key] = geo[key]

    return _copy_json_safe({k: v for k, v in block.items() if v is not None and v != ""})


def persist_provider_result(addr: Address, key: str, data: dict[str, Any] | None) -> None:
    """Write one provider sub-JSON block into raw_metadata."""
    if addr is None or not key:
        return
    meta = dict(addr.raw_metadata or {})
    meta[key] = geo_dict_to_provider_block(data) if data is not None else {"ok": False}
    if key == "google_geocoding":
        meta["geocoding"] = meta[key]
    addr.raw_metadata = meta
    if inspect(addr, raiseerr=False) is not None:
        flag_modified(addr, "raw_metadata")


def persist_provider_results(addr: Address, results: dict[str, Any]) -> None:
    """Merge multiple provider blocks (agent2 executed_models shape)."""
    if addr is None or not results:
        return
    meta = dict(addr.raw_metadata or {})
    for key, payload in results.items():
        if key not in PROVIDER_RESULT_KEYS:
            continue
        if not isinstance(payload, dict):
            continue
        block = geo_dict_to_provider_block(payload)
        if key == "geocoding":
            meta["google_geocoding"] = block
            meta["geocoding"] = block
        else:
            meta[key] = block
    addr.raw_metadata = meta
    if inspect(addr, raiseerr=False) is not None:
        flag_modified(addr, "raw_metadata")

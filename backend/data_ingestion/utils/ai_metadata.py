"""Compact AI output fields stored under addresses.raw_metadata['ai']."""
from __future__ import annotations

from typing import Any

from sqlalchemy import inspect
from sqlalchemy.orm.attributes import flag_modified

from data_ingestion.database.models import Address

AI_FIELD_KEYS = (
    "ai_address",
    "ai_street",
    "ai_latitude",
    "ai_longitude",
    "ai_city",
    "ai_country",
    "ai_zip",
    "ai_zip_code",
    "ai_confidence",
    "ai_remarks",
    "ai_type",
    "ai_agent_name",
)


def ai_metadata_dict(**fields: Any) -> dict[str, Any]:
    """Build a normalized ai{} block from keyword fields."""
    mapping = {
        "ai_address": fields.get("address") or fields.get("ai_address"),
        "ai_street": fields.get("street") or fields.get("ai_street") or fields.get("address"),
        "ai_latitude": fields.get("latitude") if fields.get("latitude") is not None else fields.get("ai_latitude"),
        "ai_longitude": fields.get("longitude") if fields.get("longitude") is not None else fields.get("ai_longitude"),
        "ai_city": fields.get("city") or fields.get("ai_city"),
        "ai_country": fields.get("country") or fields.get("ai_country"),
        "ai_zip": fields.get("zip") or fields.get("ai_zip"),
        "ai_zip_code": fields.get("zip_code") or fields.get("ai_zip_code"),
        "ai_confidence": fields.get("confidence") if fields.get("confidence") is not None else fields.get("ai_confidence"),
        "ai_remarks": fields.get("remarks") or fields.get("ai_remarks"),
        "ai_type": fields.get("ai_type"),
        "ai_agent_name": fields.get("agent_name") or fields.get("ai_agent_name") or fields.get("source_agent"),
    }
    out: dict[str, Any] = {}
    for key in AI_FIELD_KEYS:
        val = mapping.get(key)
        if val is not None and val != "":
            out[key] = val
    return out


def persist_ai_metadata(addr: Address, **fields: Any) -> None:
    """Merge compact AI fields into raw_metadata['ai'] (replaces full API blobs)."""
    if addr is None:
        return
    block = ai_metadata_dict(**fields)
    if not block:
        return

    meta = dict(addr.raw_metadata or {})
    existing = meta.get("ai")
    ai = dict(existing) if isinstance(existing, dict) else {}
    ai.update(block)
    meta["ai"] = ai

    # Drop legacy full API response blobs from older pipeline versions (not current provider blocks).
    for legacy_key in (
        "smarty",
        "melissa",
        "regrid",
        "tigerline",
        "parcel_nominatim",
        "streetview",
    ):
        meta.pop(legacy_key, None)

    addr.raw_metadata = meta
    if inspect(addr, raiseerr=False) is not None:
        flag_modified(addr, "raw_metadata")

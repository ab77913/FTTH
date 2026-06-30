"""Rule legend classification from addresses.raw_metadata."""
from __future__ import annotations

from typing import Any


def _normalize_token(value: Any) -> str:
    return str(value or "").strip().upper().replace("-", "_")


def is_uploaded_geospatial_input(meta: dict[str, Any] | None) -> bool:
    """True for original KML/KMZ rows, excluding agent-discovered rows."""
    meta = meta or {}
    if str(meta.get("file_role") or "").strip().lower() != "geospatial":
        return False
    if meta.get("agent0_discovered") is True or meta.get("agent7_discovered") is True:
        return False
    return str(meta.get("source_format") or "").strip().lower() not in {"agent0", "agent7"}


def address_found_from_raw_metadata(meta: dict[str, Any] | None) -> bool:
    """Return True when agent outcome is MATCH or AUTO_ACCEPT."""
    meta = meta or {}
    av = meta.get("address_validation") if isinstance(meta.get("address_validation"), dict) else {}
    match_status = _normalize_token(av.get("match_status") or meta.get("coord_address_match_status"))
    if match_status == "MATCH":
        return True

    final = meta.get("final_resolution") if isinstance(meta.get("final_resolution"), dict) else {}
    final_status = _normalize_token(final.get("status"))
    if final_status == "AUTO_ACCEPT":
        return True

    validation_status = _normalize_token(meta.get("validation_status"))
    return validation_status == "AUTO_ACCEPT"



def is_sticky_duplicate(meta: dict[str, Any] | None, rule_data: dict[str, Any] | None = None) -> bool:
    """True when any persisted classification has already marked the row duplicate."""
    meta = meta or {}
    rule_data = rule_data or {}
    merge_classification = meta.get("merge_classification")
    if not isinstance(merge_classification, dict):
        merge_classification = {}

    candidates = (
        meta.get("record_status"),
        meta.get("merge_status"),
        meta.get("rule_status"),
        merge_classification.get("status"),
        merge_classification.get("rule_status"),
        rule_data.get("merge_status"),
        rule_data.get("rule_status"),
        rule_data.get("status"),
    )
    return any(_normalize_token(value).lower() in {"duplicate", "duplicated"} for value in candidates)

def rule_status_from_raw_metadata(
    meta: dict[str, Any] | None,
    *,
    merge_status: str | None = None,
) -> tuple[str, str, str]:
    """Map raw_metadata (+ optional persisted merge_status) to rule legend bucket."""
    meta = meta or {}
    stored = _normalize_token(merge_status or meta.get("merge_status")).lower()

    if stored == "duplicate" or is_sticky_duplicate(meta):
        return (
            "duplicate",
            "white",
            "Duplicate address in uploaded raw data",
        )
    if stored == "new":
        # KMZ input addresses are never "new" — they came from the geospatial input file
        if is_uploaded_geospatial_input(meta):
            return (
                "verified",
                "green",
                "KMZ input address (no matching CSV record)",
            )
        return (
            "new",
            "yellow",
            "New address identified from KML/KMZ but not present in CSV/Excel",
        )

    if address_found_from_raw_metadata(meta):
        av = meta.get("address_validation") if isinstance(meta.get("address_validation"), dict) else {}
        if _normalize_token(av.get("match_status")) == "MATCH":
            return (
                "verified",
                "green",
                "Address found (coordinate and address match)",
            )
        return (
            "verified",
            "green",
            "Address found and validated",
        )

    return (
        "invalid",
        "red",
        "Address not found or could not be validated",
    )

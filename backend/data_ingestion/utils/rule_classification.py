"""Rule legend classification from addresses.raw_metadata."""
from __future__ import annotations

from typing import Any

MISSING_ADDRESS_REASON = "Address data not given in CSV"
INTERPOLATED_FORWARD_CONFLICT_REASON = (
    "Address not found: forward geocode is range-interpolated and conflicts with reverse-at-pin house number"
)

_MISSING_ADDRESS_MARKERS = (
    "ADDRESS DATA NOT GIVEN IN CSV",
    "ADDRESS DATA NOT GIVEN",
    "ADDRESS NOT GIVEN",
    "NO ADDRESS GIVEN",
    "MISSING ADDRESS",
    "BLANK ADDRESS",
)

_UNSET = object()


def _normalize_token(value: Any) -> str:
    return str(value or "").strip().upper().replace("-", "_")


def _normalize_phrase(value: Any) -> str:
    text = str(value or "").strip().upper()
    for char in ("_", "-", "/", "\\", "\n", "\r", "\t"):
        text = text.replace(char, " ")
    return " ".join(text.split())


def _meta_text_candidates(meta: dict[str, Any]) -> list[str]:
    candidates: list[str] = []
    direct_keys = (
        "COMMENTS",
        "comments",
        "comment",
        "Comment",
        "remark",
        "remarks",
        "merge_reason",
        "rule_reason",
        "reason",
        "record_status",
        "validation_status",
    )
    for key in direct_keys:
        value = meta.get(key)
        if isinstance(value, str) and value.strip():
            candidates.append(value)

    for nested_key in ("merge_classification", "address_validation", "final_resolution"):
        nested = meta.get(nested_key)
        if isinstance(nested, dict):
            for value in nested.values():
                if isinstance(value, str) and value.strip():
                    candidates.append(value)
    return candidates


def is_missing_address_record(meta: dict[str, Any] | None, raw_address: Any = _UNSET) -> bool:
    """True when the source row explicitly has no submitted address to validate."""
    meta = meta or {}
    for value in _meta_text_candidates(meta):
        text = _normalize_phrase(value)
        if any(marker in text for marker in _MISSING_ADDRESS_MARKERS):
            return True

    if raw_address is not _UNSET and (raw_address is None or not str(raw_address).strip()):
        return str(meta.get("file_role") or "").strip().lower() == "tabular"
    return False


def _int_value(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def is_interpolated_forward_reverse_conflict(meta: dict[str, Any] | None) -> bool:
    """True when a green match only came from an interpolated forward geocode."""
    meta = meta or {}
    av = meta.get("address_validation") if isinstance(meta.get("address_validation"), dict) else {}
    if _normalize_token(av.get("match_status") or meta.get("coord_address_match_status")) != "MATCH":
        return False

    selected_direction = str(av.get("selected_direction") or "").strip().lower()
    location_type = str(av.get("location_type") or "").strip().upper()
    notes = _normalize_phrase(av.get("notes") or meta.get("coord_address_validation_notes"))
    reverse_pct = _int_value(av.get("reverse_address_match_percent"), 100)
    forward_pct = _int_value(av.get("forward_address_match_percent"), 0)

    return (
        selected_direction == "forward"
        and location_type == "RANGE_INTERPOLATED"
        and forward_pct >= 100
        and reverse_pct < 100
        and "REVERSE AT PIN RETURNED HOUSE NUMBER" in notes
    )


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
    if is_interpolated_forward_reverse_conflict(meta):
        return False
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
    if stored == "excluded" or is_missing_address_record(meta):
        return (
            "excluded",
            "",
            MISSING_ADDRESS_REASON,
        )
    if is_interpolated_forward_reverse_conflict(meta):
        return (
            "invalid",
            "red",
            INTERPOLATED_FORWARD_CONFLICT_REASON,
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

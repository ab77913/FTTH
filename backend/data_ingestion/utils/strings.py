from __future__ import annotations

import re
from typing import Any


_NULL_LIKE = {"", "none", "null", "nan", "nat", "n/a", "na", "<na>"}


def clean_value(value: Any) -> Any:
    """Convert pandas/numpy empty values to None and strip strings."""
    if value is None:
        return None

    # pandas NA/NaN support without importing pandas here
    try:
        if value != value:  # noqa: PLR0124 - NaN check
            return None
    except Exception:
        pass

    if isinstance(value, str):
        stripped = value.strip()
        return None if stripped.lower() in _NULL_LIKE else stripped

    return value


def normalize_header(header: Any) -> str:
    """Normalize input column/header names for fuzzy mapping."""
    header = "" if header is None else str(header)
    header = header.strip().lower()
    header = re.sub(r"^ns\d+:", "", header)
    header = header.replace("_", " ").replace("-", " ")
    header = re.sub(r"[^a-z0-9]+", " ", header)
    return re.sub(r"\s+", " ", header).strip()


def normalize_address_key(value: str | None) -> str | None:
    """Build a normalized address-only key for merge and duplicate decisions."""
    if not value:
        return None

    text = value.upper().strip()
    replacements = {
        r"\bN\b": "NORTH",
        r"\bS\b": "SOUTH",
        r"\bE\b": "EAST",
        r"\bW\b": "WEST",
        r"\bSTREET\b": "ST",
        r"\bAVENUE\b": "AVE",
        r"\bBOULEVARD\b": "BLVD",
        r"\bDRIVE\b": "DR",
        r"\bROAD\b": "RD",
        r"\bCOURT\b": "CT",
        r"\bLANE\b": "LN",
    }
    for pattern, replacement in replacements.items():
        text = re.sub(pattern, replacement, text)

    text = re.sub(r"[^A-Z0-9]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None



def normalize_duplicate_address_key(value: str | None) -> str | None:
    """Normalize only the address text used to decide duplicate households."""
    value = clean_value(value)
    if value is None:
        return None
    return normalize_address_key(str(value))


def normalize_exact_duplicate_key(value: str | None) -> str | None:
    """Build an exact-address duplicate key without street-name canonicalization."""
    value = clean_value(value)
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip().casefold()
    return text or None


def safe_float(value: Any) -> float | None:
    value = clean_value(value)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_kml_coordinate(value: Any) -> tuple[float | None, float | None]:
    """Parse KML coordinate strings as longitude, latitude.

    KML stores coordinates as `lon,lat,alt`. This returns `(lat, lon)` for canonical records.
    """
    value = clean_value(value)
    if not value:
        return None, None

    text = str(value).strip()
    first_coord = text.split()[0]
    parts = first_coord.split(",")
    if len(parts) < 2:
        return None, None

    lon = safe_float(parts[0])
    lat = safe_float(parts[1])
    return lat, lon



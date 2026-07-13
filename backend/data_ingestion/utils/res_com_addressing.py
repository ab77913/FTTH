"""Single-switch handling for Res/Com upload rows.

Set ENABLE_RES_COM_ADDRESSING = False to return the pipeline to the previous
raw-address behavior without removing imports/call sites.
"""
from __future__ import annotations
from data_ingestion.config.paths import PROJECT_ROOT

import re
import os
from typing import Any

from data_ingestion.utils.strings import clean_value, normalize_address_key, normalize_header

_TRUE_VALUES = {"1", "true", "yes", "on", "enabled"}
_FALSE_VALUES = {"0", "false", "no", "off", "disabled"}


def _coerce_bool(value: Any) -> bool | None:
    raw = str(value or "").strip().lower()
    if raw in _FALSE_VALUES:
        return False
    if raw in _TRUE_VALUES:
        return True
    return None


def _read_env_file_flag() -> bool | None:
    env_file = PROJECT_ROOT / ".env"
    if not env_file.exists():
        return None
    try:
        lines = env_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() != "RES_COM_ADDRESSING_ENABLED":
            continue
        value = value.strip().strip('"').strip("'")
        return _coerce_bool(value)
    return None


def res_com_addressing_enabled() -> bool:
    """Read the Res/Com feature switch from .env/settings at call time."""
    env_value = _coerce_bool(os.environ.get("RES_COM_ADDRESSING_ENABLED"))
    if env_value is not None:
        return env_value

    file_value = _read_env_file_flag()
    if file_value is not None:
        return file_value

    try:
        from data_ingestion.config.settings import get_settings

        return bool(get_settings().res_com_addressing_enabled)
    except Exception:
        return True


class _DynamicResComSwitch:
    def __bool__(self) -> bool:
        return res_com_addressing_enabled()

    def __repr__(self) -> str:
        return str(res_com_addressing_enabled())


ENABLE_RES_COM_ADDRESSING = _DynamicResComSwitch()

_RES_VALUES = {"RES", "RESIDENTIAL", "RESIDENCE", "HOUSEHOLD", "HOUSEHOLDS"}
_COM_VALUES = {"COM", "COMMERCIAL", "BUSINESS"}
_TYPE_KEYS = ("Address Type", "address_type", "addr_type", "type", "category")
_FULL_ADDRESS_KEYS = (
    "ADDRESS",
    "Address",
    "address",
    "Full Address",
    "full_address",
    "Service Address",
    "service_address",
    "Property Address",
    "property_address",
)
_STREET_NO_KEYS = ("Street No", "Street Number", "street_no", "street_number", "house_number")
_STREET_NO_SUFFIX_KEYS = (
    "Street Number Suffix",
    "street_number_suffix",
    "Street No Suffix",
    "street_no_suffix",
    "house_number_suffix",
    "Civic Number Suffix",
    "civic_number_suffix",
    "Number Suffix",
    "number_suffix",
)
_STREET_NAME_KEYS = ("Street Name", "street_name", "Street", "street", "road")
_STREET_TYPE_KEYS = ("Street Type", "street_type", "Street Dir", "street_direction")
_CITY_KEYS = ("City", "city")
_STATE_KEYS = ("State", "state")
_ZIP_KEYS = ("Zip", "ZIP", "zip", "Zip Code", "zip_code", "zipcode", "Postal Code", "postal_code")

_HOUSE_AND_TEXT_RE = re.compile(r"^\s*\d+[A-Za-z]?(?:-\d+[A-Za-z]?)?\s+\S+")
_HOUSE_ONLY_RE = re.compile(r"^\s*\d+[A-Za-z]?(?:-\d+[A-Za-z]?)?\s*$")


def _text(value: Any) -> str:
    cleaned = clean_value(value)
    if cleaned is None or isinstance(cleaned, (dict, list, tuple, set)):
        return ""
    return str(cleaned).strip()


def _normalized_meta_lookup(meta: dict[str, Any]) -> dict[str, Any]:
    lookup: dict[str, Any] = {}
    for key, value in meta.items():
        norm_key = normalize_header(key)
        if norm_key and norm_key not in lookup:
            lookup[norm_key] = value
    return lookup


def _first(meta: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = _text(meta.get(key))
        if value:
            return value
    lookup = _normalized_meta_lookup(meta)
    for key in keys:
        norm_key = normalize_header(key)
        if not norm_key:
            continue
        value = _text(lookup.get(norm_key))
        if value:
            return value
    return ""


def _combine_street_number(meta: dict[str, Any]) -> str:
    """Merge civic number + alphabetic suffix (e.g. 37 + A -> 37A for Canada Post)."""
    street_no = _first(meta, _STREET_NO_KEYS)
    if not street_no:
        return ""
    suffix = _first(meta, _STREET_NO_SUFFIX_KEYS)
    if not suffix:
        return street_no

    suffix_text = suffix.strip()
    if not suffix_text:
        return street_no

    number_upper = street_no.upper()
    suffix_upper = suffix_text.upper()
    if number_upper.endswith(suffix_upper):
        return street_no

    if len(suffix_text) == 1 and suffix_text.isalpha():
        return f"{street_no}{suffix_text}"

    if suffix_text.startswith("-") or suffix_text.startswith("/"):
        return f"{street_no}{suffix_text}"

    return f"{street_no} {suffix_text}"


def _build_street_segment(meta: dict[str, Any]) -> str:
    """Street number (+ suffix) + name + optional street type (e.g. 37A John ST)."""
    street_no = _combine_street_number(meta)
    street_name = _first(meta, _STREET_NAME_KEYS)
    street_type = _first(meta, _STREET_TYPE_KEYS)
    if not street_no and not street_name:
        return ""

    parts: list[str] = []
    if street_no and street_name:
        parts.append(f"{street_no} {street_name}".strip())
    elif street_no:
        parts.append(street_no)
    elif street_name:
        parts.append(street_name)

    street_line = parts[0] if parts else ""
    if street_line and street_type:
        type_upper = street_type.upper()
        tokens = street_line.upper().split()
        if not tokens or tokens[-1] != type_upper:
            street_line = f"{street_line} {street_type}".strip()
    return street_line


def _civic_components_available(meta: dict[str, Any]) -> bool:
    return bool(_first(meta, _STREET_NO_KEYS) and _first(meta, _STREET_NAME_KEYS))


def _has_distinguishing_civic_detail(meta: dict[str, Any]) -> bool:
    """True when a street-number suffix must override a generic ADDRESS column."""
    return bool(_first(meta, _STREET_NO_SUFFIX_KEYS))


def _leading_house_number_token(value: str) -> str:
    match = re.match(r"^\s*(\d+[A-Za-z]?)", value or "")
    return match.group(1).upper() if match else ""


def _build_full_address_from_components(
    meta: dict[str, Any],
    *,
    city: Any = None,
    state: Any = None,
    zip_code: Any = None,
) -> str:
    street = _build_street_segment(meta)
    if not street:
        return ""
    parts = [street]
    city_text = _text(city) or _first(meta, _CITY_KEYS)
    state_text = _text(state) or _first(meta, _STATE_KEYS)
    zip_text = _text(zip_code) or _first(meta, _ZIP_KEYS)
    city_state = ", ".join(part for part in (city_text, state_text) if part)
    if city_state:
        parts.append(city_state)
    if zip_text:
        parts.append(zip_text[:10])
    return ", ".join(parts)


def _component_address_preferred(
    meta: dict[str, Any],
    *,
    raw_address: Any = None,
) -> bool:
    if not _civic_components_available(meta):
        return False
    if _has_distinguishing_civic_detail(meta):
        return True
    built_segment = _build_street_segment(meta)
    combined_no = _combine_street_number(meta).upper().replace(" ", "")
    if not combined_no:
        return False
    for value in (_first(meta, _FULL_ADDRESS_KEYS), raw_address):
        text = _text(value)
        if not looks_like_real_address(text):
            continue
        street_part = text.split(",", 1)[0].strip()
        full_no = _leading_house_number_token(street_part)
        if full_no and full_no != combined_no:
            return True
    return False


def compact_address_type(value: Any) -> str:
    """Return residential/commercial/unknown for Res/Com-like values."""
    token = _text(value).upper()
    if token in _RES_VALUES:
        return "residential"
    if token in _COM_VALUES:
        return "commercial"
    return "unknown"


def is_res_com_token(value: Any) -> bool:
    return compact_address_type(value) in {"residential", "commercial"}


def address_type_label(address_type: str) -> str:
    if address_type == "residential":
        return "Residential"
    if address_type == "commercial":
        return "Commercial"
    return "Unknown"


def category_for_address_type(address_type: str) -> str:
    if address_type == "residential":
        return "household"
    if address_type == "commercial":
        return "commercial"
    return "unknown"


def looks_like_real_address(value: Any) -> bool:
    """True for a usable street/full address; false for Res/Com/category tokens."""
    text = _text(value)
    if not text or is_res_com_token(text):
        return False
    if _HOUSE_AND_TEXT_RE.search(text):
        return True
    if "," in text and any(char.isdigit() for char in text) and len(text.split(",")) >= 2:
        return True
    return False


def build_address_from_metadata(
    meta: dict[str, Any] | None,
    *,
    raw_address: Any = None,
    city: Any = None,
    state: Any = None,
    zip_code: Any = None,
) -> str:
    """Build the best household address string while ignoring Res/Com labels."""
    if not ENABLE_RES_COM_ADDRESSING:
        return _text(raw_address)

    metadata = meta if isinstance(meta, dict) else {}

    if _component_address_preferred(metadata, raw_address=raw_address):
        built = _build_full_address_from_components(
            metadata,
            city=city,
            state=state,
            zip_code=zip_code,
        )
        if built:
            return built

    for value in (_first(metadata, _FULL_ADDRESS_KEYS), raw_address):
        if looks_like_real_address(value):
            return _text(value)

    if _civic_components_available(metadata):
        built = _build_full_address_from_components(
            metadata,
            city=city,
            state=state,
            zip_code=zip_code,
        )
        if built:
            return built

    street_no = _combine_street_number(metadata)
    street_name = _first(metadata, _STREET_NAME_KEYS)
    if street_no and street_name:
        street = _build_street_segment(metadata)
        parts = [street]
        city_text = _text(city) or _first(metadata, _CITY_KEYS)
        state_text = _text(state) or _first(metadata, _STATE_KEYS)
        zip_text = _text(zip_code) or _first(metadata, _ZIP_KEYS)
        city_state = ", ".join(part for part in (city_text, state_text) if part)
        if city_state:
            parts.append(city_state)
        if zip_text:
            parts.append(zip_text[:10])
        return ", ".join(parts)

    full = _first(metadata, _FULL_ADDRESS_KEYS)
    if full and not is_res_com_token(full) and not _HOUSE_ONLY_RE.match(full):
        return full

    return "" if is_res_com_token(raw_address) else _text(raw_address)


def build_street_line_from_metadata(
    meta: dict[str, Any] | None,
    *,
    raw_address: Any = None,
) -> str:
    """Street number + name only — used for CSV/KMZ merge key comparison."""
    if not meta and not raw_address:
        return ""
    metadata = meta if isinstance(meta, dict) else {}
    if _component_address_preferred(metadata, raw_address=raw_address):
        built = _build_street_segment(metadata)
        if built:
            return built
    for value in (_first(metadata, _FULL_ADDRESS_KEYS), raw_address):
        text = _text(value)
        if looks_like_real_address(text):
            return text.split(",", 1)[0].strip()
    street_no = _combine_street_number(metadata)
    street_name = _first(metadata, _STREET_NAME_KEYS)
    if street_no and street_name:
        return _build_street_segment(metadata)
    return ""


def annotate_metadata(
    meta: dict[str, Any] | None,
    *,
    raw_address: Any = None,
) -> dict[str, Any]:
    """Add address_type/category metadata without removing original upload fields."""
    if not ENABLE_RES_COM_ADDRESSING:
        return dict(meta or {})

    updated = dict(meta or {})
    detected = "unknown"
    for key in _TYPE_KEYS:
        detected = compact_address_type(updated.get(key))
        if detected != "unknown":
            break
    if detected == "unknown" and is_res_com_token(raw_address):
        detected = compact_address_type(raw_address)

    if detected != "unknown":
        updated.setdefault("address_type", address_type_label(detected))
        updated.setdefault("address_type_code", "Res" if detected == "residential" else "Com")
        updated.setdefault("category", category_for_address_type(detected))
        updated.setdefault("res_com_handled", True)
        if is_res_com_token(raw_address):
            updated.setdefault("address_type_source_value", _text(raw_address))
    return updated


def resolve_best_address(
    *,
    raw_address: Any = None,
    meta: dict[str, Any] | None = None,
    city: Any = None,
    state: Any = None,
    zip_code: Any = None,
    validated_address: Any = None,
    chosen_address: Any = None,
    final_address: Any = None,
) -> str:
    """Best address for agents, duplicate rules, exports, and display."""
    if not ENABLE_RES_COM_ADDRESSING:
        return _text(raw_address)

    for value in (final_address, chosen_address, validated_address):
        if looks_like_real_address(value):
            return _text(value)
    return build_address_from_metadata(meta, raw_address=raw_address, city=city, state=state, zip_code=zip_code)


def duplicate_key_for_address(
    *,
    raw_address: Any = None,
    meta: dict[str, Any] | None = None,
    city: Any = None,
    state: Any = None,
    zip_code: Any = None,
    validated_address: Any = None,
    chosen_address: Any = None,
    final_address: Any = None,
) -> str | None:
    address = resolve_best_address(
        raw_address=raw_address,
        meta=meta,
        city=city,
        state=state,
        zip_code=zip_code,
        validated_address=validated_address,
        chosen_address=chosen_address,
        final_address=final_address,
    )
    key = normalize_address_key(address)
    if not key:
        return None
    zip_text = _text(zip_code)
    if zip_text and zip_text[:5] not in key:
        key = f"{key}|{zip_text[:5]}"
    return key

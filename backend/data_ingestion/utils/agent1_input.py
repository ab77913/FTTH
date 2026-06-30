"""
Resolve Agent 1 input fields from Address rows + raw_metadata bundles.

Priority: validated DB columns and raw_metadata ``new`` / ``ADDRESS`` over bare
``raw_address`` (often a routing street line without city/state/zip).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from data_ingestion.database.models import Address
from data_ingestion.utils.res_com_addressing import (
    ENABLE_RES_COM_ADDRESSING,
    resolve_best_address,
)

_US_STATE_TO_ABBREV: dict[str, str] = {
    "ALABAMA": "AL", "ALASKA": "AK", "ARIZONA": "AZ", "ARKANSAS": "AR",
    "CALIFORNIA": "CA", "COLORADO": "CO", "CONNECTICUT": "CT", "DELAWARE": "DE",
    "FLORIDA": "FL", "GEORGIA": "GA", "HAWAII": "HI", "IDAHO": "ID",
    "ILLINOIS": "IL", "INDIANA": "IN", "IOWA": "IA", "KANSAS": "KS",
    "KENTUCKY": "KY", "LOUISIANA": "LA", "MAINE": "ME", "MARYLAND": "MD",
    "MASSACHUSETTS": "MA", "MICHIGAN": "MI", "MINNESOTA": "MN", "MISSISSIPPI": "MS",
    "MISSOURI": "MO", "MONTANA": "MT", "NEBRASKA": "NE", "NEVADA": "NV",
    "NEW HAMPSHIRE": "NH", "NEW JERSEY": "NJ", "NEW MEXICO": "NM", "NEW YORK": "NY",
    "NORTH CAROLINA": "NC", "NORTH DAKOTA": "ND", "OHIO": "OH", "OKLAHOMA": "OK",
    "OREGON": "OR", "PENNSYLVANIA": "PA", "RHODE ISLAND": "RI", "SOUTH CAROLINA": "SC",
    "SOUTH DAKOTA": "SD", "TENNESSEE": "TN", "TEXAS": "TX", "UTAH": "UT",
    "VERMONT": "VT", "VIRGINIA": "VA", "WASHINGTON": "WA", "WEST VIRGINIA": "WV",
    "WISCONSIN": "WI", "WYOMING": "WY", "DISTRICT OF COLUMBIA": "DC",
}

_STATE_ZIP_RE = re.compile(
    r"^\s*([A-Za-z]{2,})\s+(\d{5}(?:-\d{4})?)\s*$"
)
_LAT_KEYS = re.compile(r"\blat(itude)?\b", re.I)
_LON_KEYS = re.compile(r"\b(lon|lng|long(itude)?)\b", re.I)


@dataclass
class Agent1InputFields:
    raw_address: str
    city: str
    state: str
    zip_code: str | None
    country: str
    latitude: float | None
    longitude: float | None
    sources: list[str] = field(default_factory=list)


def _normalize_country(code: str) -> str:
    c = _strip_str(code).upper()
    if not c:
        return "US"
    if c in {"USA", "UNITED STATES", "U.S.A.", "U.S.", "UNITED STATES OF AMERICA"}:
        return "US"
    if len(c) == 2 and c.isalpha():
        return c
    return c


def street_line(fields: Agent1InputFields, canonical: Any = None) -> str:
    """Best street / address line for provider ``street`` / ``a1`` fields."""
    if canonical is not None:
        parts = [
            canonical.house_number,
            canonical.street_name,
            canonical.street_suffix,
            canonical.unit_type,
            canonical.unit_number,
        ]
        joined = " ".join(p for p in parts if p).strip()
        if joined:
            return joined
    return fields.raw_address.strip()


def build_smarty_payload(
    fields: Agent1InputFields,
    canonical: Any = None,
) -> dict[str, Any]:
    """
    Smarty US Street API fields: street, city, state, zipcode.
    ``country`` is included for Agent1 audit logs (not sent to US Street API).
    """
    return {
        "street": street_line(fields, canonical),
        "city": fields.city,
        "state": fields.state,
        "zipcode": fields.zip_code or "",
        "country": fields.country,
        "candidates": 1,
    }


def build_melissa_payload(
    fields: Agent1InputFields,
    canonical: Any = None,
) -> dict[str, Any]:
    """Melissa GlobalAddress fields: a1, loc, admarea, postal, ctry."""
    return {
        "a1": street_line(fields, canonical),
        "loc": fields.city,
        "admarea": fields.state,
        "postal": fields.zip_code or "",
        "ctry": fields.country,
        "format": "JSON",
    }


def _bundle(meta: dict[str, Any], name: str) -> dict[str, Any]:
    b = meta.get(name)
    return b if isinstance(b, dict) else {}


def _routing(meta: dict[str, Any]) -> dict[str, Any]:
    r = meta.get("_routing")
    return r if isinstance(r, dict) else {}


def _strip_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _coerce_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if not isinstance(value, (int, float, str)):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if f == 0 else f


def _normalize_state(state: str) -> str:
    s = _strip_str(state).upper()
    if not s:
        return ""
    if len(s) == 2 and s.isalpha():
        return s
    return _US_STATE_TO_ABBREV.get(s, s)


def _parse_city_state(city_state: str) -> tuple[str, str]:
    cs = _strip_str(city_state)
    if not cs:
        return "", ""
    if "," in cs:
        city, state = [p.strip() for p in cs.split(",", 1)]
        return city, _normalize_state(state)
    return cs, ""


def _parse_full_address_line(line: str) -> tuple[str, str, str, str]:
    """
    Parse a comma-separated US-style line, e.g.
    ``214 Walnut Dr, Americus, GA 31719, USA``.
    """
    line = _strip_str(line)
    if not line:
        return "", "", "", ""

    parts = [p.strip() for p in line.split(",") if p.strip()]
    if not parts:
        return "", "", "", ""

    street = parts[0]
    city = state = zip_c = ""

    if len(parts) >= 2:
        # Drop trailing country token when present.
        tail = parts[1:]
        if tail and tail[-1].upper() in {"USA", "US", "UNITED STATES"}:
            tail = tail[:-1]

        if len(tail) == 1:
            city = tail[0]
        elif len(tail) >= 2:
            city = tail[0]
            m = _STATE_ZIP_RE.match(tail[1])
            if m:
                state = _normalize_state(m.group(1))
                zip_c = m.group(2)[:5]
            else:
                state = _normalize_state(tail[1])

        if len(tail) >= 3 and not zip_c:
            m = _STATE_ZIP_RE.match(tail[2])
            if m:
                state = _normalize_state(m.group(1))
                zip_c = m.group(2)[:5]

    return street, city, state, zip_c


def _coords_from_meta(meta: dict[str, Any]) -> tuple[float | None, float | None]:
    lat = lon = None
    for key, value in meta.items():
        if value is None or isinstance(value, (dict, list)):
            continue
        f = _coerce_float(value)
        if f is None:
            continue
        key_s = str(key)
        if lat is None and _LAT_KEYS.search(key_s) and -90 <= f <= 90:
            lat = f
        elif lon is None and _LON_KEYS.search(key_s) and -180 <= f <= 180:
            lon = f
    return lat, lon


def _first_nonempty(*values: Any) -> str:
    for v in values:
        if v is None or isinstance(v, bool):
            continue
        if not isinstance(v, (str, int, float)):
            continue
        s = _strip_str(v)
        if s:
            return s
    return ""


def _first_zip(*values: Any) -> str | None:
    for v in values:
        if v is None or isinstance(v, bool):
            continue
        if not isinstance(v, (str, int, float)):
            continue
        s = _strip_str(v)
        if s:
            return s[:5]
    return None


def resolve_agent1_input(addr: Address) -> Agent1InputFields:
    """Build the best available address components for Smarty/Melissa validation."""
    meta_raw = getattr(addr, "raw_metadata", None)
    meta = meta_raw if isinstance(meta_raw, dict) else {}
    new_b = _bundle(meta, "new")
    old_b = _bundle(meta, "old")
    routing = _routing(meta)

    full_line = _first_nonempty(
        meta.get("ADDRESS"),
        getattr(addr, "validated_raw_address", None),
    )

    parsed_street, parsed_city, parsed_state, parsed_zip = (
        _parse_full_address_line(full_line) if full_line and "," in full_line else ("", "", "", "")
    )
    db_raw_address = (
        getattr(addr, "raw_address", None)
        if isinstance(getattr(addr, "raw_address", None), (str, int, float))
        else None
    )

    new_city, new_state = _parse_city_state(new_b.get("city_state", ""))
    old_city, old_state = _parse_city_state(old_b.get("city_state", ""))
    validated_city_state = getattr(addr, "validated_city_state", None)
    val_city, val_state = _parse_city_state(
        validated_city_state if isinstance(validated_city_state, str) else ""
    )

    if ENABLE_RES_COM_ADDRESSING:
        raw_address = resolve_best_address(
            raw_address=db_raw_address,
            meta=meta,
            city=getattr(addr, "city", None),
            state=getattr(addr, "state", None),
            zip_code=getattr(addr, "zip_code", None),
            validated_address=getattr(addr, "validated_street_line", None)
            if isinstance(getattr(addr, "validated_street_line", None), (str, int, float))
            else None,
            final_address=(meta.get("final_resolution") or {}).get("address")
            if isinstance(meta.get("final_resolution"), dict)
            else None,
            chosen_address=new_b.get("street_number_name"),
        )
        if raw_address and "," in raw_address:
            raw_address = _first_nonempty(db_raw_address, parsed_street, raw_address)
    else:
        raw_address = _first_nonempty(
            getattr(addr, "validated_street_line", None)
            if isinstance(getattr(addr, "validated_street_line", None), (str, int, float))
            else None,
            new_b.get("street_number_name"),
            db_raw_address,
            meta.get("secondary_number"),
            meta.get("Secondary_number"),
            meta.get("address"),
            meta.get("raw_address"),
            parsed_street,
            full_line if full_line and "," not in full_line else "",
            old_b.get("street_number_name"),
            routing.get("resolved_address"),
        )

    city = _first_nonempty(
        addr.city if isinstance(getattr(addr, "city", None), (str, int, float)) else None,
        val_city,
        new_city,
        parsed_city,
        meta.get("city"),
        old_city,
    )

    state = _first_nonempty(
        addr.state if isinstance(getattr(addr, "state", None), (str, int, float)) else None,
        val_state,
        new_state,
        parsed_state,
        meta.get("state"),
        old_state,
    )
    state = _normalize_state(state)

    zip_code = _first_zip(
        addr.zip_code if isinstance(getattr(addr, "zip_code", None), (str, int, float)) else None,
        getattr(addr, "validated_postcode", None)
        if isinstance(getattr(addr, "validated_postcode", None), (str, int, float))
        else None,
        new_b.get("zip_postal_code"),
        parsed_zip,
        meta.get("zip"),
        meta.get("zip_code"),
        meta.get("postal"),
        old_b.get("zip_postal_code"),
    )

    latitude = _coerce_float(
        addr.latitude if isinstance(getattr(addr, "latitude", None), (int, float, str)) else None
    )
    longitude = _coerce_float(
        addr.longitude if isinstance(getattr(addr, "longitude", None), (int, float, str)) else None
    )
    if latitude is None or longitude is None:
        latitude = latitude or _coerce_float(getattr(addr, "validated_latitude", None))
        longitude = longitude or _coerce_float(getattr(addr, "validated_longitude", None))
    if latitude is None or longitude is None:
        latitude = latitude or _coerce_float(new_b.get("latitude"))
        longitude = longitude or _coerce_float(new_b.get("longitude"))
    if latitude is None or longitude is None:
        meta_lat, meta_lon = _coords_from_meta(meta)
        latitude = latitude or meta_lat
        longitude = longitude or meta_lon
    if latitude is None or longitude is None:
        latitude = latitude or _coerce_float(addr.source_latitude)
        longitude = longitude or _coerce_float(addr.source_longitude)

    country = _normalize_country(
        _first_nonempty(
            getattr(addr, "validated_country_code", None),
            new_b.get("country_code"),
            old_b.get("country_code"),
            meta.get("country_code"),
            meta.get("country"),
        )
    )
    if country == "US" and full_line:
        upper = full_line.upper()
        if upper.endswith(", USA") or upper.endswith(", US"):
            country = "US"

    sources: list[str] = []
    if getattr(addr, "validated_street_line", None) and isinstance(
        getattr(addr, "validated_street_line", None), str
    ):
        sources.append("validated_street_line")
    if new_b.get("street_number_name"):
        sources.append("raw_metadata.new")
    if parsed_street:
        sources.append("ADDRESS_parse")
    if addr.raw_address and "addresses.raw_address" not in sources:
        sources.append("addresses.raw_address")
    if new_b.get("city_state") or val_city:
        sources.append("city_state_bundle")
    elif parsed_city:
        sources.append("ADDRESS_city")

    return Agent1InputFields(
        raw_address=raw_address,
        city=city,
        state=state,
        zip_code=zip_code,
        country=country,
        latitude=latitude,
        longitude=longitude,
        sources=sources or ["fallback"],
    )

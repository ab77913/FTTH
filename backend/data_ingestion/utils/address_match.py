"""Region-agnostic address text matching for geocoder acceptance (Agent 2)."""
from __future__ import annotations

import os
import re

from rapidfuzz import fuzz

from data_ingestion.utils.strings import normalize_address_key
from data_ingestion.utils.res_com_addressing import (
    ENABLE_RES_COM_ADDRESSING,
    build_address_from_metadata,
    is_res_com_token,
    looks_like_real_address,
)

_ZIP_RE = re.compile(r"\b(\d{5})(?:-\d{4})?\b")
_HOUSE_ATTACHED_SUFFIX_RE = re.compile(r"^\s*(\d+(?:-\d+)?[A-Za-z])\b")
_HOUSE_SPACED_UNIT_RE = re.compile(r"^\s*(\d+(?:-\d+)?)\s+([A-Za-z])\b")
_HOUSE_PLAIN_RE = re.compile(r"^\s*(\d+(?:-\d+)?)\b")
_HOUSE_FALLBACK_RE = re.compile(r"\b(\d+(?:-\d+)?[A-Za-z]?)\b")
_STATE_RE = re.compile(r"\b([A-Z]{2})\b")
_ORDINAL_SUFFIX_RE = re.compile(r"\b(\d+)(?:st|nd|rd|th)\b", re.I)
# Single-letter tokens after the house number that are street directions, not unit suffixes.
_COMPASS_UNIT_LETTERS = frozenset("NSEW")

# Fuzzy token-set ratio at or above this → treat as 100% text match (env-tunable).
_FUZZY_MATCH_THRESHOLD = int(os.environ.get("FTTH_ADDRESS_MATCH_FUZZY_THRESHOLD", "92"))
# When coords agree within this distance (m) and house numbers match, allow a slightly lower fuzzy bar.
_GEO_BOOST_DISTANCE_M = float(os.environ.get("FTTH_ADDRESS_MATCH_GEO_BOOST_M", "50"))
_GEO_BOOST_FUZZY_MIN = int(os.environ.get("FTTH_ADDRESS_MATCH_GEO_BOOST_FUZZY_MIN", "85"))


def _normalize_match_text(text: str) -> str:
    """Language-neutral normalization: casefold, strip punctuation, collapse space."""
    t = (text or "").strip().casefold()
    t = _ORDINAL_SUFFIX_RE.sub(r"\1", t)
    t = re.sub(r"[^\w\s]", " ", t, flags=re.UNICODE)
    return re.sub(r"\s+", " ", t).strip()


def _fuzzy_token_score(left: str, right: str) -> int:
    if not (left or "").strip() or not (right or "").strip():
        return 0
    return int(round(fuzz.token_set_ratio(_normalize_match_text(left), _normalize_match_text(right))))


def _extract_zip(text: str) -> str | None:
    match = _ZIP_RE.search(text or "")
    return match.group(1) if match else None


def extract_house_number(text: str) -> str | None:
    """Leading house/building number, including unit suffixes (278A, 278 A, 49-5)."""
    if not text:
        return None
    stripped = text.strip()
    m = _HOUSE_ATTACHED_SUFFIX_RE.match(stripped)
    if m:
        return m.group(1).upper()
    m = _HOUSE_SPACED_UNIT_RE.match(stripped)
    if m:
        suffix = m.group(2).upper()
        if suffix not in _COMPASS_UNIT_LETTERS:
            return f"{m.group(1).upper()}{suffix}"
    m = _HOUSE_PLAIN_RE.match(stripped)
    if m:
        return m.group(1).upper()
    m = _HOUSE_FALLBACK_RE.search(stripped)
    return m.group(1).upper() if m else None


def _extract_house_number(text: str) -> str | None:
    return extract_house_number(text)


def _normalize_house_number(value: str | None) -> str:
    """Normalize for equality checks (4905 ≠ 49-5)."""
    if not value:
        return ""
    return re.sub(r"[\s-]+", "", str(value).strip().upper())


def house_numbers_match(upload_line: str, formatted: str, *, geo: dict | None = None) -> bool:
    """True when structured/text house numbers agree."""
    hn_in = _normalize_house_number(_extract_house_number(upload_line))
    hn_out = _normalize_house_number(
        (geo or {}).get("house_number") or _extract_house_number(formatted)
    )
    if hn_in and hn_out:
        return hn_in == hn_out
    return True


def _comparison_candidates(
    input_address: str,
    formatted_address: str,
    *,
    city: str | None = None,
    state: str | None = None,
    zip_code: str | None = None,
) -> list[str]:
    """Build several string pairs — fuzzy matching handles abbreviations in any language."""
    full_input = _build_input_line(
        input_address,
        city=city,
        state=state,
        zip_code=zip_code,
    )
    first_segment = formatted_address.split(",")[0].strip()
    two_segments = ",".join(formatted_address.split(",")[:2]).strip()
    return list(dict.fromkeys([
        full_input,
        input_address.strip(),
        formatted_address.strip(),
        first_segment,
        two_segments,
    ]))


def _house_numbers_compatible(input_address: str, formatted_address: str) -> bool:
    """False when both sides have a house/building number and they differ."""
    return house_numbers_match(input_address, formatted_address)


def looks_like_street_address(text: str | None) -> bool:
    """True when text resembles a street line (house number + name tokens)."""
    if not text or not str(text).strip():
        return False
    t = str(text).strip()
    if extract_house_number(t):
        remainder = _HOUSE_ATTACHED_SUFFIX_RE.sub("", t, count=1)
        remainder = _HOUSE_SPACED_UNIT_RE.sub("", remainder, count=1)
        remainder = _HOUSE_PLAIN_RE.sub("", remainder, count=1).strip()
        if remainder and len(remainder.split()) >= 1:
            return True
    tokens = _normalize_match_text(t).split()
    return len(tokens) >= 2


def _build_input_line(
    address: str,
    *,
    city: str | None = None,
    state: str | None = None,
    zip_code: str | None = None,
) -> str:
    parts = [address.strip()]
    city_state = ", ".join(filter(None, [(city or "").strip(), (state or "").strip()]))
    if city_state and city_state.lower() not in address.lower():
        parts.append(city_state)
    if zip_code and str(zip_code).strip() and str(zip_code).strip()[:5] not in address:
        parts.append(str(zip_code).strip()[:5])
    return ", ".join(filter(None, parts))


def strip_kml_category_prefix(text: str, meta: dict | None) -> str:
    """Strip ``Category: placemark`` display prefix from KML raw_address lines."""
    text = (text or "").strip()
    if not text:
        return ""
    meta = meta or {}
    category = str(meta.get("category") or "").strip()
    placemark = str(meta.get("placemark_name") or "").strip()
    if category and placemark and text.lower() == f"{category}: {placemark}".lower():
        return placemark
    if category and text.lower() == category.lower():
        return ""
    return text


def resolve_upload_address_line(
    raw_address: str | None,
    meta: dict | None = None,
) -> str:
    """
    Best upload street line from raw_address and KML metadata.

    KML household rows often store ``Households: 4910 NW 152ND LN`` in raw_address
    while the real address lives in placemark_name / Address metadata keys.
    """
    meta = meta or {}
    if ENABLE_RES_COM_ADDRESSING:
        resolved = build_address_from_metadata(meta, raw_address=raw_address)
        if looks_like_real_address(resolved):
            return resolved
        if is_res_com_token(raw_address):
            return ""

    candidates: list[str] = []
    for key in ("ADDRESS", "Address", "address", "placemark_name"):
        v = str(meta.get(key) or "").strip()
        if v:
            candidates.append(v)
    for raw in (raw_address,):
        v = str(raw or "").strip()
        if v:
            candidates.append(strip_kml_category_prefix(v, meta))
    routing = meta.get("_routing")
    if isinstance(routing, dict):
        v = str(routing.get("resolved_address") or "").strip()
        if v:
            candidates.append(strip_kml_category_prefix(v, meta))
    for c in candidates:
        if ENABLE_RES_COM_ADDRESSING and is_res_com_token(c):
            continue
        if looks_like_street_address(c):
            return c
    for c in candidates:
        if ENABLE_RES_COM_ADDRESSING and is_res_com_token(c):
            continue
        if c:
            return c
    return ""


def address_match_percent(
    input_address: str,
    formatted_address: str,
    *,
    city: str | None = None,
    state: str | None = None,
    zip_code: str | None = None,
    coord_distance_m: float | None = None,
    country: str | None = None,
) -> int:
    """
    Return 0–100 match score between upload text and a geocoder formatted line.

    Uses fuzzy token matching (rapidfuzz) so abbreviations and formatting differences
    work across regions without hardcoded suffix/direction lists. House numbers must
    agree when both sides have one. Optional coord_distance_m can boost near-pin matches.
    """
    del country  # reserved for future locale-specific rules / LLM context
    if not (input_address or "").strip() or not (formatted_address or "").strip():
        return 0

    if not _house_numbers_compatible(input_address, formatted_address):
        return min(_fuzzy_token_score(input_address, formatted_address), 40)

    full_input = _build_input_line(
        input_address,
        city=city,
        state=state,
        zip_code=zip_code,
    )
    in_key = normalize_address_key(full_input)
    out_key = normalize_address_key(formatted_address)
    if in_key and out_key and in_key == out_key:
        return 100

    candidates = _comparison_candidates(
        input_address,
        formatted_address,
        city=city,
        state=state,
        zip_code=zip_code,
    )
    fuzzy_scores = [
        _fuzzy_token_score(input_address, candidate)
        for candidate in candidates
        if candidate
    ]
    fuzzy_scores.append(_fuzzy_token_score(full_input, formatted_address))
    score = max(fuzzy_scores) if fuzzy_scores else 0

    # Component checks add confidence for structured fields when present.
    checks: list[bool] = []
    hn_in = _extract_house_number(input_address)
    hn_out = _extract_house_number(formatted_address)
    if hn_in:
        checks.append(bool(hn_out and hn_in == hn_out))

    zip_in = _extract_zip(full_input) or (str(zip_code).strip()[:5] if zip_code else None)
    zip_out = _extract_zip(formatted_address)
    if zip_in:
        checks.append(bool(zip_out and zip_in == zip_out))

    state_norm = (state or "").strip().upper()[:2]
    if state_norm and len(state_norm) == 2:
        out_state = _STATE_RE.search(formatted_address.upper())
        checks.append(bool(out_state and out_state.group(1) == state_norm))

    if city and city.strip():
        city_norm = _normalize_match_text(city)
        fmt_norm = _normalize_match_text(formatted_address)
        checks.append(bool(city_norm and city_norm in fmt_norm.split()))

    if checks and all(checks) and score >= _GEO_BOOST_FUZZY_MIN:
        score = max(score, _FUZZY_MATCH_THRESHOLD)

    if (
        coord_distance_m is not None
        and coord_distance_m <= _GEO_BOOST_DISTANCE_M
        and hn_in
        and hn_out
        and hn_in == hn_out
        and score >= _GEO_BOOST_FUZZY_MIN
    ):
        score = max(score, _FUZZY_MATCH_THRESHOLD)

    if score >= _FUZZY_MATCH_THRESHOLD:
        return 100

    return int(max(0, min(score, 99)))


def address_matches_exact(
    input_address: str,
    formatted_address: str,
    *,
    city: str | None = None,
    state: str | None = None,
    zip_code: str | None = None,
    coord_distance_m: float | None = None,
    country: str | None = None,
) -> bool:
    required = int(os.environ.get("FTTH_AGENT2_ADDRESS_MATCH_REQUIRED", "100"))
    return address_match_percent(
        input_address,
        formatted_address,
        city=city,
        state=state,
        zip_code=zip_code,
        coord_distance_m=coord_distance_m,
        country=country,
    ) >= required


def address_match_percent_for_geo(
    upload_line: str,
    geo: dict | None,
    *,
    city: str | None = None,
    state: str | None = None,
    zip_code: str | None = None,
    coord_distance_m: float | None = None,
) -> int:
    """Match upload text against one geocoder result dict (reverse or forward)."""
    if not upload_line.strip() or not geo:
        return 0 if upload_line.strip() else 100
    formatted = (
        str(geo.get("formatted_address") or geo.get("display_name") or "").strip()
    )
    if not formatted:
        return 0
    pct = address_match_percent(
        upload_line,
        formatted,
        city=city,
        state=state,
        zip_code=zip_code,
        coord_distance_m=coord_distance_m,
    )
    if not house_numbers_match(upload_line, formatted, geo=geo):
        pct = min(pct, 40)
    return pct


def finalize_geocoder_confidence(
    raw_score: float | int,
    *,
    location_type: str,
    address_match_percent: int | None,
    match_required: int | None = None,
    low_cap: float | None = None,
) -> float:
    """
    High confidence only when location_type is ROOFTOP and text match meets required %.

    Used for reverse geocoding, Google forward, OSM, and interpolation in Agent 2.
    """
    required = (
        match_required
        if match_required is not None
        else int(os.environ.get("FTTH_AGENT2_ADDRESS_MATCH_REQUIRED", "100"))
    )
    cap = (
        low_cap
        if low_cap is not None
        else float(os.environ.get("FTTH_AGENT2_LOW_MATCH_CONFIDENCE", "35"))
    )
    loc = (location_type or "").strip().upper()
    pct = int(address_match_percent or 0)
    score = float(raw_score)
    if loc == "ROOFTOP" and pct >= required:
        return score
    return min(score, cap)

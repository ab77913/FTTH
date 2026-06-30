from __future__ import annotations

import re
from typing import Any

from data_ingestion.schemas import CanonicalAddressRecord
from data_ingestion.utils.strings import clean_value, normalize_duplicate_address_key, normalize_header

CSV_ADDRESS_SOURCES = (
    "secondary_number",
    "secondary number",
    "address",
    "full_address",
    "full address",
    "service_address",
    "service address",
    "street_address",
    "street address",
    "site_address",
    "site address",
)
KMZ_ADDRESS_SOURCES = CSV_ADDRESS_SOURCES

MERGE_KMZ_CATEGORIES = frozenset({"household", "households", "address", "addresses"})


def is_csv_kmz_merge_geospatial_record(record: CanonicalAddressRecord) -> bool:
    meta = record.raw_metadata or {}
    labels: list[str] = []
    category = str(meta.get("category") or "").strip().lower()
    if category:
        labels.append(category)
    folder_path = str(meta.get("folder_path") or "")
    labels.extend(part.strip().lower() for part in folder_path.split("/") if part.strip())
    return any(label in MERGE_KMZ_CATEGORIES for label in labels)


def _normalize_compare_value(value: Any) -> str:
    text = clean_value(value)
    if text is None:
        return ""
    normalized = str(text).strip().upper()
    if normalized.endswith(".0") and normalized[:-2].isdigit():
        normalized = normalized[:-2]
    return re.sub(r"\s+", " ", normalized).strip()


def _merge_key(value: Any) -> str:
    compare = _normalize_compare_value(value)
    if not compare:
        return ""
    return normalize_duplicate_address_key(compare) or ""


def _lookup_raw_value(raw: dict[str, Any], names: tuple[str, ...]) -> Any:
    if not raw:
        return None
    normalized = {normalize_header(key): value for key, value in raw.items()}
    for name in names:
        norm_name = normalize_header(name)
        if norm_name in normalized:
            return normalized[norm_name]
    return None


def extract_csv_secondary_number(record: CanonicalAddressRecord) -> str:
    raw = record.raw_metadata or {}
    for name in CSV_ADDRESS_SOURCES:
        value = _lookup_raw_value(raw, (name,))
        if value:
            key = _merge_key(value)
            if key:
                return key
    return ""


def extract_kmz_address(record: CanonicalAddressRecord) -> str:
    raw = record.raw_metadata or {}
    address_value = _lookup_raw_value(raw, KMZ_ADDRESS_SOURCES)
    return _merge_key(address_value)


def addresses_fully_match(csv_record: CanonicalAddressRecord, kmz_record: CanonicalAddressRecord) -> bool:
    csv_key = extract_csv_secondary_number(csv_record)
    kmz_key = extract_kmz_address(kmz_record)
    return bool(csv_key and kmz_key and csv_key == kmz_key)


def is_geospatial_record(record: CanonicalAddressRecord) -> bool:
    return (record.raw_metadata or {}).get("file_role") == "geospatial"


def is_tabular_record(record: CanonicalAddressRecord) -> bool:
    return (record.raw_metadata or {}).get("file_role") == "tabular"


def apply_csv_kmz_merge(records: list[CanonicalAddressRecord]) -> None:
    """Merge CSV/KMZ rows when tabular and geospatial address keys match."""
    tabular = [rec for rec in records if is_tabular_record(rec)]
    household_kmz = [
        rec
        for rec in records
        if is_geospatial_record(rec)
        and is_csv_kmz_merge_geospatial_record(rec)
    ]
    has_household_kmz_upload = bool(household_kmz)

    kmz_by_address: dict[str, list[CanonicalAddressRecord]] = {}
    for rec in household_kmz:
        key = extract_kmz_address(rec)
        if key:
            kmz_by_address.setdefault(key, []).append(rec)

    matched_kmz_ids: set[int] = set()

    for csv_rec in tabular:
        csv_address = extract_csv_secondary_number(csv_rec)
        candidates = kmz_by_address.get(csv_address) or []
        match = next((rec for rec in candidates if id(rec) not in matched_kmz_ids), None)

        meta = dict(csv_rec.raw_metadata or {})
        if match is None:
            if not has_household_kmz_upload:
                continue
            meta.update({
                "merge_status": "invalid",
                "merge_color": "red",
                "merge_reason": "No KMZ match on address / secondary_number",
                "merge_match_type": "",
                "address_source": "csv",
                "merge_csv_secondary_number": csv_address,
                "merge_classification": {
                    "status": "invalid",
                    "color": "red",
                    "reason": "No KMZ match on address / secondary_number",
                    "match_type": "",
                    "csv_secondary_number": csv_address,
                },
            })
            csv_rec.raw_metadata = meta
            continue

        matched_kmz_ids.add(id(match))
        kmz_address = extract_kmz_address(match)
        if match.latitude is not None and match.longitude is not None:
            csv_rec.latitude = match.latitude
            csv_rec.longitude = match.longitude
        if match.city:
            csv_rec.city = match.city
        if match.state:
            csv_rec.state = match.state
        if match.zip_code:
            csv_rec.zip_code = match.zip_code
        if match.address_id and not csv_rec.address_id:
            csv_rec.address_id = match.address_id

        meta.update({
            "merge_status": "verified",
            "merge_color": "green",
            "merge_reason": "Merged with KMZ after address match",
            "merge_match_type": "exact_address",
            "address_source": "csv",
            "address_source_detail": "csv_and_kmz",
            "merge_matched_source_file": match.source_file,
            "merge_csv_secondary_number": csv_address,
            "merge_kmz_address": kmz_address,
            "coordinates_source": "kmz",
            "coordinates_filled_from_source_file": match.source_file,
            "coordinates_filled_from_address": match.raw_address,
            "coordinates_filled_reason": "CSV address matched KMZ Address",
            "merge_classification": {
                "status": "verified",
                "color": "green",
                "reason": "Merged with KMZ after address match",
                "match_type": "exact_address",
                "csv_secondary_number": csv_address,
                "kmz_address": kmz_address,
                "matched_source_file": match.source_file,
            },
        })
        csv_rec.raw_metadata = meta

        match_meta = dict(match.raw_metadata or {})
        match_meta.update({
            "merge_absorbed_into_csv": True,
            "merge_status": "merged",
            "merge_color": "green",
            "merge_reason": "Absorbed into matching CSV row",
            "merge_match_type": "exact_address",
            "address_source": "kmz",
            "merge_csv_secondary_number": csv_address,
            "merge_kmz_address": kmz_address,
            "merge_classification": {
                "status": "merged",
                "color": "green",
                "reason": "Absorbed into matching CSV row",
                "match_type": "exact_address",
                "csv_secondary_number": csv_address,
                "kmz_address": kmz_address,
            },
        })
        match.raw_metadata = match_meta

    for kmz_rec in household_kmz:
        if id(kmz_rec) in matched_kmz_ids:
            continue
        kmz_address = extract_kmz_address(kmz_rec)
        meta = dict(kmz_rec.raw_metadata or {})
        meta.update({
            "merge_absorbed_into_csv": False,
            "merge_status": "verified",
            "merge_color": "green",
            "merge_reason": "Uploaded KMZ address present in source data",
            "merge_match_type": "",
            "address_source": "kmz",
            "merge_kmz_address": kmz_address,
            "merge_classification": {
                "status": "verified",
                "color": "green",
                "reason": "Uploaded KMZ address present in source data",
                "match_type": "",
                "kmz_address": kmz_address,
            },
        })
        kmz_rec.raw_metadata = meta


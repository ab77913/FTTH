from __future__ import annotations

from dataclasses import dataclass, field

from data_ingestion.schemas import CanonicalAddressRecord
from data_ingestion.utils.strings import normalize_duplicate_address_key


@dataclass
class ValidationSummary:
    valid_records: list[CanonicalAddressRecord] = field(default_factory=list)
    invalid_records: list[CanonicalAddressRecord] = field(default_factory=list)
    duplicate_records: list[CanonicalAddressRecord] = field(default_factory=list)

    @property
    def valid_count(self) -> int:
        return len(self.valid_records)

    @property
    def invalid_count(self) -> int:
        return len(self.invalid_records)

    @property
    def duplicate_count(self) -> int:
        return len(self.duplicate_records)


def validate_record(record: CanonicalAddressRecord) -> CanonicalAddressRecord:
    """Apply POC validation rules.

    This intentionally avoids provider-specific USPS/Smarty/Melissa checks.
    """
    record.validation_errors.clear()
    record.validation_warnings.clear()

    if not record.raw_address:
        record.validation_errors.append("missing_raw_address")

    if record.latitude is None or record.longitude is None:
        record.validation_warnings.append("missing_coordinates")
    else:
        if not -90 <= record.latitude <= 90:
            record.validation_errors.append("invalid_latitude")
        if not -180 <= record.longitude <= 180:
            record.validation_errors.append("invalid_longitude")

    if not record.normalized_key and record.raw_address:
        record.validation_warnings.append("missing_normalized_key")

    return record


def validate_and_deduplicate(records: list[CanonicalAddressRecord]) -> ValidationSummary:
    summary = ValidationSummary()
    canonical_by_key: dict[str, CanonicalAddressRecord] = {}

    for record in records:
        record = validate_record(record)
        if record.validation_errors:
            meta = dict(record.raw_metadata or {})
            if "missing_raw_address" in record.validation_errors:
                meta.setdefault("record_status", "ADDRESS_MISSING")
                meta.setdefault("merge_status", "address_missing")
                meta.setdefault("merge_reason", "Address missing in uploaded raw data")
            record.raw_metadata = meta
            summary.invalid_records.append(record)
            continue

        duplicate_key = normalize_duplicate_address_key(record.raw_address)
        if duplicate_key and duplicate_key in canonical_by_key:
            canonical = canonical_by_key[duplicate_key]
            record.validation_warnings.append("duplicate_in_job")
            meta = dict(record.raw_metadata or {})
            meta.update({
                "record_status": "DUPLICATE",
                "canonical_record_id": str(canonical.record_id),
                "canonical_source_file": canonical.source_file,
                "canonical_source_row_number": canonical.source_row_number,
                "canonical_raw_address": canonical.raw_address,
                "duplicate_reason": "Normalized address match",
                "duplicate_normalized_address": duplicate_key,
                "merge_status": "duplicate",
                "merge_color": "white",
                "merge_reason": "Duplicate address in uploaded raw data",
                "merge_match_type": "normalized_address",
            })
            record.raw_metadata = meta
            summary.duplicate_records.append(record)
            continue

        if duplicate_key:
            canonical_by_key[duplicate_key] = record
        meta = dict(record.raw_metadata or {})
        meta.setdefault("record_status", "UNIQUE")
        record.raw_metadata = meta
        summary.valid_records.append(record)

    return summary



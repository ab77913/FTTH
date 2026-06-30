"""Tests for CSV secondary_number vs KMZ Address merge rules."""

from __future__ import annotations

from data_ingestion.schemas import CanonicalAddressRecord
from data_ingestion.utils.csv_kmz_merge import (
    addresses_fully_match,
    apply_csv_kmz_merge,
    extract_csv_secondary_number,
    extract_kmz_address,
)
from data_ingestion.utils.kml_categories import is_transient_kmz_record, records_for_storage


def _csv(**fields: str) -> CanonicalAddressRecord:
    return CanonicalAddressRecord(
        source_file="addresses.csv",
        raw_address=fields.get("secondary_number", ""),
        city=fields.get("city"),
        zip_code=fields.get("zip"),
        raw_metadata={
            "file_role": "tabular",
            "secondary_number": fields.get("secondary_number", ""),
            "city": fields.get("city", ""),
            "zip": fields.get("zip", ""),
        },
    )


def _kmz(**fields: str) -> CanonicalAddressRecord:
    address = fields.get("Address", fields.get("address", ""))
    return CanonicalAddressRecord(
        source_file="network.kmz",
        raw_address=f"Households: {address}",
        city=fields.get("City", fields.get("city")),
        zip_code=fields.get("ZIP", fields.get("zip")),
        latitude=29.45,
        longitude=-82.22,
        raw_metadata={
            "file_role": "geospatial",
            "category": "Households",
            "folder_path": "Comsof Design/Households",
            "Address": address,
            "City": fields.get("City", fields.get("city", "")),
            "ZIP": fields.get("ZIP", fields.get("zip", "")),
        },
    )


def test_address_match_is_case_insensitive() -> None:
    csv_rec = _csv(secondary_number="6155 Avenue F")
    kmz_rec = _kmz(Address="6155 AVENUE F")
    assert extract_csv_secondary_number(csv_rec) == extract_kmz_address(kmz_rec)
    assert addresses_fully_match(csv_rec, kmz_rec)


def test_address_match_standardizes_street_suffixes() -> None:
    csv_rec = _csv(secondary_number="4 Ferguson Street")
    kmz_rec = _kmz(Address="4 FERGUSON ST")
    assert extract_csv_secondary_number(csv_rec) == extract_kmz_address(kmz_rec)
    assert addresses_fully_match(csv_rec, kmz_rec) is True


def test_exact_match_merges_and_drops_kmz_row_from_storage() -> None:
    csv_rec = _csv(secondary_number="6155 AVENUE F", city="MC INTOSH", zip="32643")
    kmz_rec = _kmz(Address="6155 AVENUE F", City="REDDICK", ZIP="")
    records = [csv_rec, kmz_rec]

    apply_csv_kmz_merge(records)

    assert csv_rec.raw_metadata["merge_status"] == "verified"
    assert csv_rec.raw_metadata["address_source"] == "csv"
    assert csv_rec.latitude == 29.45
    assert kmz_rec.raw_metadata["merge_absorbed_into_csv"] is True
    assert is_transient_kmz_record(kmz_rec)
    assert records_for_storage(records) == [csv_rec]


def test_non_match_keeps_separate_records() -> None:
    csv_rec = _csv(secondary_number="6155 AVENUE F")
    kmz_rec = _kmz(Address="6202 AVENUE G")
    records = [csv_rec, kmz_rec]

    apply_csv_kmz_merge(records)

    assert csv_rec.raw_metadata["merge_status"] == "invalid"
    assert kmz_rec.raw_metadata["merge_status"] == "verified"
    assert kmz_rec.raw_metadata["address_source"] == "kmz"
    assert kmz_rec.raw_metadata["merge_color"] == "green"
    assert kmz_rec.raw_metadata["merge_absorbed_into_csv"] is False
    assert records_for_storage(records) == records


def test_service_address_column_is_used_for_csv_kmz_matching() -> None:
    csv_rec = CanonicalAddressRecord(
        source_file="addresses.csv",
        raw_address="6155 AVENUE F",
        raw_metadata={"file_role": "tabular", "service_address": "6155 AVENUE F"},
    )
    kmz_rec = _kmz(Address="6155 AVENUE F")
    records = [csv_rec, kmz_rec]

    apply_csv_kmz_merge(records)

    assert addresses_fully_match(csv_rec, kmz_rec) is True
    assert csv_rec.raw_metadata["merge_status"] == "verified"
    assert kmz_rec.raw_metadata["merge_absorbed_into_csv"] is True


def test_kmz_placemark_name_is_not_used_for_csv_kmz_matching() -> None:
    csv_rec = _csv(secondary_number="6155 AVENUE F")
    kmz_rec = CanonicalAddressRecord(
        source_file="network.kmz",
        raw_address="Households: 6155 AVENUE F",
        latitude=29.45,
        longitude=-82.22,
        raw_metadata={
            "file_role": "geospatial",
            "category": "Households",
            "folder_path": "Comsof Design/Households",
            "placemark_name": "6155 AVENUE F",
        },
    )
    records = [csv_rec, kmz_rec]

    apply_csv_kmz_merge(records)

    assert addresses_fully_match(csv_rec, kmz_rec) is False
    assert csv_rec.raw_metadata["merge_status"] == "invalid"
    assert kmz_rec.raw_metadata["merge_status"] == "verified"
    assert kmz_rec.raw_metadata["merge_kmz_address"] == ""


def test_non_household_kmz_is_not_used_for_csv_kmz_matching() -> None:
    csv_rec = _csv(secondary_number="6155 AVENUE F")
    kmz_rec = CanonicalAddressRecord(
        source_file="network.kmz",
        raw_address="Fiber: 6155 AVENUE F",
        raw_metadata={
            "file_role": "geospatial",
            "category": "Fiber Routes",
            "folder_path": "Comsof Design/Fiber Routes",
            "Address": "6155 AVENUE F",
        },
    )
    records = [csv_rec, kmz_rec]

    apply_csv_kmz_merge(records)

    assert "merge_status" not in csv_rec.raw_metadata
    assert "merge_status" not in kmz_rec.raw_metadata

def test_premise_kmz_category_is_not_used_for_csv_kmz_matching() -> None:
    csv_rec = _csv(secondary_number="6155 AVENUE F")
    kmz_rec = CanonicalAddressRecord(
        source_file="network.kmz",
        raw_address="Premises: 6155 AVENUE F",
        raw_metadata={
            "file_role": "geospatial",
            "category": "Premises",
            "folder_path": "Comsof Design/Premises",
            "Address": "6155 AVENUE F",
        },
    )
    records = [csv_rec, kmz_rec]

    apply_csv_kmz_merge(records)

    assert "merge_status" not in csv_rec.raw_metadata
    assert "merge_status" not in kmz_rec.raw_metadata


def test_grouped_merge_keeps_absorbing_csv_row_unique() -> None:
    from data_ingestion.ingestion_service import IngestionService

    csv_rec = _csv(secondary_number="4 FERGUSON ST")
    kmz_rec = _kmz(Address="4 FERGUSON ST")
    records = [kmz_rec, csv_rec]

    service = IngestionService.__new__(IngestionService)
    service._annotate_grouped_merge(records, batch_id="batch", file_summaries=[])
    stored = records_for_storage(records)

    assert stored == [csv_rec]
    assert csv_rec.raw_metadata["record_status"] == "UNIQUE"
    assert csv_rec.raw_metadata["address_source"] == "csv"
    assert csv_rec.raw_metadata["merge_status"] == "verified"
    assert kmz_rec.raw_metadata["merge_absorbed_into_csv"] is True
    assert kmz_rec.raw_metadata.get("record_status") != "DUPLICATE"

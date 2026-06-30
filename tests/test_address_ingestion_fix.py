"""Regression tests for bare KML/KMZ point address extraction."""

from __future__ import annotations

from pathlib import Path

from data_ingestion.extractors.kml_extractor import KMLExtractor
from data_ingestion.parsers.canonical_mapper import CanonicalMapper


def test_sample_kml_without_household_or_address_folder_is_ignored() -> None:
    content = Path("examples/sample.kml").read_bytes()
    records = KMLExtractor().extract_from_bytes(content, source_file="sample.kml")
    assert records == []


def test_sample_addresses_csv_maps_address_column() -> None:
    from data_ingestion.extractors.csv_extractor import CSVExtractor

    path = Path("examples/sample_addresses.csv")
    raw_records = CSVExtractor().extract(path)
    mapped = CanonicalMapper().map_records(raw_records)
    assert len(mapped) == 5
    assert mapped[0].raw_address == "1603 LAFAYETTE ST"


def test_csv_kmz_merge_normalizes_avenue_abbreviation() -> None:
    from data_ingestion.schemas import CanonicalAddressRecord
    from data_ingestion.utils.csv_kmz_merge import addresses_fully_match

    csv_rec = CanonicalAddressRecord(
        source_file="data.csv",
        raw_address="6155 AVENUE F",
        raw_metadata={"file_role": "tabular", "Address": "6155 AVENUE F"},
    )
    kmz_rec = CanonicalAddressRecord(
        source_file="design.kmz",
        raw_address="6155 AVE F",
        raw_metadata={
            "file_role": "geospatial",
            "category": "Households",
            "Address": "6155 AVE F",
        },
    )
    assert addresses_fully_match(csv_rec, kmz_rec)

def test_secondary_number_maps_to_raw_address() -> None:
    from data_ingestion.schemas import RawExtractedRecord

    raw = RawExtractedRecord(
        source_file="addresses.csv",
        row_number=1,
        raw_data={"secondary_number": "201 N 1ST ST", "city": "IMPERIAL", "state": "TX"},
    )
    mapped = CanonicalMapper().map_record(raw)
    assert mapped.raw_address == "201 N 1ST ST"
    assert mapped.city == "IMPERIAL"
    assert mapped.state == "TX"

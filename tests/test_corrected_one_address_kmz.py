"""Regression: KMZ with one address point and one polygon layer."""

from __future__ import annotations

from pathlib import Path

from data_ingestion.extractors.kml_extractor import KMLExtractor
from data_ingestion.parsers.canonical_mapper import CanonicalMapper
from data_ingestion.utils.kml_categories import is_household_kml_record, records_for_storage
from data_ingestion.validators import validate_and_deduplicate

_FIXTURE = Path("tests/fixtures/corrected_one_address_with_polygon.kmz")


def test_corrected_one_address_kmz_extracts_point_and_polygon() -> None:
    assert _FIXTURE.is_file(), f"Missing fixture: {_FIXTURE}"
    raw_records = KMLExtractor().extract(_FIXTURE)
    assert len(raw_records) == 2

    by_geom = {rec.raw_data["geometry_type"]: rec for rec in raw_records}
    assert "Polygon" in by_geom
    assert "Point" in by_geom

    polygon = by_geom["Polygon"]
    point = by_geom["Point"]
    assert polygon.raw_data["map_layer_only"] is True
    assert point.raw_data["map_layer_only"] is False
    assert point.raw_data["raw_address"] == "15345 NW 42ND TER"
    assert point.raw_data["latitude"] == 29.37154400000312
    assert point.raw_data["longitude"] == -82.19611400000174
    assert is_household_kml_record(category=point.raw_data["category"])


def test_corrected_one_address_kmz_maps_and_validates_both_rows() -> None:
    raw_records = KMLExtractor().extract(_FIXTURE)
    mapped = CanonicalMapper().map_records(raw_records)
    stored = records_for_storage(mapped)
    summary = validate_and_deduplicate(stored)

    assert summary.valid_count == 2
    assert summary.invalid_count == 0
    addresses = [rec.raw_address for rec in summary.valid_records]
    assert "15345 NW 42ND TER" in addresses
    assert "6CA3 Polygon" in addresses

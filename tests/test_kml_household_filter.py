"""Tests for KML/KMZ household category filtering."""

from __future__ import annotations

from data_ingestion.extractors.kml_extractor import KMLExtractor
from data_ingestion.schemas import CanonicalAddressRecord
from data_ingestion.utils.kml_categories import (
    is_household_kml_record,
    is_transient_kmz_record,
    records_for_storage,
)

_SAMPLE_KML = b"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <Folder>
      <name>HOUSEHOLD</name>
      <Placemark>
        <name>1603 LAFAYETTE ST</name>
        <ExtendedData>
          <Data name="City"><value>Americus</value></Data>
          <Data name="State"><value>GA</value></Data>
          <Data name="ZIP"><value>31709</value></Data>
        </ExtendedData>
        <Point><coordinates>-84.241345,32.086591,0</coordinates></Point>
      </Placemark>
      <Placemark>
        <name>Household Boundary</name>
        <Polygon><outerBoundaryIs><LinearRing><coordinates>-84.24,32.08,0 -84.24,32.09,0 -84.25,32.09,0 -84.24,32.08,0</coordinates></LinearRing></outerBoundaryIs></Polygon>
      </Placemark>
    </Folder>
    <Folder>
      <name>FIBER ROUTES</name>
      <Placemark>
        <name>Route A</name>
        <LineString><coordinates>-84.24,32.08,0 -84.25,32.09,0</coordinates></LineString>
      </Placemark>
    </Folder>
    <Folder>
      <name>NODES</name>
      <Placemark>
        <name>706 KINGS WAY</name>
        <Point><coordinates>-84.240000,32.087000,0</coordinates></Point>
      </Placemark>
    </Folder>
    <Folder>
      <name>PREMISES</name>
      <Placemark>
        <name>999 EXTRA RD</name>
        <Point><coordinates>-84.260000,32.097000,0</coordinates></Point>
      </Placemark>
    </Folder>
  </Document>
</kml>
"""


def test_is_household_kml_record_matches_category_and_path() -> None:
    assert is_household_kml_record(category="HOUSEHOLD")
    assert is_household_kml_record(category="Households")
    assert is_household_kml_record(folder_path="Network Design/HOUSEHOLD")
    assert not is_household_kml_record(category="FIBER ROUTES")
    assert not is_household_kml_record(category="", folder_path="")


def test_kml_extractor_keeps_only_household_address_points_and_polygons() -> None:
    records = KMLExtractor().extract_from_bytes(_SAMPLE_KML, source_file="sample.kmz")
    assert len(records) == 2

    point = next(rec for rec in records if rec.raw_data["geometry_type"] == "Point")
    polygon = next(rec for rec in records if rec.raw_data["geometry_type"] == "Polygon")

    assert point.raw_data == {
        "Address": "1603 LAFAYETTE ST",
        "City": "Americus",
        "State": "GA",
        "ZIP": "31709",
        "category": "HOUSEHOLD",
        "folder_path": "HOUSEHOLD",
        "geometry_type": "Point",
        "latitude": 32.086591,
        "longitude": -84.241345,
        "source_format": "kml",
        "map_layer_only": False,
        "raw_address": "1603 LAFAYETTE ST",
    }
    assert polygon.raw_data["category"] == "HOUSEHOLD"
    assert polygon.raw_data["geometry_type"] == "Polygon"
    assert polygon.raw_data["map_layer_only"] is True
    assert polygon.raw_data["coordinates"]


def test_kmz_records_are_not_persisted_when_merged() -> None:
    merged_kmz = CanonicalAddressRecord(
        source_file="network.kmz",
        raw_address="HOUSEHOLD: 1603 LAFAYETTE ST",
        raw_metadata={
            "file_role": "geospatial",
            "source_format": "kmz",
            "category": "HOUSEHOLD",
            "merge_absorbed_into_csv": True,
        },
    )
    separate_kmz = CanonicalAddressRecord(
        source_file="network.kmz",
        raw_address="HOUSEHOLD: 999 UNKNOWN ST",
        raw_metadata={
            "file_role": "geospatial",
            "source_format": "kmz",
            "category": "HOUSEHOLD",
            "merge_absorbed_into_csv": False,
        },
    )
    csv_record = CanonicalAddressRecord(
        source_file="addresses.csv",
        raw_address="1603 LAFAYETTE ST",
        raw_metadata={"file_role": "tabular", "source_format": "csv"},
    )

    assert is_transient_kmz_record(merged_kmz)
    assert not is_transient_kmz_record(separate_kmz)
    assert not is_transient_kmz_record(csv_record)
    assert records_for_storage([merged_kmz, separate_kmz, csv_record]) == [separate_kmz, csv_record]

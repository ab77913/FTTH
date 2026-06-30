from types import SimpleNamespace

from api_server import (
    FINAL_FLOW_SCHEMA,
    FRONTEND_EXPORT_COLUMNS,
    _FINAL_OUTPUT_EXPORT_SQL,
    _build_frontend_export_row,
    _ui_cell_value,
    _ui_column_header,
    _ui_table_columns,
    _address_source_label,
    _filter_known_outside_polygon_records,
    _point_in_polygon_ring,
    _uploaded_input_fields,
)


def test_final_output_export_filter_excludes_empty_rows():
    sql = _FINAL_OUTPUT_EXPORT_SQL

    assert "raw_address" in sql
    assert "latitude IS NOT NULL" in sql
    assert "longitude IS NOT NULL" in sql
    assert "NULLIF(BTRIM(COALESCE(raw_address, '')), '') IS NOT NULL" in sql


def test_final_output_export_filter_keeps_household_map_rules():
    sql = _FINAL_OUTPUT_EXPORT_SQL

    assert "map_layer_only" in sql
    assert "geometry_type" in sql
    assert "household" in sql


def test_uploaded_input_fields_uses_explicit_snapshot_and_order():
    meta = {
        "_uploaded_data": {"Customer ID": "A-1", "Latitude": "35.1"},
        "_uploaded_columns": ["Latitude", "Customer ID"],
        "final_address": "AI address",
    }

    assert list(_uploaded_input_fields(meta).items()) == [
        ("Latitude", "35.1"),
        ("Customer ID", "A-1"),
    ]


def test_uploaded_input_fields_uses_live_comment_when_snapshot_is_stale():
    meta = {
        "_uploaded_data": {"Address": "1 Main St", "Comments": "old comment"},
        "_uploaded_columns": ["Address", "Comments"],
        "Comments": "edited in UI",
    }

    assert list(_uploaded_input_fields(meta).items()) == [
        ("Address", "1 Main St"),
        ("Comments", "edited in UI"),
    ]

def test_uploaded_input_fields_legacy_stops_before_pipeline_metadata():
    meta = {
        "Address": "1 Main St",
        "Custom Field": "keep me",
        "upload_batch_id": "batch-1",
        "final_address": "AI address",
        "agent4_confidence": 92,
    }

    assert _uploaded_input_fields(meta) == {
        "Address": "1 Main St",
        "Custom Field": "keep me",
    }


def _export_address(**overrides):
    values = {
        "id": 1,
        "raw_address": "1 Main St",
        "city": "",
        "state": "",
        "zip_code": "",
        "latitude": 35.1,
        "longitude": -80.1,
        "source_file": "input.csv",
        "source_row_number": 2,
        "coord_address_match_status": "MATCH",
        "coord_address_distance_m": 0,
        "coord_address_validation_notes": "saved UI comment",
        "reverse_geocode_confidence_score": 100,
        "validated_raw_address": None,
        "validated_latitude": None,
        "validated_longitude": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_frontend_export_includes_saved_ui_comments():
    row = _build_frontend_export_row(_export_address(), meta={})

    assert "coord_address_validation_notes" in FRONTEND_EXPORT_COLUMNS
    assert row["coord_address_validation_notes"] == "saved UI comment"


def test_frontend_export_prefers_live_metadata_comments():
    row = _build_frontend_export_row(
        _export_address(coord_address_validation_notes="old comment"),
        meta={"Comments": "edited in UI"},
    )

    assert row["coord_address_validation_notes"] == "edited in UI"


def test_frontend_export_accepts_singular_comment_metadata():
    row = _build_frontend_export_row(
        _export_address(coord_address_validation_notes="old comment"),
        meta={"comment": "singular UI comment"},
    )

    assert row["coord_address_validation_notes"] == "singular UI comment"


def test_final_flow_schema_exports_comments_column():
    assert ("coord_address_validation_notes", "Comments") in FINAL_FLOW_SCHEMA


def test_frontend_export_uses_ui_match_status_display_logic():
    row = _build_frontend_export_row(
        _export_address(
            raw_address="123 Main St",
            coord_address_match_status="MISMATCH_WARN",
            reverse_geocode_confidence_score=95,
        ),
        meta={
            "validated_raw_address": "123 Main Street",
            "address_validation": {"match_status": "MISMATCH_WARN", "confidence_score": 95},
        },
    )

    assert row["coord_address_match_status"] == "MATCH"


def test_ui_export_columns_follow_table_order_and_headers():
    columns = [
        {"key": "agent0_status", "label": "Status", "source": "agent0"},
        {"key": "longitude", "label": "Longitude", "source": "raw"},
        {"key": "raw_address", "label": "Raw Address", "source": "raw"},
    ]

    ordered = _ui_table_columns(columns)

    assert [c["key"] for c in ordered] == ["raw_address", "longitude", "agent0_status"]
    assert _ui_column_header(ordered[0]) == "Raw Address"
    assert _ui_column_header(ordered[2]) == "A0 Status"


def test_ui_export_cell_values_match_table_source_rules():
    record = {
        "raw_address": "top-level address",
        "latitude": None,
        "raw_data": {
            "raw_address": "metadata address",
            "latitude": 31.25,
            "agent0_status": "MATCH",
            "coord_address_match_status": "MATCH",
        },
    }

    assert _ui_cell_value(record, {"key": "raw_address", "source": "raw"}) == "top-level address"
    assert _ui_cell_value(record, {"key": "latitude", "source": "raw"}) == 31.25
    assert _ui_cell_value(record, {"key": "agent0_status", "source": "agent0"}) == "MATCH"
    assert _ui_cell_value(record, {"key": "coord_address_match_status", "source": "agent1"}) == "MATCH"


def test_ui_export_columns_respect_flow_subcheck_visibility():
    columns = [
        {"key": "raw_address", "label": "Address", "source": "raw"},
        {"key": "coord_address_match_status", "label": "A1: Match Status", "source": "agent1"},
        {"key": "agent2_formatted_address", "label": "Formatted Address", "source": "agent2"},
    ]
    flow_config = {
        "agents": [
            {
                "agent_name": "agent2_geocoding",
                "enabled": True,
                "pipeline_options": {
                    "coord_validation": False,
                    "reverse_geocoder": False,
                    "google_geocoding": True,
                    "osm_geocoding": False,
                    "street_interpolation": False,
                },
            }
        ]
    }

    ordered = _ui_table_columns(columns, flow_config)

    assert [c["key"] for c in ordered] == ["raw_address", "agent2_formatted_address"]

def test_point_in_polygon_ring_includes_boundary_and_excludes_outside():
    ring = [[-80.0, 35.0], [-79.0, 35.0], [-79.0, 36.0], [-80.0, 36.0], [-80.0, 35.0]]

    assert _point_in_polygon_ring(35.5, -79.5, ring) is True
    assert _point_in_polygon_ring(35.0, -79.5, ring) is True
    assert _point_in_polygon_ring(36.5, -79.5, ring) is False


def test_polygon_output_filter_removes_only_known_outside_records():
    class _Scalars:
        def __init__(self, rows):
            self._rows = rows

        def all(self):
            return self._rows

    class _Session:
        def scalars(self, _stmt):
            return _Scalars([
                {
                    "geometry_type": "Polygon",
                    "coordinates": "-80,35,0 -79,35,0 -79,36,0 -80,36,0 -80,35,0",
                }
            ])

    inside = SimpleNamespace(id=1, latitude=35.5, longitude=-79.5, raw_metadata={})
    outside = SimpleNamespace(id=2, latitude=36.5, longitude=-79.5, raw_metadata={})
    no_coord = SimpleNamespace(id=3, latitude=None, longitude=None, raw_metadata={})
    polygon_layer = SimpleNamespace(id=4, latitude=None, longitude=None, raw_metadata={"geometry_type": "Polygon"})

    filtered = _filter_known_outside_polygon_records(_Session(), "job-id", [inside, outside, no_coord, polygon_layer])

    assert [row.id for row in filtered] == [1, 3, 4]


def test_address_source_label_prefers_csv_for_merged_rows() -> None:
    assert _address_source_label({"address_source": "csv", "file_role": "geospatial"}, "network.kmz") == "csv"
    assert _address_source_label({"file_role": "geospatial", "source_format": "kmz"}, "network.kmz") == "kmz"
    assert _address_source_label({"file_role": "tabular", "source_format": "csv"}, "addresses.csv") == "csv"




def test_address_source_label_uses_csv_when_same_address_exists_in_csv() -> None:
    meta = {"file_role": "geospatial", "source_format": "kmz", "merge_status": "verified"}

    assert _address_source_label(
        meta,
        "network.kmz",
        address_key="4 FERGUSON ST",
        csv_address_keys={"4 FERGUSON ST"},
    ) == "csv"

def test_address_source_label_marks_new_rows_as_new_address() -> None:
    meta = {"merge_status": "new", "rule_status": "new", "file_role": "geospatial"}

    assert _address_source_label(meta, "network.kmz") == "new address"

def test_frontend_export_includes_address_source_column() -> None:
    row = _build_frontend_export_row(_export_address(source_file="network.kmz"), meta={"file_role": "geospatial"})

    assert "address_source" in FRONTEND_EXPORT_COLUMNS
    assert row["address_source"] == "kmz"


def test_ui_export_columns_include_address_source_after_source_file() -> None:
    columns = [
        {"key": "source_row_number", "label": "#", "source": "raw"},
        {"key": "address_source", "label": "Source", "source": "raw"},
        {"key": "source_file", "label": "Source File", "source": "raw"},
        {"key": "raw_address", "label": "Address", "source": "raw"},
    ]

    ordered = _ui_table_columns(columns)

    assert [c["key"] for c in ordered[:4]] == ["source_file", "address_source", "source_row_number", "raw_address"]

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from data_ingestion.schemas import RawExtractedRecord
from data_ingestion.utils.strings import clean_value, normalize_header, parse_kml_coordinate


PLACEMARK_NAME_HEADERS = {
    "name2",
    "placemark name",
    "placemark",
    "feature name",
    "name 2",
}
FIELD_NAME_HEADERS = {"name", "field", "attribute", "attribute name", "description name"}
FIELD_VALUE_HEADERS = {"value", "field value", "attribute value", "description value"}
COORDINATE_HEADERS = {"coordinates", "coordinate", "coord", "coords"}


def _find_column(columns: list[str], accepted: set[str]) -> str | None:
    normalized = {column: normalize_header(column) for column in columns}
    for column, norm in normalized.items():
        if norm in accepted:
            return column
    return None


def looks_like_flattened_kml_table(df: pd.DataFrame) -> bool:
    columns = [str(c) for c in df.columns]
    placemark = _find_column(columns, PLACEMARK_NAME_HEADERS)
    field_name = _find_column(columns, FIELD_NAME_HEADERS)
    field_value = _find_column(columns, FIELD_VALUE_HEADERS)
    coordinates = _find_column(columns, COORDINATE_HEADERS)
    return bool(placemark and field_name and (field_value or coordinates))


def extract_flattened_kml_table(
    df: pd.DataFrame,
    *,
    source_file: str | Path,
    source_sheet: str | None = None,
) -> list[RawExtractedRecord]:
    """Group flattened KML/KMZ export rows into one raw record per placemark.

    This handles tables where a single GIS placemark appears on repeated rows:
    - `ns1:name2` contains the placemark/address name
    - `name` contains a field name, such as Address or Address ID
    - `ns1:value` contains the field value
    - `ns1:coordinates` contains KML coordinates
    """
    source_file = str(source_file)
    columns = [str(c) for c in df.columns]

    placemark_col = _find_column(columns, PLACEMARK_NAME_HEADERS)
    field_col = _find_column(columns, FIELD_NAME_HEADERS)
    value_col = _find_column(columns, FIELD_VALUE_HEADERS)
    coord_col = _find_column(columns, COORDINATE_HEADERS)

    if not placemark_col or not field_col:
        return []

    grouped: dict[str, dict[str, Any]] = {}
    row_numbers: dict[str, int] = {}

    for idx, row in df.iterrows():
        placemark_name = clean_value(row.get(placemark_col))
        if not placemark_name:
            continue

        key = str(placemark_name).strip()
        if key not in grouped:
            grouped[key] = {
                "placemark_name": key,
                "raw_address": key,
                "source_format": "flattened_kml_table",
            }
            row_numbers[key] = int(idx) + 2  # include header row

        field_name = clean_value(row.get(field_col))
        field_value = clean_value(row.get(value_col)) if value_col else None
        if field_name and field_value is not None:
            grouped[key][str(field_name)] = field_value

        if coord_col:
            coordinate_text = clean_value(row.get(coord_col))
            if coordinate_text and "coordinates" not in grouped[key]:
                lat, lon = parse_kml_coordinate(coordinate_text)
                grouped[key]["coordinates"] = coordinate_text
                grouped[key]["latitude"] = lat
                grouped[key]["longitude"] = lon

    return [
        RawExtractedRecord(
            source_file=source_file,
            source_sheet=source_sheet,
            row_number=row_numbers.get(key),
            raw_data=data,
        )
        for key, data in grouped.items()
    ]

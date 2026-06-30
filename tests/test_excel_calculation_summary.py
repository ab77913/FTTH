from __future__ import annotations

import openpyxl

from api_server import (
    _a1a2_confidence_score,
    _add_excel_calculation_summary,
    _excel_summary_metrics,
)


def _row(address_id: int, status: str, *, source_format: str = "csv", discovered: str = ""):
    row = {
        "id": address_id,
        "raw_address": f"{address_id} Main St",
        "source_file": "6CA3.xlsx",
        "file_role": "tabular" if not discovered else "geospatial",
        "source_format": discovered or source_format,
        "original_source_file": "6CA3.xlsx",
        "rule_status": "new" if discovered else status,
        "rule_color": "yellow" if discovered else ("green" if status == "valid" else "red"),
        "_is_tabular_upload": not bool(discovered),
    }
    if discovered:
        row[f"{discovered}_discovered"] = True
    return row


def test_excel_summary_matches_requested_calculation_example() -> None:
    rows = []
    for address_id in range(1, 178):
        rows.append(_row(address_id, "valid"))
    for address_id in range(178, 223):
        rows.append(_row(address_id, "invalid"))
    for address_id in range(223, 237):
        rows.append(_row(address_id, "new", discovered="agent7"))

    metrics = _excel_summary_metrics(rows)

    assert metrics == {
        "file_name": "6CA3",
        "addresses_in_demand": 222,
        "addresses_served_in_field": 177,
        "addresses_not_served_in_field": 45,
        "additional_addresses": 14,
        "engineered_hh_count": 191,
        "out_of_da": 0,
        "address_data_not_given": 0,
        "duplicate_addresses": 0,
        "discrepancy_percentage": 45 / 222,
        "comments": "EXCEL ATTACHED",
    }


def test_excel_summary_excludes_duplicates_from_planned_demand() -> None:
    rows = [
        _row(1, "valid"),
        _row(2, "valid"),
        _row(3, "invalid"),
        _row(4, "duplicate"),
    ]

    metrics = _excel_summary_metrics(rows)

    assert metrics["addresses_in_demand"] == 3
    assert metrics["addresses_served_in_field"] == 2
    assert metrics["addresses_not_served_in_field"] == 1
    assert metrics["duplicate_addresses"] == 1
    assert metrics["engineered_hh_count"] == 2
    assert metrics["discrepancy_percentage"] == 1 / 3


def test_excel_summary_detects_duplicate_from_raw_metadata() -> None:
    duplicate_row = _row(2, "valid")
    duplicate_row.update({
        "merge_status": "verified",
        "record_status": "DUPLICATE",
        "raw_metadata_json": {
            "merge_status": "duplicate",
            "record_status": "DUPLICATE",
        },
    })

    metrics = _excel_summary_metrics([_row(1, "valid"), duplicate_row])

    assert metrics["addresses_in_demand"] == 1
    assert metrics["addresses_served_in_field"] == 1
    assert metrics["addresses_not_served_in_field"] == 0
    assert metrics["duplicate_addresses"] == 1



def test_excel_summary_uses_updated_sheet_source_values() -> None:
    rows = [
        {
            "id": 1,
            "raw_address": "1 Main St",
            "source_file": "demand.csv",
            "address_source": "csv",
            "rule_status": "valid",
            "merge_status": "verified",
        },
        {
            "id": 2,
            "raw_address": "2 Main St",
            "source_file": "demand.csv",
            "address_source": "csv",
            "rule_status": "invalid",
            "merge_status": "invalid",
        },
        {
            "id": 3,
            "raw_address": "3 Main St",
            "source_file": "demand.csv",
            "address_source": "csv",
            "rule_status": "duplicate",
            "merge_status": "duplicate",
        },
        {
            "id": 4,
            "raw_address": "4 Main St",
            "source_file": "network.kmz",
            "address_source": "new address",
            "rule_status": "new",
            "merge_status": "new",
        },
    ]

    metrics = _excel_summary_metrics(rows)

    assert metrics["file_name"] == "demand"
    assert metrics["addresses_in_demand"] == 2
    assert metrics["addresses_served_in_field"] == 1
    assert metrics["addresses_not_served_in_field"] == 1
    assert metrics["additional_addresses"] == 1
    assert metrics["engineered_hh_count"] == 2
    assert metrics["duplicate_addresses"] == 1
    assert metrics["discrepancy_percentage"] == 1 / 2

def test_excel_summary_sheet_uses_requested_layout_and_percentage_format() -> None:
    workbook = openpyxl.Workbook()
    metrics = {
        "file_name": "6CA3",
        "addresses_in_demand": 222,
        "addresses_served_in_field": 177,
        "addresses_not_served_in_field": 45,
        "additional_addresses": 14,
        "engineered_hh_count": 191,
        "out_of_da": 0,
        "address_data_not_given": 0,
        "duplicate_addresses": 0,
        "discrepancy_percentage": 45 / 222,
        "comments": "EXCEL ATTACHED",
    }

    sheet = _add_excel_calculation_summary(workbook, metrics)

    assert sheet.title == "Calculation Summary"
    assert sheet["A2"].value == "FILE NAME"
    assert sheet["A3"].value == "6CA3"
    assert sheet["B3"].value == 222
    assert sheet["F3"].number_format == "0.00%"
    assert sheet["A8"].value == "TOTAL PLANNED HH/CSV ADDRESSES"
    assert sheet["B9"].value == 191
    assert sheet["A10"].fill.fgColor.rgb == "FF00B050"
    assert sheet["A11"].fill.fgColor.rgb == "FFFFFF00"
    assert sheet["A12"].fill.fgColor.rgb == "FFFF0000"
    assert sheet["B16"].number_format == "0.00%"


def test_a1a2_match_confidence_is_always_100() -> None:
    assert _a1a2_confidence_score("MATCH", 99, 35, 0, 35) == 100
    assert _a1a2_confidence_score("MISMATCH_WARN", 35, 99, 0, 35) == 35


from __future__ import annotations

from types import SimpleNamespace

from data_ingestion.utils.ingest_display import ingest_display_fields


def test_ingest_display_prefers_source_columns_over_agent_updates() -> None:
    addr = SimpleNamespace(
        source_raw_address="4345 NW 154TH ST",
        source_latitude=29.37,
        source_longitude=-82.19,
        raw_address="Agent Updated Address",
        city="TAMPA",
        state="FL",
        zip_code="33601",
        latitude=28.0,
        longitude=-83.0,
        source_row_number=5,
        raw_metadata={
            "old": {
                "street_number_name": "4345 NW 154TH ST",
                "city": "REDDICK",
                "state": "FL",
                "latitude": 29.37,
                "longitude": -82.19,
            }
        },
    )
    fields = ingest_display_fields(addr)
    assert fields["raw_address"] == "4345 NW 154TH ST"
    assert fields["city"] == "REDDICK"
    assert fields["state"] == "FL"
    assert fields["latitude"] == 29.37
    assert fields["longitude"] == -82.19
    assert fields["source_row_number"] == 5

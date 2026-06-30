from data_ingestion.parsers import CanonicalMapper
from data_ingestion.schemas import RawExtractedRecord


def test_maps_basic_address_record():
    raw = RawExtractedRecord(
        source_file="test.csv",
        row_number=2,
        raw_data={
            "Address": "1603 LAFAYETTE ST",
            "Latitude": "32.086591",
            "Longitude": "-84.241345",
            "Terminal ID": "T-1",
            "Node": "6BA8",
            "Address ID": "A1098636881",
        },
    )

    record = CanonicalMapper().map_record(raw, customer_id="demo")

    assert record.raw_address == "1603 LAFAYETTE ST"
    assert record.latitude == 32.086591
    assert record.longitude == -84.241345
    assert record.terminal_id == "T-1"
    assert record.network_node == "6BA8"
    assert record.address_id == "A1098636881"
    assert record.normalized_key is not None

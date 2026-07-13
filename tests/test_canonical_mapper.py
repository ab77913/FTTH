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


def test_maps_canada_post_street_number_suffix_into_raw_address():
    raw = RawExtractedRecord(
        source_file="canadapost.csv",
        row_number=3,
        raw_data={
            "Street Number": "37",
            "Street Number Suffix": "A",
            "Street Name": "JOHN",
            "Street Type": "ST",
            "City": "St. Catharines",
            "State": "ON",
            "Postal Code": "L2N4P2",
        },
    )

    record = CanonicalMapper().map_record(raw, customer_id="demo")

    assert "37A" in (record.raw_address or "").replace(" ", "")
    assert "JOHN" in (record.raw_address or "").upper()


def test_maps_suffix_when_address_column_omits_it():
    raw = RawExtractedRecord(
        source_file="canadapost.csv",
        row_number=89,
        raw_data={
            "ADDRESS": "9 DOROTHY ST, ST CATHARINES, ON",
            "Street Number": "9",
            "Street Number Suffix": "B",
            "Street Name": "DOROTHY",
            "Street Type": "ST",
            "City": "ST CATHARINES",
            "State": "ON",
            "Postal Code": "L2N4A4",
            "Latitude": "43.181273",
            "Longitude": "-79.265959",
        },
    )

    record = CanonicalMapper().map_record(raw, customer_id="demo")

    assert "9B" in (record.raw_address or "").replace(" ", "").upper()
    assert "DOROTHY" in (record.raw_address or "").upper()

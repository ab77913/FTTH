from data_ingestion.schemas import CanonicalAddressRecord
from data_ingestion.validators import validate_and_deduplicate


def test_validate_and_deduplicate():
    records = [
        CanonicalAddressRecord(source_file="x.csv", raw_address="1603 LAFAYETTE ST", normalized_key="1603 LAFAYETTE ST"),
        CanonicalAddressRecord(source_file="x.csv", raw_address="1603 LAFAYETTE ST", normalized_key="1603 LAFAYETTE ST"),
        CanonicalAddressRecord(source_file="x.csv", raw_address=None),
    ]

    summary = validate_and_deduplicate(records)

    assert summary.valid_count == 1
    assert summary.duplicate_count == 1
    assert summary.invalid_count == 1


def test_validate_and_deduplicate_uses_normalized_address_match():
    records = [
        CanonicalAddressRecord(source_file="x.csv", raw_address="1603 LAFAYETTE STREET", normalized_key="1603 LAFAYETTE ST"),
        CanonicalAddressRecord(source_file="x.csv", raw_address="1603 LAFAYETTE ST", normalized_key="1603 LAFAYETTE ST"),
    ]

    summary = validate_and_deduplicate(records)

    assert summary.valid_count == 1
    assert summary.duplicate_count == 1


def test_validate_and_deduplicate_normalized_match_ignores_case_and_spacing():
    records = [
        CanonicalAddressRecord(source_file="x.csv", raw_address="1603  LAFAYETTE ST", normalized_key="1603 LAFAYETTE ST"),
        CanonicalAddressRecord(source_file="x.csv", raw_address="1603 lafayette st", normalized_key="1603 LAFAYETTE ST"),
    ]

    summary = validate_and_deduplicate(records)

    assert summary.valid_count == 1
    assert summary.duplicate_count == 1



def test_validate_and_deduplicate_marks_only_repeated_address_duplicate():
    first = CanonicalAddressRecord(source_file="x.csv", raw_address="4 FERGUSON ST", normalized_key="4 FERGUSON ST")
    second = CanonicalAddressRecord(source_file="x.csv", raw_address="4 ferguson st", normalized_key="4 FERGUSON ST")

    summary = validate_and_deduplicate([first, second])

    assert summary.valid_records == [first]
    assert summary.duplicate_records == [second]
    assert first.raw_metadata["record_status"] == "UNIQUE"
    assert second.raw_metadata["record_status"] == "DUPLICATE"
    assert second.raw_metadata["canonical_record_id"] == str(first.record_id)
    assert second.raw_metadata["duplicate_reason"] == "Normalized address match"


def test_validate_and_deduplicate_ignores_coordinates_for_duplicate_decision() -> None:
    first = CanonicalAddressRecord(
        source_file="a.csv",
        raw_address="4 FERGUSON ST",
        latitude=10.0,
        longitude=20.0,
        normalized_key="4 FERGUSON ST",
    )
    second = CanonicalAddressRecord(
        source_file="b.csv",
        raw_address="4 Ferguson Street",
        latitude=11.0,
        longitude=21.0,
        normalized_key="4 FERGUSON STREET",
    )

    summary = validate_and_deduplicate([first, second])

    assert summary.valid_records == [first]
    assert summary.duplicate_records == [second]
    assert second.raw_metadata["record_status"] == "DUPLICATE"
    assert second.raw_metadata["duplicate_normalized_address"] == "4 FERGUSON ST"


def test_validate_and_deduplicate_does_not_merge_blank_addresses() -> None:
    records = [
        CanonicalAddressRecord(source_file="a.csv", raw_address=None),
        CanonicalAddressRecord(source_file="b.csv", raw_address=""),
    ]

    summary = validate_and_deduplicate(records)

    assert summary.valid_count == 0
    assert summary.duplicate_count == 0
    assert summary.invalid_count == 2
    assert [rec.raw_metadata["record_status"] for rec in summary.invalid_records] == ["ADDRESS_MISSING", "ADDRESS_MISSING"]
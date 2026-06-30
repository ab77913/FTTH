"""Tests for upload validation utilities."""

from pathlib import Path


from data_ingestion.utils.upload_validation import (
    validate_content_signature,
    validate_extension,
    validate_file_size,
    validate_uploaded_file,
)


def test_validate_extension_rejects_unknown(tmp_path: Path):
    bad = tmp_path / "data.txt"
    bad.write_text("hello", encoding="utf-8")
    assert validate_extension(bad) is not None


def test_validate_csv_passes(tmp_path: Path):
    csv_file = tmp_path / "addresses.csv"
    csv_file.write_text("street,city\n1 Main,Anytown\n", encoding="utf-8")
    assert validate_uploaded_file(csv_file) is None


def test_validate_kml_signature(tmp_path: Path):
    kml = tmp_path / "map.kml"
    kml.write_text('<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2"></kml>', encoding="utf-8")
    assert validate_content_signature(kml) is None


def test_validate_empty_file_rejected(tmp_path: Path):
    empty = tmp_path / "empty.csv"
    empty.write_bytes(b"")
    assert validate_file_size(empty, limit=1024) is not None


def test_validate_kmz_requires_zip_signature(tmp_path: Path):
    fake = tmp_path / "bad.kmz"
    fake.write_text("not a zip", encoding="utf-8")
    assert validate_content_signature(fake) is not None

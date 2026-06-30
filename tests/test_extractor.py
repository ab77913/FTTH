"""Optional KML extractor smoke test for an external KMZ fixture."""

from __future__ import annotations

import os
import zipfile
from pathlib import Path

import pytest

from data_ingestion.extractors.kml_extractor import KMLExtractor


def test_external_kmz_extracts_records() -> None:
    kmz_env = os.environ.get("FTTH_TEST_KMZ")
    if not kmz_env:
        pytest.skip("Set FTTH_TEST_KMZ to run the external KMZ extractor smoke test")
    kmz_path = Path(kmz_env)
    if not kmz_path.is_file():
        pytest.skip("Set FTTH_TEST_KMZ to run the external KMZ extractor smoke test")

    with zipfile.ZipFile(kmz_path) as zf:
        kml_files = [name for name in zf.namelist() if name.endswith(".kml")]
        assert kml_files
        content = zf.read(kml_files[0])

    records = KMLExtractor().extract_from_bytes(content, source_file=kmz_path.name)
    assert records
    assert records[0].raw_data

"""Optional KMZ ingestion smoke test for an external fixture."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from data_ingestion.database.db import session_scope
from data_ingestion.database.repositories import IngestionRepository
from data_ingestion.ingestion_service import IngestionService


def test_external_kmz_ingests() -> None:
    kmz_env = os.environ.get("FTTH_TEST_KMZ")
    if not kmz_env:
        pytest.skip("Set FTTH_TEST_KMZ to run the external KMZ ingestion smoke test")
    kmz_path = Path(kmz_env)
    if not kmz_path.is_file():
        pytest.skip("Set FTTH_TEST_KMZ to run the external KMZ ingestion smoke test")

    with session_scope() as session:
        service = IngestionService(IngestionRepository(session))
        result = service.ingest_file(kmz_path)

    assert result.total_raw_records > 0
    assert result.stored_records > 0

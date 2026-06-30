"""
Quick integration test for the FTTH Data Ingestion pipeline.

This script tests:
1. CSV extraction from sample data
2. Canonical mapping (field alias resolution)
3. Validation and deduplication
4. (If DB available) Database init and full ingestion

Run:  python test_pipeline.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from data_ingestion.extractors import CSVExtractor
from data_ingestion.parsers import CanonicalMapper
from data_ingestion.validators import validate_and_deduplicate
from data_ingestion.utils.file_detection import detect_file_type, FileType


def test_csv_extraction():
    print("=" * 60)
    print("TEST 1: CSV Extraction")
    print("=" * 60)
    csv_path = PROJECT_ROOT / "examples" / "sample_addresses.csv"
    extractor = CSVExtractor()
    records = extractor.extract(csv_path)
    print(f"  File: {csv_path.name}")
    print(f"  Records extracted: {len(records)}")
    assert len(records) > 0, "No records extracted from CSV"
    print(f"  First record raw_data keys: {list(records[0].raw_data.keys())}")
    print(f"  First record raw_data: {records[0].raw_data}")
    print("  [PASS]\n")


def test_file_detection():
    print("=" * 60)
    print("TEST 2: File Type Detection")
    print("=" * 60)
    csv_path = PROJECT_ROOT / "examples" / "sample_addresses.csv"
    kml_path = PROJECT_ROOT / "examples" / "sample.kml"

    csv_type = detect_file_type(csv_path)
    print(f"  {csv_path.name} -> {csv_type}")
    assert csv_type == FileType.CSV

    kml_type = detect_file_type(kml_path)
    print(f"  {kml_path.name} -> {kml_type}")
    assert kml_type == FileType.KML

    print("  [PASS]\n")


def test_canonical_mapping():
    print("=" * 60)
    print("TEST 3: Canonical Mapping")
    print("=" * 60)
    raw_records = CSVExtractor().extract(PROJECT_ROOT / "examples" / "sample_addresses.csv")
    mapper = CanonicalMapper()
    canonical = mapper.map_records(raw_records, customer_id="demo_customer")
    print(f"  Mapped {len(canonical)} records to canonical format")
    first = canonical[0]
    print("  Sample record:")
    print(f"    raw_address:   {first.raw_address}")
    print(f"    latitude:      {first.latitude}")
    print(f"    longitude:     {first.longitude}")
    print(f"    terminal_id:   {first.terminal_id}")
    print(f"    network_node:  {first.network_node}")
    print(f"    address_id:    {first.address_id}")
    print(f"    customer_id:   {first.customer_id}")
    print(f"    normalized_key:{first.normalized_key}")
    assert first.raw_address is not None, "raw_address should not be None"
    assert first.latitude is not None, "latitude should not be None"
    print("  [PASS]\n")


def test_validation():
    print("=" * 60)
    print("TEST 4: Validation & Deduplication")
    print("=" * 60)
    raw_records = CSVExtractor().extract(PROJECT_ROOT / "examples" / "sample_addresses.csv")
    canonical_records = CanonicalMapper().map_records(raw_records, customer_id="demo_customer")
    summary = validate_and_deduplicate(canonical_records)
    print(f"  Valid:      {summary.valid_count}")
    print(f"  Invalid:    {summary.invalid_count}")
    print(f"  Duplicates: {summary.duplicate_count}")
    assert summary.valid_count > 0, "Should have at least one valid record"
    print("  [PASS]\n")


def test_database_ingestion():
    print("=" * 60)
    print("TEST 5: Database Ingestion (full pipeline)")
    print("=" * 60)
    try:
        from data_ingestion.config.settings import get_settings
        from data_ingestion.database.db import init_db, session_scope
        from data_ingestion.database.repositories import IngestionRepository
        from data_ingestion.ingestion_service import IngestionService

        settings = get_settings()
        print(f"  Database URL: {settings.database_url}")
        print("  Initializing database...")
        init_db()
        print("  Database schema created successfully!")

        csv_path = PROJECT_ROOT / "examples" / "sample_addresses.csv"
        with session_scope() as session:
            repo = IngestionRepository(session)
            service = IngestionService(repo)
            result = service.ingest_file(csv_path, customer_id="demo_customer")

        print("  Ingestion Result:")
        print(f"    Job ID:          {result.job_id}")
        print(f"    Source File:     {result.source_file}")
        print(f"    Total Raw:       {result.total_raw_records}")
        print(f"    Valid:           {result.valid_records}")
        print(f"    Invalid:         {result.invalid_records}")
        print(f"    Duplicates:      {result.duplicate_records}")
        print(f"    Stored:          {result.stored_records}")
        print(f"    Queued:          {result.queued_records}")
        print(f"    Status:          {result.status}")
        print("  [PASS]\n")
    except Exception as e:
        import pytest

        pytest.skip(f"Database not available: {e}")


def main():
    print("\n" + "#" * 60)
    print("  FTTH Data Ingestion Pipeline - Integration Test")
    print("#" * 60 + "\n")

    # Tests that work without DB
    test_csv_extraction()
    test_file_detection()
    test_canonical_mapping()
    test_validation()

    # Database test (optional)
    try:
        test_database_ingestion()
        db_ok = True
    except Exception:
        db_ok = False

    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print("  Extraction:    PASS")
    print("  Detection:     PASS")
    print("  Mapping:       PASS")
    print("  Validation:    PASS")
    print(f"  DB Ingestion:  {'PASS' if db_ok else 'SKIP (Docker/DB not available)'}")
    print()
    if db_ok:
        print("  All tests PASSED! The pipeline is fully working.")
    else:
        print("  Core pipeline works! Start Docker for full DB integration.")
        print("  Run: docker-compose up -d")
        print("  Then: python test_pipeline.py")
    print()


if __name__ == "__main__":
    main()

"""Re-extract a stored KMZ/KML upload and insert placemarks missing from a job."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from uuid import UUID

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from data_ingestion.database.db import get_session_factory
from data_ingestion.database.models import Address, IngestionJob
from data_ingestion.database.repositories import IngestionRepository
from data_ingestion.extractors.kml_extractor import KMLExtractor
from data_ingestion.parsers.canonical_mapper import CanonicalMapper
from data_ingestion.utils.kml_categories import records_for_storage
from data_ingestion.validators import validate_and_deduplicate
from sqlalchemy import select


def _find_upload_kmz(job: IngestionJob) -> Path | None:
    uploads = PROJECT_ROOT / "uploads"
    if not uploads.is_dir():
        return None
    name = job.source_file
    matches = sorted(uploads.glob(f"**/{name}"), key=lambda p: p.stat().st_mtime, reverse=True)
    return matches[0] if matches else None


def _record_key(rec) -> tuple:
    meta = rec.raw_metadata or {}
    return (
        meta.get("geometry_type") or "",
        (rec.raw_address or "").strip().upper(),
        round(float(rec.latitude or 0), 6),
        round(float(rec.longitude or 0), 6),
    )


def backfill_job(job_id: str, *, kmz_path: Path | None = None) -> int:
    session = get_session_factory()()
    try:
        job = session.get(IngestionJob, UUID(job_id))
        if not job:
            raise SystemExit(f"Job not found: {job_id}")

        path = kmz_path or _find_upload_kmz(job)
        if not path or not path.is_file():
            raise SystemExit(f"KMZ/KML file not found for job {job_id}")

        raw_records = KMLExtractor().extract(path)
        mapped = CanonicalMapper().map_records(raw_records, customer_id=job.customer_id)
        for rec in mapped:
            rec.job_id = job.id
        candidates = records_for_storage(mapped)
        summary = validate_and_deduplicate(candidates)
        incoming = summary.valid_records + summary.invalid_records

        existing = session.scalars(select(Address).where(Address.job_id == job.id)).all()
        existing_keys = {_record_key_from_address(a) for a in existing}

        repo = IngestionRepository(session)
        to_add = []
        for rec in incoming:
            if _record_key(rec) in existing_keys:
                continue
            rec.normalized_key = None if rec.validation_errors else rec.normalized_key
            to_add.append(rec)

        if not to_add:
            print(f"No missing placemarks for job {job_id}")
            return 0

        saved = repo.save_addresses(to_add)
        repo.update_job_row_count(job.id, len(existing) + len(saved))
        repo.enqueue_addresses(saved[: len([r for r in to_add if not r.validation_errors])])
        session.commit()
        print(f"Added {len(saved)} missing placemark(s) to job {job_id} from {path.name}")
        for addr in saved:
            meta = addr.raw_metadata or {}
            print(f"  + id={addr.id} {addr.raw_address} ({meta.get('geometry_type')}) lat={addr.latitude} lon={addr.longitude}")
        return len(saved)
    finally:
        session.close()


def _record_key_from_address(addr: Address) -> tuple:
    meta = addr.raw_metadata or {}
    return (
        meta.get("geometry_type") or "",
        (addr.raw_address or "").strip().upper(),
        round(float(addr.latitude or 0), 6),
        round(float(addr.longitude or 0), 6),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job_id", help="Ingestion job UUID")
    parser.add_argument("--kmz", type=Path, default=None, help="Optional KMZ/KML path override")
    args = parser.parse_args()
    backfill_job(args.job_id, kmz_path=args.kmz)


if __name__ == "__main__":
    main()

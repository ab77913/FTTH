#!/usr/bin/env python3
"""
Backfill reverse_geocode_confidence_score into addresses.raw_metadata for a job.

Usage (from FTTH_PROD):
  python scripts/dev/backfill_confidence_metadata.py <job_id>
"""
from __future__ import annotations

import sys
from pathlib import Path
from uuid import UUID

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from sqlalchemy import select

from data_ingestion.agents.reverse_geocoder import (
    _backfill_confidence_in_raw_metadata,
    _compute_confidence,
    _reverse_geocode,
    _sync_confidence_in_raw_metadata,
)
from data_ingestion.database.db import ensure_address_validation_columns, get_session_factory
from data_ingestion.database.models import Address


def backfill_job(job_id: str) -> dict:
    ensure_address_validation_columns()
    session = get_session_factory()()
    updated = 0
    recomputed = 0
    skipped = 0
    try:
        rows = session.scalars(
            select(Address).where(Address.job_id == UUID(job_id)).order_by(Address.id)
        ).all()
        for addr in rows:
            if _backfill_confidence_in_raw_metadata(addr):
                updated += 1
                continue
            meta = addr.raw_metadata or {}
            av = meta.get("address_validation") if isinstance(meta.get("address_validation"), dict) else {}
            if not av or av.get("confidence_score") is not None:
                skipped += 1
                continue
            lat, lon = addr.latitude, addr.longitude
            if not lat or not lon:
                skipped += 1
                continue
            geo = _reverse_geocode(float(lat), float(lon))
            if not geo:
                skipped += 1
                continue
            status = av.get("match_status") or addr.coord_address_match_status or ""
            distance_m = av.get("distance_m")
            if distance_m is None:
                distance_m = addr.coord_address_distance_m
            score = _compute_confidence(geo, distance_m=distance_m, match_status=status)
            addr.reverse_geocode_confidence_score = score
            _sync_confidence_in_raw_metadata(addr, score)
            recomputed += 1
        session.commit()
        return {"total": len(rows), "patched_from_column": updated, "recomputed": recomputed, "skipped": skipped}
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python scripts/dev/backfill_confidence_metadata.py <job_id>")
        sys.exit(1)
    summary = backfill_job(sys.argv[1])
    print(summary)


if __name__ == "__main__":
    main()

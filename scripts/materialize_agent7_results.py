"""Convert legacy Agent 7 column-only discoveries into Address records."""
from __future__ import annotations

import argparse
import json

from sqlalchemy import func, select, text

from data_ingestion.agents.agent7_neighborhood_discovery import (
    AGENT_NAME,
    _create_address_from_discovery,
    _distance_meters,
    _upsert_result,
)
from data_ingestion.database.db import get_session_factory
from data_ingestion.database.models import Address, AgentResult, IngestionJob
from data_ingestion.utils.strings import normalize_address_key


def materialize(job_id: str, *, dedup_distance_m: float = 25.0) -> dict:
    session = get_session_factory()()
    try:
        rows = session.scalars(select(Address).where(Address.job_id == job_id)).all()
        by_id = {int(row.id): row for row in rows}
        household_rows = [
            row for row in rows
            if str((row.raw_metadata or {}).get("map_layer_only", "")).lower() != "true"
            and str((row.raw_metadata or {}).get("geometry_type") or "") not in {"Polygon", "LineString"}
        ]
        existing_keys = {
            key
            for row in household_rows
            if (key := (row.normalized_key or normalize_address_key(row.raw_address)))
        }
        existing_coords = [
            (float(row.latitude), float(row.longitude))
            for row in household_rows
            if row.latitude is not None and row.longitude is not None
        ]
        legacy_results = session.scalars(select(AgentResult).where(
            AgentResult.job_id == job_id,
            AgentResult.agent_name == AGENT_NAME,
        ).order_by(AgentResult.address_id)).all()

        created_ids: list[int] = []
        skipped_duplicates = 0
        for result in legacy_results:
            data = dict(result.data or {})
            if data.get("is_new_record"):
                continue
            discoveries = data.get("new_address_details") or []
            materialized: list[dict] = []
            for raw_discovery in discoveries:
                discovery = dict(raw_discovery or {})
                previous_id = discovery.get("created_address_id")
                if previous_id and session.get(Address, int(previous_id)) is not None:
                    materialized.append(discovery)
                    continue
                address = str(discovery.get("address") or "").strip()
                key = normalize_address_key(address)
                try:
                    lat = float(discovery["latitude"])
                    lon = float(discovery["longitude"])
                except (KeyError, TypeError, ValueError):
                    continue
                near_existing = any(
                    _distance_meters(lat, lon, old_lat, old_lon) <= dedup_distance_m
                    for old_lat, old_lon in existing_coords
                )
                if not address or (key and key in existing_keys) or near_existing:
                    skipped_duplicates += 1
                    continue
                seed = by_id.get(int(discovery.get("seed_address_id") or result.address_id))
                polygon = by_id.get(int(discovery.get("polygon_id") or 0))
                if seed is None or polygon is None:
                    continue
                new_row = _create_address_from_discovery(
                    session,
                    job_id=job_id,
                    discovery=discovery,
                    seed_row=seed,
                    polygon_row=polygon,
                )
                discovery["created_address_id"] = new_row.id
                materialized.append(discovery)
                created_ids.append(new_row.id)
                by_id[int(new_row.id)] = new_row
                if key:
                    existing_keys.add(key)
                existing_coords.append((lat, lon))
                _upsert_result(session, job_id=job_id, address_id=new_row.id, data={
                    "status": "accepted_new_record",
                    "is_new_record": True,
                    "final_address": address,
                    "new_addresses": [address],
                    "new_address_count": 1,
                    "new_address_details": [discovery],
                    "candidates_checked": 1,
                    "created_address_id": new_row.id,
                })

            data["new_addresses"] = []
            data["discovered_addresses"] = [item.get("address") for item in materialized]
            data["created_address_ids"] = [item["created_address_id"] for item in materialized]
            data["new_address_details"] = materialized
            result.data = data

        agent7_rows = session.scalars(select(Address).where(
            Address.job_id == job_id,
            text("raw_metadata->>'agent7_discovered' = 'true'"),
        )).all()
        for row in agent7_rows:
            existing_result = session.scalar(select(AgentResult).where(
                AgentResult.agent_name == AGENT_NAME,
                AgentResult.address_id == row.id,
            ))
            result_data = dict(existing_result.data or {}) if existing_result else {}
            result_data.update({
                "status": "accepted_new_record",
                "is_new_record": True,
                "final_address": row.raw_address,
                "new_addresses": [row.raw_address],
                "new_address_count": 1,
                "created_address_id": row.id,
            })
            _upsert_result(session, job_id=job_id, address_id=row.id, data=result_data)

        job = session.get(IngestionJob, job_id)
        if job is not None:
            job.row_count = int(session.scalar(
                select(func.count()).select_from(Address).where(Address.job_id == job_id)
            ) or 0)
        session.commit()
        return {
            "job_id": job_id,
            "created": len(created_ids),
            "created_address_ids": created_ids,
            "skipped_duplicates": skipped_duplicates,
            "row_count": job.row_count if job is not None else None,
        }
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("job_id")
    parser.add_argument("--dedup-distance-m", type=float, default=25.0)
    args = parser.parse_args()
    print(json.dumps(materialize(args.job_id, dedup_distance_m=args.dedup_distance_m), indent=2))

"""
Celery tasks for the FTTH pipeline.

The main task `run_pipeline_task` runs the full 6-agent pipeline in a
Celery worker process and pushes live progress updates to Redis so the
API server can stream them to clients.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any
from uuid import UUID

from celery import Task

from data_ingestion.worker.celery_app import celery_app
from data_ingestion.agents.pipeline_runner import run_full_pipeline
from data_ingestion.database.db import session_scope
from data_ingestion.database.repositories import IngestionRepository
from data_ingestion.schemas import IngestionStatus
from data_ingestion.utils.pipeline_options import normalize_pipeline_options
from data_ingestion.utils.pipeline_progress import (
    apply_stage_progress,
    finalize_agents_from_results,
    mark_current_agent_failed,
)

logger = logging.getLogger(__name__)

# Redis key patterns (same as api_server uses)
_PROGRESS_KEY = "ftth:job:progress:{job_id}"
_PROGRESS_CHANNEL = "ftth:progress:{job_id}"
_TTL_SECONDS = 86_400

_AGENT_DEFINITIONS = [
    {"id": "agent0_house_discovery", "name": "Agent 0: House Discovery", "description": "Discovers households inside uploaded KML/KMZ polygons"},
    {"id": "agent2_geocoding", "name": "Agent 1: Geocoding", "description": "Reverse + forward geocoding (Google/OSM/interpolation)"},
    {"id": "agent1_address_validator", "name": "Agent 2: Address Validation", "description": "Validates addresses via Smarty + Melissa, extracts coordinates"},
    {"id": "agent3_parcel", "name": "Agent 3: Parcel & Land Use", "description": "Parcel and land-use lookup"},
    {"id": "agent4_building", "name": "Agent 4: Building", "description": "Building footprint enrichment before final synthesis"},
    {"id": "agent5_0_offline_ocr", "name": "Agent 5-0: Offline OCR", "description": "Local Ollama vision OCR + PaddleOCR fallback for low-confidence rows"},
    {"id": "agent5_streetview", "name": "Agent 5: Street View", "description": "Imagery and house-number analysis"},
    {"id": "agent6_final", "name": "Agent 6: FTTH Final", "description": "Final FTTH suitability synthesis"},
    {"id": "agent7_neighborhood_discovery", "name": "Agent 7: Neighborhood Discovery", "description": "Finds polygon addresses missing from the Final address set"},
]


def _new_agents(total_records: int) -> list[dict[str, Any]]:
    return [
        {
            "agent_id": agent["id"],
            "agent_name": agent["name"],
            "description": agent["description"],
            "status": "pending",
            "progress": 0,
            "records_processed": 0,
            "records_total": total_records,
            "started_at": None,
            "completed_at": None,
            "errors": [],
        }
        for agent in _AGENT_DEFINITIONS
    ]


def _get_redis():
    """Return a Redis client, importing lazily to avoid import-time failures."""
    import redis as _redis
    from data_ingestion.config.settings import get_settings
    return _redis.from_url(get_settings().redis_url, decode_responses=True)


class _PipelineTask(Task):
    """Base task with Redis client reuse across calls in the same worker."""

    _redis = None

    @property
    def redis(self):
        if self._redis is None:
            self._redis = _get_redis()
        return self._redis


@celery_app.task(
    bind=True,
    base=_PipelineTask,
    name="ftth.run_pipeline",
    max_retries=3,
    default_retry_delay=10,
    acks_late=True,
)
def run_pipeline_task(
    self: _PipelineTask,
    job_id: str,
    total_records: int,
    agent2_options_json: str | None = None,
    pipeline_options_json: str | None = None,
) -> dict[str, Any]:
    """
    Execute the full FTTH pipeline for *job_id*.

    Progress updates are written atomically to a Redis hash and published on a
    pub/sub channel so the API server's WebSocket endpoint can stream them.
    """
    redis = self.redis
    progress_key = _PROGRESS_KEY.format(job_id=job_id)
    channel = _PROGRESS_CHANNEL.format(job_id=job_id)
    logger.info(
        "Pipeline task start | job_id=%s | total_records=%s | task_id=%s | pid=%s",
        job_id,
        total_records,
        getattr(self.request, "id", None),
        os.getpid(),
    )
    if pipeline_options_json:
        pipeline_options = normalize_pipeline_options(json.loads(pipeline_options_json))
    elif agent2_options_json:
        pipeline_options = normalize_pipeline_options({"agent2": json.loads(agent2_options_json)})
    else:
        pipeline_options = normalize_pipeline_options(None)

    def _set_job_status(status: IngestionStatus, error_message: str | None = None) -> None:
        try:
            with session_scope() as session:
                IngestionRepository(session).update_job_status(UUID(job_id), status, error_message)
        except Exception as exc:
            logger.warning("DB job status update failed for %s: %s", job_id, exc)

    # ── helpers ──────────────────────────────────────────────────────────────
    def _publish(payload: dict) -> None:
        try:
            redis.publish(channel, json.dumps(payload))
        except Exception as exc:
            logger.warning("Redis publish failed: %s", exc)

    def _set_progress(mapping: dict) -> None:
        try:
            redis.hset(progress_key, mapping=mapping)
            redis.expire(progress_key, _TTL_SECONDS)
        except Exception as exc:
            logger.warning("Redis hset failed: %s", exc)

    def _load_agents() -> list[dict[str, Any]]:
        try:
            raw = redis.hget(progress_key, "agents")
            if raw:
                loaded = json.loads(raw)
                if isinstance(loaded, list) and loaded:
                    return loaded
        except Exception as exc:
            logger.warning("Redis agent progress load failed: %s", exc)
        return _new_agents(total_records)

    def _save_agents(agents: list[dict[str, Any]], current_idx: int | None = None) -> None:
        mapping: dict[str, Any] = {"agents": json.dumps(agents)}
        if current_idx is not None:
            mapping["current_agent_idx"] = str(current_idx)
        _set_progress(mapping)

    def _stamp() -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # ── progress callback forwarded from pipeline_runner ─────────────────────
    def _progress_cb(done: int, total: int, stage_name: str | None = None,
                     stage_done: int = 0, stage_total: int = 1) -> None:
        pct = int((done / max(total, 1)) * 100)
        current_idx = None
        agents = _load_agents()
        current_idx = apply_stage_progress(
            agents,
            stage_name=stage_name,
            stage_done=stage_done,
            stage_total=stage_total,
            total_records=total_records,
        )
        current_agent = None
        if current_idx is not None and 0 <= current_idx < len(agents):
            current_agent = agents[current_idx].get("agent_id")

        payload = {
            "job_id": job_id,
            "overall_progress": min(pct, 99),
            "status": "processing",
            "current_agent": current_agent,
            "stage_name": stage_name or "",
            "stage_done": stage_done,
            "stage_total": stage_total,
            "agents": agents,
        }
        _set_progress({
            "overall_progress": min(pct, 99),
            "status": "processing",
            "current_stage": stage_name or "",
            "current_agent_idx": str(current_idx if current_idx is not None else redis.hget(progress_key, "current_agent_idx") or 0),
            "agents": json.dumps(agents),
        })
        _publish(payload)
        # Also update Celery task state so Flower shows progress
        try:
            self.update_state(
                state="PROGRESS",
                meta={"pct": min(pct, 99), "stage": stage_name},
            )
        except Exception as exc:
            logger.debug("Celery task-state update skipped: %s", exc)

    # ── run pipeline ─────────────────────────────────────────────────────────
    logger.info("Celery task run_pipeline_task started: job_id=%s", job_id)
    _set_job_status(IngestionStatus.PROCESSING)
    agents = _load_agents()
    started_at = _stamp()
    _set_progress({
        "status": "processing",
        "overall_progress": 0,
        "job_id": job_id,
        "total_records": str(total_records),
        "current_agent_idx": "0",
        "pipeline_started_at": started_at,
        "pipeline_completed_at": "",
        "agents": json.dumps(agents),
        "output_csv": "",
        "output_kmz": "",
    })
    _publish({"job_id": job_id, "status": "processing", "overall_progress": 0})

    try:
        summary = run_full_pipeline(
            job_id,
            progress_callback=_progress_cb,
            pipeline_options=pipeline_options,
        )
        completed_agents = _load_agents()
        finalize_agents_from_results(
            completed_agents,
            summary,
            total_records=total_records,
        )
        now = _stamp()
        for agent in completed_agents:
            if agent.get("status") != "completed":
                agent["status"] = "completed"
                agent["progress"] = 100
                agent["records_processed"] = agent.get("records_total") or total_records
                agent["completed_at"] = agent.get("completed_at") or now

        _set_progress({
            "status": "completed",
            "overall_progress": 100,
            "current_agent_idx": str(max(len(completed_agents) - 1, 0)),
            "pipeline_completed_at": now,
            "agents": json.dumps(completed_agents),
            "output_csv": f"/api/export/csv?job_id={job_id}",
            "output_kmz": f"/api/export/kmz?job_id={job_id}",
            "summary": json.dumps(summary),
        })
        _publish({
            "job_id": job_id,
            "status": "completed",
            "overall_progress": 100,
            "agents": completed_agents,
            "done": True,
            "output_csv": f"/api/export/csv?job_id={job_id}",
            "output_kmz": f"/api/export/kmz?job_id={job_id}",
        })
        _set_job_status(IngestionStatus.COMPLETED)
        logger.info("Celery task run_pipeline_task completed: job_id=%s", job_id)
        return summary

    except Exception as exc:
        logger.exception("Pipeline failed for job_id=%s: %s", job_id, exc)
        _set_job_status(IngestionStatus.FAILED, str(exc))
        failed_agents = _load_agents()
        current_raw = redis.hget(progress_key, "current_agent_idx") or "0"
        try:
            current_idx = int(current_raw)
        except (TypeError, ValueError):
            current_idx = 0
        mark_current_agent_failed(failed_agents, current_idx, str(exc))
        failed_at = _stamp()
        _set_progress({"status": "failed", "error": str(exc), "pipeline_completed_at": failed_at, "agents": json.dumps(failed_agents)})
        _publish({"job_id": job_id, "status": "failed", "error": str(exc), "agents": failed_agents, "done": True})
        raise self.retry(exc=exc)

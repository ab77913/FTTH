"""
Redis-backed progress store.

Replaces the in-memory ``_agent_progress`` dict in api_server.py.
Each job's progress is stored as a Redis hash under the key::

    ftth:job:progress:{job_id}

Live updates are published on the channel::

    ftth:progress:{job_id}

Falls back gracefully to an in-process dict when Redis is unavailable.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

# ── Redis key / channel templates ─────────────────────────────────────────────
_HASH_KEY = "ftth:job:progress:{job_id}"
_CHANNEL = "ftth:progress:{job_id}"
_TTL = 86_400  # 24 h in seconds


class ProgressStore:
    """Thread-safe progress store backed by Redis with in-memory fallback."""

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url
        self._client = None
        self._fallback: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._available = False
        self._connect()

    # ── Connection ────────────────────────────────────────────────────────────

    def _connect(self) -> None:
        try:
            import redis as _redis
            client = _redis.from_url(self._redis_url, decode_responses=True, socket_connect_timeout=2)
            client.ping()
            self._client = client
            self._available = True
            logger.info("ProgressStore: connected to Redis at %s", self._redis_url)
        except Exception as exc:
            logger.warning(
                "ProgressStore: Redis unavailable (%s); using in-memory fallback. "
                "Start Redis to enable cross-process progress and WebSocket streaming.",
                exc,
            )
            self._available = False

    @property
    def redis_available(self) -> bool:
        return self._available

    def _try_redis(self, fn, *args, **kwargs):
        """Execute *fn* on the Redis client; on any error fall through."""
        if not self._available or self._client is None:
            return None
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            logger.debug("Redis op failed (%s); continuing with fallback", exc)
            return None

    # ── Public API ────────────────────────────────────────────────────────────

    def init_job(
        self,
        job_id: str,
        agent_definitions: list[dict],
        total_records: int,
        agent2_options: dict[str, bool] | None = None,
        pipeline_options: dict[str, dict[str, bool]] | None = None,
    ) -> None:
        """Initialise progress state for a new job run."""
        from data_ingestion.utils.pipeline_options import normalize_pipeline_options

        raw = pipeline_options
        if raw is None and agent2_options is not None:
            raw = {"agent2": agent2_options}
        opts = normalize_pipeline_options(raw)
        data: dict[str, Any] = {
            "job_id": job_id,
            "status": "processing",
            "overall_progress": 0,
            "current_agent_idx": 0,
            "total_records": total_records,
            "pipeline_options": opts,
            "pipeline_started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "pipeline_completed_at": None,
            "agents": [
                {
                    "agent_id": a["id"],
                    "agent_name": a["name"],
                    "description": a["description"],
                    "status": "pending",
                    "progress": 0,
                    "records_processed": 0,
                    "records_total": total_records,
                    "started_at": None,
                    "completed_at": None,
                    "errors": [],
                }
                for a in agent_definitions
            ],
            "output_csv": None,
            "output_kmz": None,
        }
        # In-memory fallback
        with self._lock:
            self._fallback[job_id] = data

        # Redis (serialise agents list as JSON string inside the hash)
        flat = {
            "status": "processing",
            "overall_progress": "0",
            "current_agent_idx": "0",
            "total_records": str(total_records),
            "pipeline_started_at": data["pipeline_started_at"] or "",
            "pipeline_completed_at": "",
            "agents": json.dumps(data["agents"]),
            "pipeline_options": json.dumps(opts),
            "agent2_options": json.dumps(opts.get("agent2", {})),
            "output_csv": "",
            "output_kmz": "",
        }
        key = _HASH_KEY.format(job_id=job_id)
        self._try_redis(lambda: (
            self._client.hset(key, mapping=flat),  # type: ignore[union-attr]
            self._client.expire(key, _TTL),         # type: ignore[union-attr]
        ))

    def get(self, job_id: str) -> dict | None:
        """Return the current progress dict for *job_id*, or ``None``."""
        # Prefer in-memory since api_server _run_agents mutates it directly
        with self._lock:
            local = self._fallback.get(job_id)
            if local is not None:
                return local

        # Try Redis (job may have been started by a Celery worker)
        key = _HASH_KEY.format(job_id=job_id)
        raw = self._try_redis(lambda: self._client.hgetall(key))  # type: ignore[union-attr]
        if raw:
            try:
                agents = json.loads(raw.get("agents", "[]"))
                return {
                    "job_id": job_id,
                    "status": raw.get("status", "unknown"),
                    "overall_progress": int(raw.get("overall_progress", 0)),
                    "current_agent_idx": int(raw.get("current_agent_idx", 0)),
                    "total_records": int(raw.get("total_records", 0)),
                    "pipeline_started_at": raw.get("pipeline_started_at") or None,
                    "pipeline_completed_at": raw.get("pipeline_completed_at") or None,
                    "agents": agents,
                    "pipeline_options": json.loads(raw.get("pipeline_options", "{}") or "{}"),
                    "agent2_options": json.loads(raw.get("agent2_options", "{}") or "{}"),
                    "output_csv": raw.get("output_csv") or None,
                    "output_kmz": raw.get("output_kmz") or None,
                }
            except Exception:
                pass
        return None

    def set_status(self, job_id: str, status: str, **extra) -> None:
        """Update top-level status fields (e.g. status=completed, output_csv=...)."""
        completed_at = None
        if status in ("completed", "failed"):
            completed_at = extra.get("pipeline_completed_at") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            extra = {**extra, "pipeline_completed_at": completed_at}

        with self._lock:
            local = self._fallback.get(job_id)
            if local is not None:
                local["status"] = status
                local.update(extra)

        key = _HASH_KEY.format(job_id=job_id)
        mapping = {"status": status}
        if completed_at:
            mapping["pipeline_completed_at"] = completed_at
        for k, v in extra.items():
            mapping[k] = str(v) if v is not None else ""
        self._try_redis(lambda: (
            self._client.hset(key, mapping=mapping),  # type: ignore[union-attr]
            self._client.expire(key, _TTL),            # type: ignore[union-attr]
        ))

    def is_processing(self, job_id: str) -> bool:
        p = self.get(job_id)
        return bool(p and p.get("status") == "processing")

    def publish(self, job_id: str, payload: dict) -> None:
        """Publish a payload to the job's Redis pub/sub channel (no-op if Redis is down)."""
        channel = _CHANNEL.format(job_id=job_id)
        message = json.dumps(payload)
        self._try_redis(lambda: self._client.publish(channel, message))  # type: ignore[union-attr]

    def clear_local(self, job_id: str) -> None:
        """Remove the in-memory entry for *job_id* so get() reads from Redis.

        Call this after dispatching to a Celery worker so that progress
        updates written to Redis by the worker are visible to get().
        """
        with self._lock:
            self._fallback.pop(job_id, None)

    def subscribe(self, job_id: str):
        """Return a Redis PubSub subscription for *job_id*'s channel, or None."""
        if not self._available or self._client is None:
            return None
        try:
            pubsub = self._client.pubsub()
            pubsub.subscribe(_CHANNEL.format(job_id=job_id))
            return pubsub
        except Exception as exc:
            logger.warning("Could not subscribe to Redis channel: %s", exc)
            return None


# ── Module-level singleton (initialised lazily on first import in api_server) ─
_store: ProgressStore | None = None
_store_lock = threading.Lock()


def get_progress_store(redis_url: str | None = None) -> ProgressStore:
    global _store
    with _store_lock:
        if _store is None:
            if redis_url is None:
                from data_ingestion.config.settings import get_settings
                redis_url = get_settings().redis_url
            _store = ProgressStore(redis_url)
    return _store

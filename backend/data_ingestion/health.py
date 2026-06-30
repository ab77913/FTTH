"""Liveness and readiness probes for the FTTH API."""

from __future__ import annotations

import os
import socket
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import text

from data_ingestion.database.db import get_engine

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def liveness_payload() -> dict[str, Any]:
    return {"status": "ok", "service": "ftth-api"}


def _check_writable_directory(path: Path) -> str:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".readiness-check"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return "ok"
    except Exception as exc:
        return f"error: {exc.__class__.__name__}"


def _check_tcp_url(url: str, *, default_port: int, timeout: float = 2.0) -> str:
    parsed = urlparse(url)
    host = parsed.hostname
    port = parsed.port or default_port
    if not host:
        return "error: invalid-url"
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return "ok"
    except Exception as exc:
        return f"error: {exc.__class__.__name__}"


def readiness_payload() -> dict[str, Any]:
    checks: dict[str, str] = {
        "database": "unknown",
        "redis": "skipped",
        "rabbitmq": "skipped",
        "storage:data": "unknown",
        "storage:uploads": "unknown",
        "storage:logs": "unknown",
    }
    ready = True

    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = f"error: {exc.__class__.__name__}"
        ready = False

    for name, path in {
        "storage:data": PROJECT_ROOT / "data",
        "storage:uploads": PROJECT_ROOT / "uploads",
        "storage:logs": PROJECT_ROOT / "logs",
    }.items():
        checks[name] = _check_writable_directory(path)
        if checks[name] != "ok":
            ready = False

    redis_url = os.environ.get("REDIS_URL", "").strip()
    if redis_url:
        try:
            import redis

            client = redis.from_url(redis_url, socket_connect_timeout=2, socket_timeout=2)
            client.ping()
            checks["redis"] = "ok"
        except Exception as exc:
            checks["redis"] = f"error: {exc.__class__.__name__}"
            # Redis is optional for in-memory progress fallback.
            if os.environ.get("FTTH_REQUIRE_REDIS", "").strip().lower() in {"1", "true", "yes"}:
                ready = False

    rabbitmq_url = os.environ.get("RABBITMQ_URL", "").strip()
    if rabbitmq_url:
        checks["rabbitmq"] = _check_tcp_url(rabbitmq_url, default_port=5672)
        if checks["rabbitmq"] != "ok" and os.environ.get("FTTH_REQUIRE_RABBITMQ", "").strip().lower() in {"1", "true", "yes"}:
            ready = False
    elif os.environ.get("FTTH_REQUIRE_RABBITMQ", "").strip().lower() in {"1", "true", "yes"}:
        checks["rabbitmq"] = "error: missing-url"
        ready = False

    return {
        "status": "ready" if ready else "degraded",
        "service": "ftth-api",
        "checks": checks,
        "ready": ready,
    }
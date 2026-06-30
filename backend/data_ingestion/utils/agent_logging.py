"""Shared per-agent file logging helpers.

Each agent can opt into a dedicated log file under ``logs/`` while using the
same redaction and payload preview rules.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any

from data_ingestion.config.log_paths import agent_log_path as default_agent_log_path, ensure_log_dirs
from data_ingestion.config.logging_setup import FlushingFileHandler
from data_ingestion.config.paths import PROJECT_ROOT

_PROJECT_ROOT = PROJECT_ROOT
_LOCK = threading.Lock()
_CONFIGURED: dict[tuple[str, int], Path] = {}

_SECRET_KEY_RE = re.compile(
    r"(?i)(key|api[-_]?key|authorization|subscription[-_]?key|token|secret|password|apikey)$"
)
_SECRET_TEXT_RE = re.compile(
    r"(?i)(key|api[-_]?key|authorization|subscription[-_]?key|token|secret|password|apikey)"
    r"(['\"]?\s*[=:]\s*['\"]?|=)([^&\s,'\"]+)"
)


def agent_logs_enabled(agent_id: str) -> bool:
    specific = os.environ.get(f"FTTH_{agent_id.upper()}_LOG")
    if specific is not None:
        return specific.lower() not in {"0", "false", "no", "off"}
    return os.environ.get("FTTH_AGENT_LOGS", "1").lower() not in {"0", "false", "no", "off"}


def payload_logs_enabled() -> bool:
    return os.environ.get("FTTH_AGENT_LOG_PAYLOADS", "1").lower() not in {
        "0", "false", "no", "off",
    }


def agent_log_path(agent_id: str) -> Path:
    env_key = f"FTTH_{agent_id.upper()}_LOG_FILE"
    raw = os.environ.get(env_key, "").strip()
    if raw:
        path = Path(raw)
        if not path.is_absolute():
            path = _PROJECT_ROOT / path
        return path.resolve()
    ensure_log_dirs()
    return default_agent_log_path(agent_id)


def configure_agent_logger(
    logger: logging.Logger,
    agent_id: str,
    *,
    include_console: bool = False,
) -> Path | None:
    """Attach a per-agent file handler once per process."""
    if not agent_logs_enabled(agent_id):
        return None
    pid = os.getpid()
    key = (agent_id, pid)
    with _LOCK:
        if key in _CONFIGURED:
            return _CONFIGURED[key]

        path = agent_log_path(agent_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        logger.setLevel(logging.DEBUG)
        logger.propagate = include_console
        fmt = logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler = FlushingFileHandler(path, encoding="utf-8")
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(fmt)
        if not any(
            isinstance(existing, logging.FileHandler)
            and getattr(existing, "baseFilename", "") == str(path)
            for existing in logger.handlers
        ):
            logger.addHandler(handler)
        _CONFIGURED[key] = path
        logger.info("Log file ready: %s | agent_id=%s | pid=%s", path, agent_id, pid)
        return path


def redact(value: Any) -> Any:
    """Return a copy with obvious secrets redacted."""
    if isinstance(value, dict):
        out: dict[Any, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            out[key] = "<redacted>" if _SECRET_KEY_RE.search(key_text) else redact(item)
        return out
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact(item) for item in value)
    if isinstance(value, str):
        return _SECRET_TEXT_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}<redacted>", value)
    return value


def preview(value: Any, *, limit: int | None = None) -> str:
    limit = limit if limit is not None else _preview_limit()
    if isinstance(value, (bytes, bytearray)):
        return f"<{len(value)} bytes>"
    safe = redact(value)
    try:
        text = json.dumps(safe, default=str, ensure_ascii=False)
    except Exception:
        text = str(safe)
    if len(text) > limit:
        return f"{text[:limit]}... [truncated {len(text) - limit} chars]"
    return text


def log_payload(
    logger: logging.Logger,
    label: str,
    payload: Any,
    *,
    level: int = logging.INFO,
    limit: int | None = None,
) -> None:
    if payload_logs_enabled():
        logger.log(level, "%s: %s", label, preview(payload, limit=limit))


def log_api_call(
    logger: logging.Logger,
    service: str,
    *,
    request: Any = None,
    response: Any = None,
    status: Any = None,
    extra: Any = None,
) -> None:
    if not payload_logs_enabled():
        return
    parts = [f"API {service}"]
    if status is not None:
        parts.append(f"status={status}")
    if request is not None:
        parts.append(f"request={preview(request)}")
    if response is not None:
        parts.append(f"response={preview(response)}")
    if extra is not None:
        parts.append(f"extra={preview(extra)}")
    logger.info(" | ".join(parts))


def _preview_limit() -> int:
    try:
        return max(500, int(os.environ.get("FTTH_AGENT_LOG_MAXLEN", "8000")))
    except (TypeError, ValueError):
        return 8000

"""File logging for Agent 5 (Street View + Azure Vision)."""
from __future__ import annotations
from data_ingestion.config.log_paths import agent_log_path as _default_agent_log_path, ensure_log_dirs
from data_ingestion.config.logging_setup import FlushingFileHandler
from data_ingestion.config.paths import PROJECT_ROOT

import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any

_PROJECT_ROOT = PROJECT_ROOT

_AGENT5_LOGGER_NAMES = (
    "data_ingestion.agents.agent5_streetview",
    "data_ingestion.utils.agent5_vision",
    "data_ingestion.utils.agent5_paddle_ocr",
    "data_ingestion.utils.agent5_image_utils",
    "data_ingestion.utils.agent5_metadata",
    "data_ingestion.utils.agent5_gpt_vision",
    "data_ingestion.utils.agent5_iterative_search",
    "data_ingestion.utils.agent5_input",
    "data_ingestion.utils.agent5_paddleocr_scan",
    "data_ingestion.utils.agent5_house_numbers_output",
)

# Patterns whose values must be redacted before anything reaches the log file.
_SECRET_RE = re.compile(
    r"(?i)(key|api[-_]?key|authorization|subscription[-_]?key|token|secret)"
    r"(['\"]?\s*[=:]\s*['\"]?|=)([^&\s'\"]+)"
)
_DEFAULT_PREVIEW_LIMIT = 6000

_LOG_CONFIGURED = False
_LOG_CONFIGURED_PID = -1
_LOG_LOCK = threading.Lock()
_SHARED_HANDLER: logging.FileHandler | None = None


def agent5_log_file() -> Path:
    raw = os.environ.get("FTTH_AGENT5_LOG_FILE", "").strip()
    if raw:
        path = Path(raw)
        if not path.is_absolute():
            path = _PROJECT_ROOT / path
        return path.resolve()
    ensure_log_dirs()
    return _default_agent_log_path("agent5_streetview")


def agent5_logging_enabled() -> bool:
    return os.environ.get("FTTH_AGENT5_LOG", "1").lower() not in ("0", "false", "no", "off")


def log_payloads_enabled() -> bool:
    """Whether full request/response payloads are written to the Agent 5 log."""
    return os.environ.get("FTTH_AGENT5_LOG_PAYLOADS", "1").lower() not in (
        "0", "false", "no", "off",
    )


def _preview_limit() -> int:
    try:
        return int(os.environ.get("FTTH_AGENT5_LOG_MAXLEN", _DEFAULT_PREVIEW_LIMIT))
    except (TypeError, ValueError):
        return _DEFAULT_PREVIEW_LIMIT


def redact(text: Any) -> str:
    """Mask API keys / tokens / authorization values in a string."""
    return _SECRET_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}<redacted>", str(text))


def preview(obj: Any, *, limit: int | None = None) -> str:
    """Render an object for logging: JSON when possible, secrets redacted, truncated."""
    limit = _preview_limit() if limit is None else limit
    if isinstance(obj, (bytes, bytearray)):
        return f"<{len(obj)} bytes>"
    if isinstance(obj, (dict, list, tuple)):
        try:
            text = json.dumps(obj, default=_json_default, ensure_ascii=False)
        except Exception:
            text = repr(obj)
    else:
        text = str(obj)
    text = redact(text)
    if len(text) > limit:
        text = f"{text[:limit]}... [truncated {len(text) - limit} chars]"
    return text


def _json_default(value: Any) -> str:
    if isinstance(value, (bytes, bytearray)):
        return f"<{len(value)} bytes>"
    return str(value)


def log_api_call(
    logger: logging.Logger,
    service: str,
    *,
    request: Any = None,
    response: Any = None,
    status: Any = None,
    extra: Any = None,
) -> None:
    """Emit a single structured DEBUG line capturing an external API exchange."""
    if not log_payloads_enabled():
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
    logger.debug(" | ".join(parts))


def configure_agent5_file_logging() -> Path | None:
    """Attach a shared file handler to all Agent 5 loggers (once per process)."""
    global _LOG_CONFIGURED, _LOG_CONFIGURED_PID, _SHARED_HANDLER
    if not agent5_logging_enabled():
        return None

    current_pid = os.getpid()
    with _LOG_LOCK:
        if _LOG_CONFIGURED and _LOG_CONFIGURED_PID == current_pid:
            return agent5_log_file()

        log_file = agent5_log_file()
        log_file.parent.mkdir(parents=True, exist_ok=True)
        fmt = logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler = FlushingFileHandler(log_file, encoding="utf-8")
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(fmt)
        _SHARED_HANDLER = handler

        for logger_name in _AGENT5_LOGGER_NAMES:
            agent_logger = logging.getLogger(logger_name)
            agent_logger.setLevel(logging.DEBUG)
            agent_logger.propagate = False
            if not any(
                isinstance(existing, logging.FileHandler)
                and getattr(existing, "baseFilename", "") == handler.baseFilename
                for existing in agent_logger.handlers
            ):
                agent_logger.addHandler(handler)

        _LOG_CONFIGURED = True
        _LOG_CONFIGURED_PID = current_pid
        logging.getLogger("ppocr").setLevel(logging.ERROR)
        return log_file

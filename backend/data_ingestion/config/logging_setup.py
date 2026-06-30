"""Configure application logging (plain or JSON) with per-service file logs."""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone

from data_ingestion.config.log_paths import (
    SERVICE_LOGGER_MAP,
    ensure_log_dirs,
    service_log_path,
)

_SERVICE_LOCK = threading.Lock()
_SERVICE_CONFIGURED = False
_SERVICE_HANDLERS: dict[str, logging.Handler] = {}


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in ("request_id", "method", "path", "status_code", "duration_ms"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class FlushingFileHandler(logging.FileHandler):
    """File handler that flushes after each record (Windows-safe for shared logs)."""

    def emit(self, record: logging.LogRecord) -> None:
        super().emit(record)
        self.flush()


def _service_handler(service_key: str) -> logging.Handler | None:
    if service_key in _SERVICE_HANDLERS:
        return _SERVICE_HANDLERS[service_key]
    path = service_log_path(service_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        handler = FlushingFileHandler(path, encoding="utf-8")
    except PermissionError:
        fallback = path.with_name(f"{path.stem}.{os.getpid()}{path.suffix}")
        try:
            handler = FlushingFileHandler(fallback, encoding="utf-8")
        except PermissionError:
            logging.getLogger(__name__).warning(
                "Could not open service log file for %s (locked); using console only",
                service_key,
            )
            return None
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    _SERVICE_HANDLERS[service_key] = handler
    return handler


def configure_service_file_loggers(*, force: bool = False) -> None:
    """Attach dedicated file handlers for infrastructure / service loggers."""
    global _SERVICE_CONFIGURED
    with _SERVICE_LOCK:
        if _SERVICE_CONFIGURED and not force:
            return
        ensure_log_dirs()
        for logger_name, service_key in SERVICE_LOGGER_MAP.items():
            service_logger = logging.getLogger(logger_name)
            service_logger.setLevel(logging.DEBUG)
            handler = _service_handler(service_key)
            if handler is None:
                continue
            if not any(
                isinstance(existing, logging.FileHandler)
                and getattr(existing, "baseFilename", "") == handler.baseFilename
                for existing in service_logger.handlers
            ):
                service_logger.addHandler(handler)
            # Keep console/root visibility except noisy access logs.
            service_logger.propagate = logger_name != "ftth.access"
            service_logger.info("Service logger ready | service=%s | path=%s", service_key, handler.baseFilename)
        _SERVICE_CONFIGURED = True


def configure_logging() -> None:
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    use_json = os.environ.get("FTTH_LOG_JSON", "").strip().lower() in {"1", "true", "yes"}

    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler()
    if use_json:
        handler.setFormatter(JsonLogFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
        )
    root.addHandler(handler)
    root.setLevel(level)

    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    configure_service_file_loggers()

"""Logging configuration tests."""

import logging

from data_ingestion.config.logging_setup import JsonLogFormatter, configure_logging


def test_json_formatter_includes_message():
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )
    record.request_id = "abc-123"
    payload = JsonLogFormatter().format(record)
    assert "hello" in payload
    assert "abc-123" in payload


def test_configure_logging_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "data_ingestion.config.log_paths.LOG_SERVICES_DIR",
        tmp_path / "services",
    )
    from data_ingestion.config import logging_setup

    logging_setup._SERVICE_CONFIGURED = False
    logging_setup._SERVICE_HANDLERS.clear()
    configure_logging()
    assert logging.getLogger().level <= logging.INFO
    access_log = tmp_path / "services" / "access.log"
    logging.getLogger("ftth.access").info("access test line")
    for handler in logging.getLogger("ftth.access").handlers:
        handler.flush()
    assert access_log.exists()
    assert "access test line" in access_log.read_text(encoding="utf-8")

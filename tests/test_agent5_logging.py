"""Tests for Agent 5 file logging."""
from __future__ import annotations

import logging
from pathlib import Path

from data_ingestion.utils.agent5_logging import configure_agent5_file_logging


def test_configure_agent5_file_logging_writes_to_file(tmp_path: Path, monkeypatch) -> None:
    import data_ingestion.utils.agent5_logging as mod

    log_file = tmp_path / "agent5_test.log"
    monkeypatch.setenv("FTTH_AGENT5_LOG_FILE", str(log_file))
    monkeypatch.setenv("FTTH_AGENT5_LOG", "1")
    mod._LOG_CONFIGURED = False
    mod._LOG_CONFIGURED_PID = -1

    configure_agent5_file_logging()
    logger = logging.getLogger("data_ingestion.utils.agent5_vision")
    logger.info("agent5 logging test message")
    for handler in logger.handlers:
        handler.flush()

    assert log_file.exists()
    content = log_file.read_text(encoding="utf-8")
    assert "agent5 logging test message" in content

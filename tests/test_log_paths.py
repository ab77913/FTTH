"""Tests for canonical log path registry."""

from pathlib import Path

from data_ingestion.config.log_paths import (
    AGENT_LOG_IDS,
    SERVICE_LOG_FILES,
    agent_log_path,
    all_expected_logs,
    ensure_log_dirs,
    service_log_path,
)
from data_ingestion.utils.agent_logging import configure_agent_logger


def test_agent_log_paths_under_agents_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "data_ingestion.config.log_paths.LOG_AGENTS_DIR",
        tmp_path / "agents",
    )
    monkeypatch.setattr(
        "data_ingestion.config.log_paths.LOG_ROOT",
        tmp_path,
    )
    path = agent_log_path("agent4_building")
    assert path.parent.name == "agents"
    assert path.name == "agent4_building.log"


def test_service_log_paths_under_services_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "data_ingestion.config.log_paths.LOG_SERVICES_DIR",
        tmp_path / "services",
    )
    path = service_log_path("pipeline_tasks")
    assert path.parent.name == "services"
    assert path.name == "pipeline_tasks.log"


def test_all_expected_logs_covers_agents_and_services():
    expected = all_expected_logs()
    assert len(expected) == len(AGENT_LOG_IDS) + len(SERVICE_LOG_FILES)
    assert "agent:agent4_building" in expected
    assert "service:pipeline_tasks" in expected


def test_configure_agent_logger_writes_detailed_line(tmp_path, monkeypatch):
    agents_dir = tmp_path / "agents"
    monkeypatch.setattr("data_ingestion.config.log_paths.LOG_AGENTS_DIR", agents_dir)
    monkeypatch.setattr("data_ingestion.utils.agent_logging._CONFIGURED", {})
    monkeypatch.setenv("FTTH_AGENT_LOGS", "1")

    import logging

    test_logger = logging.getLogger("tests.agent4")
    test_logger.handlers.clear()
    path = configure_agent_logger(test_logger, "agent4_building")
    assert path is not None
    test_logger.info("agent4 unit test log line")
    for handler in test_logger.handlers:
        handler.flush()

    content = Path(path).read_text(encoding="utf-8")
    assert "Log file ready" in content
    assert "agent4 unit test log line" in content


def test_ensure_log_dirs_creates_structure(tmp_path, monkeypatch):
    monkeypatch.setattr("data_ingestion.config.log_paths.LOG_ROOT", tmp_path)
    monkeypatch.setattr("data_ingestion.config.log_paths.LOG_AGENTS_DIR", tmp_path / "agents")
    monkeypatch.setattr("data_ingestion.config.log_paths.LOG_SERVICES_DIR", tmp_path / "services")
    ensure_log_dirs()
    assert (tmp_path / "agents").is_dir()
    assert (tmp_path / "services").is_dir()

"""Canonical log file locations for agents and services."""

from __future__ import annotations

from pathlib import Path

from data_ingestion.config.paths import PROJECT_ROOT

LOG_ROOT = PROJECT_ROOT / "logs"
LOG_AGENTS_DIR = LOG_ROOT / "agents"
LOG_SERVICES_DIR = LOG_ROOT / "services"

# Pipeline agent ids (match Celery / DB agent_name values).
AGENT_LOG_IDS: tuple[str, ...] = (
    "pipeline_runner",
    "agent0_house_discovery",
    "reverse_geocoder",
    "agent1_address_validator",
    "agent2_geocoding",
    "agent3_parcel",
    "agent4_building",
    "agent5_0_offline_ocr",
    "agent5_streetview",
    "agent6_final",
    "agent7_neighborhood_discovery",
)

# Service log filenames under logs/services/.
SERVICE_LOG_FILES: dict[str, str] = {
    "api_server": "api_server.log",
    "access": "access.log",
    "celery_worker": "celery_worker.log",
    "pipeline_tasks": "pipeline_tasks.log",
    "ingestion": "ingestion.log",
    "messaging": "messaging.log",
    "redis": "redis.log",
    "ollama_chat": "ollama_chat.log",
    "ollama_warmup": "ollama_warmup.log",
    "dispatcher": "dispatcher.log",
}

# Logger name -> service log key (multiple loggers may share one file).
SERVICE_LOGGER_MAP: dict[str, str] = {
    "ftth.access": "access",
    "data_ingestion.ingestion_service": "ingestion",
    "data_ingestion.worker.tasks": "pipeline_tasks",
    "data_ingestion.worker.celery_app": "pipeline_tasks",
    "data_ingestion.messaging.rabbitmq_client": "messaging",
    "data_ingestion.messaging.agent_bus": "messaging",
    "data_ingestion.utils.redis_progress": "redis",
    "data_ingestion.utils.redis_cache": "redis",
    "data_ingestion.utils.ollama_chat_router": "ollama_chat",
    "ftth.ollama_warmup": "ollama_warmup",
    "data_ingestion.dispatcher.sequential_dispatcher": "dispatcher",
    "backend.api.server": "api_server",
}


def ensure_log_dirs() -> None:
    """Create logs/agents and logs/services if missing."""
    LOG_AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    LOG_SERVICES_DIR.mkdir(parents=True, exist_ok=True)


def agent_log_path(agent_id: str) -> Path:
    """Default path for an agent log file (env may override in agent_logging)."""
    return (LOG_AGENTS_DIR / f"{agent_id}.log").resolve()


def service_log_path(service_key: str) -> Path:
    """Path for a service log file."""
    filename = SERVICE_LOG_FILES.get(service_key, f"{service_key}.log")
    return (LOG_SERVICES_DIR / filename).resolve()


def expected_agent_logs() -> dict[str, Path]:
    return {agent_id: agent_log_path(agent_id) for agent_id in AGENT_LOG_IDS}


def expected_service_logs() -> dict[str, Path]:
    return {key: service_log_path(key) for key in SERVICE_LOG_FILES}


def all_expected_logs() -> dict[str, Path]:
    out = {f"agent:{k}": v for k, v in expected_agent_logs().items()}
    out.update({f"service:{k}": v for k, v in expected_service_logs().items()})
    return out

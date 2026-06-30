from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
import logging

from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from data_ingestion.config.settings import get_settings
from data_ingestion.database.models import Base


logger = logging.getLogger(__name__)
_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_engine(
            settings.database_url,
            pool_pre_ping=True,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_timeout=settings.db_pool_timeout,
            future=True,
        )
    return _engine


def dispose_engine() -> None:
    """Release connection pool (graceful shutdown)."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
        _engine = None
        _SessionLocal = None


def get_session_factory() -> sessionmaker[Session]:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), autoflush=False, autocommit=False, future=True)
    return _SessionLocal


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def ensure_address_validation_columns() -> None:
    """Add coord/address validation columns to existing deployments (idempotent)."""
    engine = get_engine()
    required_columns = {
        "source_raw_address": "TEXT",
        "source_latitude": "DOUBLE PRECISION",
        "source_longitude": "DOUBLE PRECISION",
        "validated_raw_address": "TEXT",
        "validated_street_line": "TEXT",
        "validated_postcode": "VARCHAR(32)",
        "validated_city_state": "VARCHAR(255)",
        "validated_country_code": "VARCHAR(8)",
        "validated_latitude": "DOUBLE PRECISION",
        "validated_longitude": "DOUBLE PRECISION",
        "coord_address_match_status": "VARCHAR(32)",
        "coord_address_distance_m": "DOUBLE PRECISION",
        "coord_address_validation_notes": "TEXT",
        "reverse_geocode_confidence_score": "INTEGER",
    }
    with engine.connect() as connection:
        existing_columns = set(
            connection.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'addresses'
                      AND column_name = ANY(:columns)
                    """
                ),
                {"columns": list(required_columns)},
            ).scalars()
        )
        missing_columns = {
            column: column_type
            for column, column_type in required_columns.items()
            if column not in existing_columns
        }
        if not missing_columns:
            return

    for column, column_type in missing_columns.items():
        try:
            with engine.begin() as connection:
                connection.execute(text("SET LOCAL lock_timeout = '3s'"))
                connection.execute(
                    text(f"ALTER TABLE addresses ADD COLUMN IF NOT EXISTS {column} {column_type}")
                )
        except OperationalError as exc:
            if "lock timeout" in str(exc).lower():
                logger.warning(
                    "Skipped startup migration for addresses.%s because the table is locked",
                    column,
                )
                continue
            raise


def ensure_agent1_data_column() -> None:
    """Add data JSON column to agent1_results (idempotent)."""
    engine = get_engine()
    with engine.connect() as conn:
        existing = conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='agent1_results' AND column_name='data'"
        )).scalar()
    if not existing:
        try:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE agent1_results ADD COLUMN IF NOT EXISTS data JSON"))
            logger.info("Added agent1_results.data column")
        except OperationalError as exc:
            if "lock timeout" in str(exc).lower():
                logger.warning("Skipped agent1_results.data migration (table locked)")
            else:
                raise


def ensure_pipeline_flow_tables() -> None:
    """Create pipeline flow template tables if they don't exist, and insert default template."""
    from uuid import uuid4
    from data_ingestion.database.models import PipelineFlowTemplate
    
    engine = get_engine()
    with engine.begin() as connection:
        # Tables will be created by Base.metadata.create_all(), but ensure indices exist
        try:
            connection.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_pipeline_flow_templates_is_active ON pipeline_flow_templates(is_active)"
            ))
            connection.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_job_pipeline_flows_job_id ON job_pipeline_flows(job_id)"
            ))
            connection.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_job_pipeline_flows_template_id ON job_pipeline_flows(template_id)"
            ))
        except OperationalError:
            logger.debug("Pipeline flow indices already exist or table not ready")
    
    # Insert default template if none exists
    with session_scope() as session:
        existing = session.query(PipelineFlowTemplate).filter(
            PipelineFlowTemplate.name == "Default FTTH Pipeline"
        ).first()
        
        if not existing:
            default_flow = {
                "name": "Default FTTH Pipeline",
                "agents": [
                    {
                        "agent_name": "agent0_house_discovery",
                        "enabled": True,
                        "data_source": "database",
                        "confidence_thresholds": {},
                        "pipeline_options": {
                            "enabled": True,
                            "reverse_geocode": True,
                            "grid_step": 0.00025,
                            "max_candidates_per_polygon": 0,
                            "max_candidates_per_job": 0,
                            "dedup_distance_m": 20,
                        },
                        "order": 0
                    },
                    {
                        "agent_name": "agent2_geocoding",
                        "enabled": True,
                        "data_source": "database",
                        "confidence_thresholds": {"threshold_1": 60, "threshold_2": 40},
                        "order": 1
                    },
                    {
                        "agent_name": "agent1_address_validator",
                        "enabled": True,
                        "data_source": "database",
                        "confidence_thresholds": {"threshold_1": 70, "threshold_2": 50},
                        "order": 2
                    },
                    {
                        "agent_name": "agent3_parcel",
                        "enabled": True,
                        "data_source": "agent1_output",
                        "confidence_thresholds": {"threshold_1": 65},
                        "order": 3
                    },
                    {
                        "agent_name": "agent4_building",
                        "enabled": True,
                        "data_source": "database",
                        "confidence_thresholds": {"threshold_1": 60},
                        "order": 4
                    },
                    {
                        "agent_name": "agent5_streetview",
                        "enabled": True,
                        "data_source": "agent4_output",
                        "confidence_thresholds": {"threshold_1": 55},
                        "pipeline_options": {
                            "enabled": True,
                            "analysis_mode": "hybrid",
                            "street_view": True,
                            "satellite_fallback": True,
                            "azure_vision": True,
                            "gpt_vision": True,
                            "llm_provider": "online",
                            "ollama_vision_model": "qwen2.5vl:latest",
                            "fast_mode": True,
                        },
                        "order": 6
                    },
                    {
                        "agent_name": "agent5_0_offline_ocr",
                        "enabled": False,
                        "data_source": "database",
                        "confidence_thresholds": {},
                        "pipeline_options": {
                            "enabled": False,
                            "street_view": True,
                            "paddle_ocr": True,
                            "ocr_engine": "paddle_primary",
                            "ollama_guidance": False,
                            "max_workers": 4,
                            "max_iterations": 5,
                            "confidence_gate": 90,
                        },
                        "order": 5
                    },
                    {
                        "agent_name": "agent6_finalization",
                        "enabled": True,
                        "data_source": "all_agents",
                        "confidence_thresholds": {},
                        "order": 7
                    }
                ]
            }
            template = PipelineFlowTemplate(
                id=uuid4(),
                name="Default FTTH Pipeline",
                description="Standard pipeline: all agents enabled, cascading confidence thresholds",
                is_active=True,
                created_by="system",
                config=default_flow
            )
            session.add(template)
            session.commit()
            logger.info("Default pipeline flow template created")
        else:
            config = dict(existing.config or {})
            agents = config.get("agents") or []
            changed = False
            if not any(
                isinstance(agent, dict) and agent.get("agent_name") == "agent0_house_discovery"
                for agent in agents
            ):
                agents.insert(0, {
                    "agent_name": "agent0_house_discovery",
                    "enabled": True,
                    "data_source": "database",
                    "confidence_thresholds": {},
                    "pipeline_options": {
                        "enabled": True,
                        "reverse_geocode": True,
                        "grid_step": 0.00025,
                        "max_candidates_per_polygon": 0,
                        "max_candidates_per_job": 0,
                        "dedup_distance_m": 20,
                    },
                    "order": 0
                })
                changed = True
            for agent in agents:
                if isinstance(agent, dict) and agent.get("agent_name") == "agent0_house_discovery":
                    pipeline_options = agent.setdefault("pipeline_options", {})
                    if isinstance(pipeline_options, dict):
                        if pipeline_options.get("max_candidates_per_polygon") in (None, 250):
                            pipeline_options["max_candidates_per_polygon"] = 0
                            changed = True
                        if pipeline_options.get("max_candidates_per_job") in (None, 1000):
                            pipeline_options["max_candidates_per_job"] = 0
                            changed = True
                if isinstance(agent, dict) and agent.get("agent_name") == "agent5_streetview":
                    if int(agent.get("order") or 5) < 6:
                        agent["order"] = 6
                        changed = True
                    pipeline_options = agent.setdefault("pipeline_options", {})
                    if isinstance(pipeline_options, dict):
                        defaults = {
                            "enabled": True,
                            "analysis_mode": "hybrid",
                            "street_view": True,
                            "satellite_fallback": True,
                            "azure_vision": True,
                            "gpt_vision": True,
                            "llm_provider": "online",
                            "ollama_vision_model": "qwen2.5vl:latest",
                            "fast_mode": True,
                        }
                        for key, value in defaults.items():
                            if key not in pipeline_options:
                                pipeline_options[key] = value
                                changed = True
                if isinstance(agent, dict) and agent.get("agent_name") == "agent5_0_offline_ocr":
                    pipeline_options = agent.setdefault("pipeline_options", {})
                    if isinstance(pipeline_options, dict):
                        defaults = {
                            "enabled": False,
                            "street_view": True,
                            "paddle_ocr": True,
                            "ocr_engine": "paddle_primary",
                            "ollama_guidance": False,
                            "max_workers": 4,
                            "max_iterations": 5,
                            "confidence_gate": 90,
                        }
                        for key, value in defaults.items():
                            if key not in pipeline_options:
                                pipeline_options[key] = value
                                changed = True
                        if pipeline_options.get("ocr_engine") == "vision_only":
                            pipeline_options["ocr_engine"] = "paddle_primary"
                            changed = True
                        if pipeline_options.get("ocr_engine") == "paddle_primary" and pipeline_options.get("ollama_guidance"):
                            pipeline_options["ollama_guidance"] = False
                            changed = True
                if isinstance(agent, dict) and agent.get("agent_name") == "agent6_finalization":
                    if int(agent.get("order") or 6) < 7:
                        agent["order"] = 7
                        changed = True
                if isinstance(agent, dict) and agent.get("data_source") == "raw_input":
                    agent["data_source"] = "database"
                    changed = True
            if not any(
                isinstance(agent, dict) and agent.get("agent_name") == "agent5_0_offline_ocr"
                for agent in agents
            ):
                agents.append({
                    "agent_name": "agent5_0_offline_ocr",
                    "enabled": False,
                    "data_source": "database",
                    "confidence_thresholds": {},
                    "pipeline_options": {
                        "enabled": False,
                        "street_view": True,
                        "paddle_ocr": True,
                        "ocr_engine": "paddle_primary",
                        "ollama_guidance": False,
                        "max_workers": 4,
                        "max_iterations": 5,
                        "confidence_gate": 90,
                    },
                    "order": 6,
                })
                changed = True
            if changed:
                config["agents"] = agents
                existing.config = config
                session.commit()
                logger.info("Migrated flow template data_source raw_input → database")


def ensure_performance_indexes() -> None:
    """Idempotent indexes for frequent list/sort queries."""
    engine = get_engine()
    statements = (
        "CREATE INDEX IF NOT EXISTS ix_ingestion_jobs_created_at ON ingestion_jobs (created_at DESC)",
        "CREATE INDEX IF NOT EXISTS ix_ingestion_jobs_status ON ingestion_jobs (status)",
    )
    with engine.begin() as connection:
        for stmt in statements:
            try:
                connection.execute(text(stmt))
            except OperationalError as exc:
                logger.warning("Skipped index creation: %s", exc)


def init_db() -> None:
    """Create required extensions and tables."""
    settings = get_settings()
    engine = get_engine()
    with engine.begin() as connection:
        if settings.postgis_enabled:
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
    Base.metadata.create_all(bind=engine)
    ensure_address_validation_columns()
    ensure_agent1_data_column()
    ensure_pipeline_flow_tables()
    ensure_performance_indexes()

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables or .env."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = Field(
        default="postgresql+psycopg2://ftth:ftth@127.0.0.1:5432/ftth",
        alias="DATABASE_URL",
    )
    # When true: create postgis extension, geometry columns, and ST_* writes.
    # Requires PostgreSQL with PostGIS (e.g. postgis/postgis Docker image).
    postgis_enabled: bool = Field(default=False, alias="POSTGIS_ENABLED")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    default_customer_id: str = Field(default="demo_customer", alias="DEFAULT_CUSTOMER_ID")
    dispatch_batch_size: int = Field(default=100, alias="DISPATCH_BATCH_SIZE")

    # ── Redis ────────────────────────────────────────────────────────────────
    redis_url: str = Field(default="redis://127.0.0.1:6379/0", alias="REDIS_URL")

    # ── JWT ──────────────────────────────────────────────────────────────────
    jwt_secret_key: str = Field(
        default="change-me-in-production-please-use-env-var",
        alias="JWT_SECRET_KEY",
    )
    jwt_expire_hours: int = Field(default=24, alias="JWT_EXPIRE_HOURS")

    # ── Celery ───────────────────────────────────────────────────────────────
    celery_broker_url: str = Field(default="redis://127.0.0.1:6379/1", alias="CELERY_BROKER_URL")
    celery_result_backend: str = Field(default="redis://127.0.0.1:6379/2", alias="CELERY_RESULT_BACKEND")

    # ── RabbitMQ (A2A agent bus) ──────────────────────────────────────────────
    rabbitmq_url: str = Field(
        default="amqp://ftth:ftth@127.0.0.1:5672/ftth",
        alias="RABBITMQ_URL",
    )

    # ── LRU cache ────────────────────────────────────────────────────────────
    lru_cache_max_size: int = Field(default=10_000, alias="LRU_CACHE_MAX_SIZE")
    lru_cache_ttl: int = Field(default=2_592_000, alias="LRU_CACHE_TTL")  # 30 days

    # Pipeline confidence gates. A row stops at the first agent whose score is
    # at or above its threshold; lower scores continue to the next agent.
    agent0_confidence_threshold: int = Field(default=90, alias="AGENT0_CONFIDENCE_THRESHOLD")
    agent1_confidence_threshold: int = Field(default=90, alias="AGENT1_CONFIDENCE_THRESHOLD")
    agent2_confidence_threshold: int = Field(default=90, alias="AGENT2_CONFIDENCE_THRESHOLD")
    agent3_confidence_threshold: int = Field(default=90, alias="AGENT3_CONFIDENCE_THRESHOLD")
    agent5_confidence_threshold: int = Field(default=90, alias="AGENT5_CONFIDENCE_THRESHOLD")
    agent6_confidence_threshold: int = Field(default=90, alias="AGENT6_CONFIDENCE_THRESHOLD")
    # Fast Agent 5: parallel workers + no sleep. Full house-number steps b–j are preserved.
    agent5_fast_mode: bool = Field(default=True, alias="AGENT5_FAST_MODE")
    agent5_max_workers: int = Field(default=4, alias="AGENT5_MAX_WORKERS")
    res_com_addressing_enabled: bool = Field(default=True, alias="RES_COM_ADDRESSING_ENABLED")

    # ── Database pool ─────────────────────────────────────────────────────────
    db_pool_size: int = Field(default=10, alias="DB_POOL_SIZE")
    db_max_overflow: int = Field(default=20, alias="DB_MAX_OVERFLOW")
    db_pool_timeout: int = Field(default=30, alias="DB_POOL_TIMEOUT")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

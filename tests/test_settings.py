"""
Unit tests for application settings (data_ingestion/config/settings.py).

Validates:
  - All default values are correct
  - RabbitMQ URL field exists and has the right default
  - LRU cache max_size and TTL defaults
  - Environment variable overrides take precedence over defaults
  - Settings are a Pydantic BaseSettings subclass (env-file aware)
  - get_settings() is an lru_cache singleton

No .env file or live services required — env vars are monkeypatched.

Run:
    pytest tests/test_settings.py -v
"""
from __future__ import annotations

import os
from unittest.mock import patch



# ─────────────────────────────────────────────────────────────────────────────
# Helper: fresh Settings instance bypassing the lru_cache
# ─────────────────────────────────────────────────────────────────────────────

def _fresh_settings(**env_overrides):
    """Return a new Settings() with env vars overridden for this call only."""
    from data_ingestion.config.settings import Settings
    # Temporarily patch os.environ so Pydantic picks up the overrides
    with patch.dict(os.environ, {k: str(v) for k, v in env_overrides.items()}, clear=False):
        return Settings()


# ─────────────────────────────────────────────────────────────────────────────
# 1. Default values
# ─────────────────────────────────────────────────────────────────────────────

class TestDefaultValues:
    def test_database_url_default(self):
        s = _fresh_settings()
        assert "postgresql" in s.database_url
        assert "ftth" in s.database_url

    def test_log_level_default(self):
        s = _fresh_settings()
        assert s.log_level == "INFO"

    def test_default_customer_id(self):
        s = _fresh_settings()
        assert s.default_customer_id == "demo_customer"

    def test_dispatch_batch_size_default(self):
        s = _fresh_settings()
        assert s.dispatch_batch_size == 100

    def test_redis_url_default(self):
        s = _fresh_settings()
        assert s.redis_url.startswith("redis://")
        assert "6379" in s.redis_url

    def test_jwt_expire_hours_default(self):
        s = _fresh_settings()
        assert s.jwt_expire_hours == 24

    def test_celery_broker_url_default(self):
        s = _fresh_settings()
        assert "redis://" in s.celery_broker_url

    def test_celery_result_backend_default(self):
        s = _fresh_settings()
        assert "redis://" in s.celery_result_backend

    def test_postgis_enabled_default_false(self):
        s = _fresh_settings()
        assert s.postgis_enabled is False


# ─────────────────────────────────────────────────────────────────────────────
# 2. RabbitMQ settings
# ─────────────────────────────────────────────────────────────────────────────

class TestRabbitMQSettings:
    def test_rabbitmq_url_field_exists(self):
        s = _fresh_settings()
        assert hasattr(s, "rabbitmq_url")

    def test_rabbitmq_url_default_scheme(self):
        s = _fresh_settings()
        assert s.rabbitmq_url.startswith("amqp://")

    def test_rabbitmq_url_default_credentials(self):
        s = _fresh_settings()
        assert "ftth:ftth" in s.rabbitmq_url

    def test_rabbitmq_url_default_port(self):
        s = _fresh_settings()
        assert "5672" in s.rabbitmq_url

    def test_rabbitmq_url_default_vhost(self):
        s = _fresh_settings()
        assert s.rabbitmq_url.endswith("/ftth")

    def test_rabbitmq_url_env_override(self):
        s = _fresh_settings(RABBITMQ_URL="amqp://user:pass@broker:5672/vhost")
        assert s.rabbitmq_url == "amqp://user:pass@broker:5672/vhost"

    def test_rabbitmq_url_is_string(self):
        s = _fresh_settings()
        assert isinstance(s.rabbitmq_url, str)


# ─────────────────────────────────────────────────────────────────────────────
# 3. LRU cache settings
# ─────────────────────────────────────────────────────────────────────────────

class TestLRUCacheSettings:
    def test_lru_cache_max_size_field_exists(self):
        s = _fresh_settings()
        assert hasattr(s, "lru_cache_max_size")

    def test_lru_cache_ttl_field_exists(self):
        s = _fresh_settings()
        assert hasattr(s, "lru_cache_ttl")

    def test_lru_cache_max_size_default(self):
        s = _fresh_settings()
        assert s.lru_cache_max_size == 10_000

    def test_lru_cache_ttl_default_30_days(self):
        s = _fresh_settings()
        assert s.lru_cache_ttl == 2_592_000  # 30 × 24 × 3600

    def test_lru_cache_max_size_env_override(self):
        s = _fresh_settings(LRU_CACHE_MAX_SIZE="5000")
        assert s.lru_cache_max_size == 5000

    def test_lru_cache_ttl_env_override(self):
        s = _fresh_settings(LRU_CACHE_TTL="86400")
        assert s.lru_cache_ttl == 86_400

    def test_lru_cache_max_size_is_int(self):
        s = _fresh_settings()
        assert isinstance(s.lru_cache_max_size, int)

    def test_lru_cache_ttl_is_int(self):
        s = _fresh_settings()
        assert isinstance(s.lru_cache_ttl, int)

    def test_lru_cache_max_size_positive(self):
        s = _fresh_settings()
        assert s.lru_cache_max_size > 0

    def test_lru_cache_ttl_positive(self):
        s = _fresh_settings()
        assert s.lru_cache_ttl > 0


# ─────────────────────────────────────────────────────────────────────────────
# 4. Environment variable overrides
# ─────────────────────────────────────────────────────────────────────────────

class TestEnvVarOverrides:
    def test_database_url_override(self):
        s = _fresh_settings(DATABASE_URL="postgresql+psycopg2://u:p@host:5432/db")
        assert s.database_url == "postgresql+psycopg2://u:p@host:5432/db"

    def test_log_level_override(self):
        s = _fresh_settings(LOG_LEVEL="DEBUG")
        assert s.log_level == "DEBUG"

    def test_redis_url_override(self):
        s = _fresh_settings(REDIS_URL="redis://cache-host:6380/1")
        assert s.redis_url == "redis://cache-host:6380/1"

    def test_dispatch_batch_size_override(self):
        s = _fresh_settings(DISPATCH_BATCH_SIZE="250")
        assert s.dispatch_batch_size == 250

    def test_postgis_enabled_override(self):
        s = _fresh_settings(POSTGIS_ENABLED="true")
        assert s.postgis_enabled is True

    def test_jwt_secret_key_override(self):
        s = _fresh_settings(JWT_SECRET_KEY="super-secret-key-abc123")
        assert s.jwt_secret_key == "super-secret-key-abc123"

    def test_jwt_expire_hours_override(self):
        s = _fresh_settings(JWT_EXPIRE_HOURS="48")
        assert s.jwt_expire_hours == 48

    def test_celery_broker_url_override(self):
        s = _fresh_settings(CELERY_BROKER_URL="redis://broker:6379/3")
        assert s.celery_broker_url == "redis://broker:6379/3"

    def test_multiple_overrides_at_once(self):
        s = _fresh_settings(
            LOG_LEVEL="WARNING",
            LRU_CACHE_MAX_SIZE="2000",
            RABBITMQ_URL="amqp://guest:guest@localhost:5672/",
        )
        assert s.log_level == "WARNING"
        assert s.lru_cache_max_size == 2000
        assert s.rabbitmq_url == "amqp://guest:guest@localhost:5672/"


# ─────────────────────────────────────────────────────────────────────────────
# 5. get_settings() singleton behaviour
# ─────────────────────────────────────────────────────────────────────────────

class TestGetSettingsSingleton:
    def test_get_settings_returns_settings_instance(self):
        from data_ingestion.config.settings import Settings, get_settings
        s = get_settings()
        assert isinstance(s, Settings)

    def test_get_settings_returns_same_object(self):
        from data_ingestion.config.settings import get_settings
        s1 = get_settings()
        s2 = get_settings()
        assert s1 is s2

    def test_get_settings_is_lru_cached(self):
        from data_ingestion.config import settings as settings_mod
        assert hasattr(settings_mod.get_settings, "cache_info"), (
            "get_settings() must be decorated with @lru_cache"
        )

    def test_get_settings_has_rabbitmq_url(self):
        from data_ingestion.config.settings import get_settings
        s = get_settings()
        assert hasattr(s, "rabbitmq_url")
        assert s.rabbitmq_url  # non-empty

    def test_get_settings_has_lru_cache_fields(self):
        from data_ingestion.config.settings import get_settings
        s = get_settings()
        assert hasattr(s, "lru_cache_max_size")
        assert hasattr(s, "lru_cache_ttl")


# ─────────────────────────────────────────────────────────────────────────────
# 6. Settings model metadata
# ─────────────────────────────────────────────────────────────────────────────

class TestSettingsModelMetadata:
    def test_settings_is_pydantic_base_settings(self):
        from data_ingestion.config.settings import Settings
        from pydantic_settings import BaseSettings
        assert issubclass(Settings, BaseSettings)

    def test_settings_has_model_config(self):
        from data_ingestion.config.settings import Settings
        assert hasattr(Settings, "model_config")

    def test_settings_ignores_extra_env_vars(self):
        """extra='ignore' means unknown env vars don't raise."""
        s = _fresh_settings(UNKNOWN_ENV_VAR_XYZ="ignored")
        assert s is not None

    def test_all_required_fields_present(self):
        from data_ingestion.config.settings import Settings
        fields = Settings.model_fields
        required_fields = [
            "database_url", "postgis_enabled", "redis_url", "rabbitmq_url",
            "lru_cache_max_size", "lru_cache_ttl",
            "celery_broker_url", "celery_result_backend",
        ]
        for field in required_fields:
            assert field in fields, f"Missing field: {field}"

    def test_jwt_secret_key_has_default(self):
        s = _fresh_settings()
        assert s.jwt_secret_key  # non-empty default

    def test_default_celery_broker_uses_redis_db1(self):
        s = _fresh_settings()
        assert "/1" in s.celery_broker_url

    def test_default_celery_result_uses_redis_db2(self):
        s = _fresh_settings()
        assert "/2" in s.celery_result_backend

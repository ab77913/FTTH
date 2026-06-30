"""Startup configuration validation tests."""

import os
from unittest.mock import patch

import pytest

from data_ingestion.config.startup_checks import validate_startup_config


def test_startup_warns_on_insecure_jwt():
    from data_ingestion.config.settings import get_settings

    get_settings.cache_clear()
    with patch.dict(os.environ, {"FTTH_ENV": "development", "JWT_SECRET_KEY": "short"}, clear=False):
        warnings = validate_startup_config(strict=False)
    assert any("JWT" in w for w in warnings)


def test_startup_strict_raises_on_insecure_jwt():
    from data_ingestion.config.settings import get_settings

    get_settings.cache_clear()
    with patch.dict(
        os.environ,
        {"FTTH_ENV": "production", "JWT_SECRET_KEY": "change-me-in-production"},
        clear=False,
    ):
        with pytest.raises(RuntimeError, match="JWT"):
            validate_startup_config(strict=True)

def test_startup_strict_rejects_default_database_password():
    from data_ingestion.config.settings import get_settings

    get_settings.cache_clear()
    with patch.dict(
        os.environ,
        {
            "FTTH_ENV": "production",
            "JWT_SECRET_KEY": "x" * 40,
            "DATABASE_URL": "postgresql+psycopg2://ftth:ftth@db.internal:5432/ftth",
            "FTTH_CORS_ORIGINS": "https://example.com",
        },
        clear=False,
    ):
        with pytest.raises(RuntimeError, match="database password"):
            validate_startup_config(strict=True)


def test_startup_strict_allows_local_database_when_explicit():
    from data_ingestion.config.settings import get_settings

    get_settings.cache_clear()
    with patch.dict(
        os.environ,
        {
            "FTTH_ENV": "production",
            "JWT_SECRET_KEY": "x" * 40,
            "DATABASE_URL": "postgresql+psycopg2://ftth:strong-password@127.0.0.1:5432/ftth",
            "FTTH_ALLOW_LOCAL_DATABASE": "1",
            "FTTH_CORS_ORIGINS": "https://example.com",
        },
        clear=False,
    ):
        warnings = validate_startup_config(strict=True)
    assert warnings == []

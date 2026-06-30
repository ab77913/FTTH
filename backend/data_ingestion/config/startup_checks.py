"""Validate configuration at application startup."""

from __future__ import annotations

import logging
import os
from urllib.parse import urlparse

from data_ingestion.config.settings import get_settings

logger = logging.getLogger(__name__)

_INSECURE_JWT_DEFAULTS = {
    "change-me-in-production-please-use-env-var",
    "change-me-in-production",
    "ci-test-secret-not-for-production",
}
_INSECURE_DATABASE_PASSWORDS = {"", "password", "postgres", "ftth", "admin", "changeme"}


def validate_startup_config(*, strict: bool | None = None) -> list[str]:
    """Return warnings; raise RuntimeError when strict and critical issues exist."""
    if strict is None:
        strict = os.environ.get("FTTH_ENV", "").strip().lower() in {"production", "prod"}

    warnings: list[str] = []
    settings = get_settings()

    jwt = (settings.jwt_secret_key or "").strip()
    if jwt in _INSECURE_JWT_DEFAULTS or len(jwt) < 32:
        msg = "JWT_SECRET_KEY is missing or uses an insecure default"
        warnings.append(msg)
        if strict:
            raise RuntimeError(msg)

    database_url = os.environ.get("DATABASE_URL", "").strip() or settings.database_url
    if strict and not database_url:
        raise RuntimeError("DATABASE_URL is required in production")
    parsed_db = urlparse(database_url)
    if strict and (parsed_db.password or "") in _INSECURE_DATABASE_PASSWORDS:
        raise RuntimeError("DATABASE_URL uses an insecure default database password")
    local_database_allowed = os.environ.get("FTTH_ALLOW_LOCAL_DATABASE", "").strip().lower() in {"1", "true", "yes"}
    if strict and parsed_db.hostname in {"localhost", "127.0.0.1"} and not local_database_allowed:
        raise RuntimeError("DATABASE_URL points to localhost in production; set FTTH_ALLOW_LOCAL_DATABASE=1 only for single-host deployments")

    cors = os.environ.get("FTTH_CORS_ORIGINS", "").strip()
    if strict and ("*" in cors or not cors):
        msg = "FTTH_CORS_ORIGINS must be an explicit allowlist in production"
        warnings.append(msg)
        if strict:
            raise RuntimeError(msg)

    for warning in warnings:
        logger.warning("startup config: %s", warning)

    return warnings

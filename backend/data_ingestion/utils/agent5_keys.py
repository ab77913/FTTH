"""Resolve and validate Google Maps / Street View API keys."""

from __future__ import annotations
from data_ingestion.config.paths import PROJECT_ROOT

import logging
import os
import re

logger = logging.getLogger(__name__)

_ENV_FILE = PROJECT_ROOT / ".env"

_KEY_ENV_PRIORITY = (
    "GOOGLE_MAPS_API_KEY",
    "GOOGLE_API_KEY",
    "GOOGLE_GEOCODING_API_KEY",
)

_PLACEHOLDER_VALUES = frozenset(
    {
        "",
        "123456789",
        "your-api-key",
        "change-me",
        "changeme",
    }
)

_GOOGLE_KEY_PATTERN = re.compile(r"^AIza[0-9A-Za-z\-_]{35}$")


def _dotenv_values() -> dict[str, str]:
    if not _ENV_FILE.exists():
        return {}
    try:
        from dotenv import dotenv_values

        return {
            str(key): str(value).strip()
            for key, value in dotenv_values(_ENV_FILE).items()
            if value is not None and str(value).strip()
        }
    except Exception:
        logger.debug("Could not read .env from %s", _ENV_FILE, exc_info=True)
        return {}


def resolve_google_api_key() -> str:
    """Return the best available Google Maps key from .env then os.environ."""
    dotenv = _dotenv_values()
    for name in _KEY_ENV_PRIORITY:
        value = dotenv.get(name) or os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def is_plausible_google_api_key(key: str | None) -> bool:
    """Reject obvious placeholders; accept standard Google key format."""
    if not key:
        return False
    candidate = str(key).strip()
    if candidate.lower() in _PLACEHOLDER_VALUES:
        return False
    if _GOOGLE_KEY_PATTERN.match(candidate):
        return True
    # Allow non-standard but long keys used in some enterprise setups.
    return len(candidate) >= 32 and candidate.isascii()

"""Utility helpers: config loading, logging, geometry math, and timing."""

from __future__ import annotations

import math
import time
from functools import wraps
from pathlib import Path
from typing import Any, Callable

import yaml
from loguru import logger


def load_config(path: str = "config/settings.example.yaml") -> dict[str, Any]:
    """Load YAML config, merging thresholds.yaml into the thresholds key if absent."""
    config_path = Path(path)
    config: dict[str, Any] = {}

    if config_path.exists():
        with config_path.open("r", encoding="utf-8") as fh:
            config = yaml.safe_load(fh) or {}

    if "thresholds" not in config:
        threshold_path = Path("config/thresholds.yaml")
        if threshold_path.exists():
            with threshold_path.open("r", encoding="utf-8") as fh:
                config["thresholds"] = yaml.safe_load(fh) or {}

    return config


def setup_logger(level: str = "INFO", file: str | None = None):
    """Configure loguru logger with optional file sink and return logger."""
    logger.remove()
    logger.add(
        sink=lambda msg: print(msg, end=""),
        level=level,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level:<8}</level> | {message}",
        colorize=True,
    )
    if file:
        log_path = Path(file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        logger.add(
            sink=file,
            level=level,
            rotation="10 MB",
            retention="7 days",
            format="{time:YYYY-MM-DD HH:mm:ss} | {level:<8} | {message}",
        )
    return logger


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return the great-circle distance in metres between two WGS-84 points."""
    R = 6_371_000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def timer(func: Callable) -> Callable:
    """Decorator that logs the elapsed wall-clock time of the wrapped function."""
    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        t0 = time.perf_counter()
        result = func(*args, **kwargs)
        elapsed = time.perf_counter() - t0
        logger.debug(f"{func.__qualname__} completed in {elapsed:.3f}s")
        return result
    return wrapper

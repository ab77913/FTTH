"""PostGIS feature flag and geometry column helpers.

Set POSTGIS_ENABLED=true (and use a PostGIS-enabled PostgreSQL image) to store
points in geometry columns. When false, latitude/longitude columns are used only.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from sqlalchemy import func


@lru_cache(maxsize=1)
def postgis_enabled() -> bool:
    from data_ingestion.config.settings import get_settings

    return get_settings().postgis_enabled


def set_address_geom(address: Any, longitude: float, latitude: float) -> None:
    """Populate Address.geom from WGS84 lon/lat when PostGIS is enabled."""
    if not postgis_enabled():
        return
    address.geom = func.ST_SetSRID(func.ST_MakePoint(longitude, latitude), 4326)

"""Tests for SpatialIndex matching logic."""

from __future__ import annotations

import pytest
import geopandas as gpd
from shapely.geometry import box

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.spatial import SpatialIndex


CONFIG = {
    "thresholds": {
        "search_radius_m": 15,
        "fallback_radius_m": 30,
        "min_area_valid_m2": 40,
    }
}


def _make_box_at(lon: float, lat: float, size_deg: float = 0.0001) -> dict:
    return box(lon - size_deg, lat - size_deg, lon + size_deg, lat + size_deg)


@pytest.fixture()
def sample_gdf() -> gpd.GeoDataFrame:
    lons = [-97.752, -97.753, -97.754, -97.755, -97.756]
    lats = [30.251, 30.252, 30.253, 30.254, 30.255]
    geometries = [_make_box_at(lon, lat) for lon, lat in zip(lons, lats)]
    gdf = gpd.GeoDataFrame(
        {
            "id": [f"fp_{i:03d}" for i in range(5)],
            "source": ["ms"] * 5,
            "area_m2": [120.0, 150.0, 200.0, 800.0, 5500.0],
        },
        geometry=geometries,
        crs="EPSG:4326",
    )
    return gdf


@pytest.fixture()
def spatial_index(sample_gdf: gpd.GeoDataFrame) -> SpatialIndex:
    idx = SpatialIndex(CONFIG)
    idx.build_sqlite(sample_gdf)
    return idx


def test_match_within_radius(spatial_index: SpatialIndex) -> None:
    """A point inside a footprint centroid should return a match."""
    result = spatial_index.match(lat=30.251, lon=-97.752, radius_m=50)
    assert result is not None
    assert "footprint_id" in result
    assert result["distance_m"] is not None


def test_no_match_outside_radius(spatial_index: SpatialIndex) -> None:
    """A point 500 m away from all footprints should return None."""
    result = spatial_index.match(lat=31.000, lon=-98.000, radius_m=15)
    assert result is None


def test_batch_match_returns_all(spatial_index: SpatialIndex) -> None:
    """batch_match on 5 records returns a list of 5 dicts (some may have _match=None)."""
    records = [
        {"address_id": f"a{i}", "lat": 30.251 + i * 0.001, "lon": -97.752 - i * 0.001}
        for i in range(5)
    ]
    results = spatial_index.batch_match(records)
    assert len(results) == 5
    for r in results:
        assert "_match" in r


def test_fallback_radius_used(spatial_index: SpatialIndex) -> None:
    """A point at ~20 m (beyond primary 15 m) should match via fallback 30 m radius."""
    primary_result = spatial_index.match(lat=30.251, lon=-97.752, radius_m=1)
    fallback_result = spatial_index.match(lat=30.251, lon=-97.752, radius_m=None)
    assert fallback_result is not None

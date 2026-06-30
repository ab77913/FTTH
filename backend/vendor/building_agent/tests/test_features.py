"""Tests for FeatureExtractor geometric computations."""

from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from shapely.geometry import box, Polygon

from src.features import FeatureExtractor


CONFIG = {
    "thresholds": {
        "min_area_valid_m2": 40,
        "search_radius_m": 15,
        "fallback_radius_m": 30,
    }
}


@pytest.fixture()
def extractor() -> FeatureExtractor:
    return FeatureExtractor(CONFIG)


@pytest.fixture()
def rect_polygon() -> Polygon:
    """40 x 10 metre rectangle near Austin TX (degrees approximated)."""
    lon0, lat0 = -97.752, 30.251
    d_lon_40m = 40 / 111_320
    d_lat_10m = 10 / 110_574
    return Polygon([
        (lon0, lat0),
        (lon0 + d_lon_40m, lat0),
        (lon0 + d_lon_40m, lat0 + d_lat_10m),
        (lon0, lat0 + d_lat_10m),
        (lon0, lat0),
    ])


@pytest.fixture()
def square_polygon() -> Polygon:
    """20 x 20 metre square near Austin TX."""
    lon0, lat0 = -97.752, 30.251
    d_20m = 20 / 111_320
    return Polygon([
        (lon0, lat0),
        (lon0 + d_20m, lat0),
        (lon0 + d_20m, lat0 + d_20m),
        (lon0, lat0 + d_20m),
        (lon0, lat0),
    ])


def test_area_rectangle(extractor: FeatureExtractor, rect_polygon: Polygon) -> None:
    """Projected area of ~40x10m rectangle should be close to 400 m²."""
    area = extractor.compute_area(rect_polygon)
    assert 300 < area < 500, f"Expected ~400 m², got {area:.1f}"


def test_elongation_rectangle(extractor: FeatureExtractor, rect_polygon: Polygon) -> None:
    """40x10m rectangle should have elongation ~4.0 ± 0.5."""
    elongation = extractor.compute_elongation(rect_polygon)
    assert 3.5 < elongation < 4.5, f"Expected ~4.0, got {elongation:.3f}"


def test_elongation_square(extractor: FeatureExtractor, square_polygon: Polygon) -> None:
    """Square polygon should have elongation ~1.0 ± 0.1."""
    elongation = extractor.compute_elongation(square_polygon)
    assert 0.9 < elongation < 1.15, f"Expected ~1.0, got {elongation:.3f}"


def test_perimeter_complexity_convex(extractor: FeatureExtractor, rect_polygon: Polygon) -> None:
    """A convex polygon returns perimeter_complexity ~1.0."""
    complexity = extractor.compute_perimeter_complexity(rect_polygon)
    assert 0.98 < complexity < 1.02, f"Expected ~1.0, got {complexity:.3f}"


def test_floor_count_sfu(extractor: FeatureExtractor) -> None:
    """Small area with SFU hint returns 1 floor."""
    floors = extractor.compute_floor_count_estimate(120.0, "SFU")
    assert floors == 1


def test_floor_count_mdu_large(extractor: FeatureExtractor) -> None:
    """2000 m² MDU should return more than 5 floors."""
    floors = extractor.compute_floor_count_estimate(2000.0, "MDU")
    assert floors > 5, f"Expected > 5, got {floors}"

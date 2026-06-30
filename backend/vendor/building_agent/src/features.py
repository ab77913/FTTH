from __future__ import annotations

import math
from typing import Any

import geopandas as gpd
from loguru import logger
from shapely.geometry.base import BaseGeometry


class FeatureExtractor:

    CRS_WGS84 = "EPSG:4326"
    CRS_METRE = "EPSG:3857"

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        thresholds: dict[str, Any] = config.get("thresholds", {})
        self.min_area: float = float(thresholds.get("min_area_valid_m2", 40))

    # ---------------------------------------------------------
    # ✅ AREA
    # ---------------------------------------------------------
    def compute_area(self, geometry: BaseGeometry | None) -> float:
        if geometry is None or geometry.is_empty:
            return 0.0
        try:
            series = gpd.GeoSeries([geometry], crs=self.CRS_WGS84).to_crs(self.CRS_METRE)
            return float(series.iloc[0].area)
        except Exception:
            try:
                return float(geometry.area)
            except Exception:
                return 0.0

    # ---------------------------------------------------------
    # ✅ SHAPE
    # ---------------------------------------------------------
    def compute_elongation(self, geometry: BaseGeometry | None) -> float:
        if geometry is None or geometry.is_empty:
            return 1.0
        try:
            rect = geometry.minimum_rotated_rectangle
            coords = list(rect.exterior.coords)

            edges = [
                math.dist(coords[i], coords[i + 1])
                for i in range(len(coords) - 1)
                if math.dist(coords[i], coords[i + 1]) > 0
            ]

            if len(edges) < 2:
                return 1.0

            return max(edges) / min(edges)

        except Exception:
            return 1.0

    # ---------------------------------------------------------
    # ✅ FLOOR
    # ---------------------------------------------------------
    def compute_floor_count_estimate(self, area_m2: float, structure_hint: str) -> int:

        if structure_hint == "SFU":
            return 1

        if structure_hint == "MDU_SMALL":
            return 2

        if structure_hint == "MDU_LARGE":
            return max(3, min(6, int(area_m2 / 250)))

        if structure_hint == "ANCHOR":
            return 1

        return 1

    # ---------------------------------------------------------
    # ✅ ✅ UNIT COUNT (FIXED)
    # ---------------------------------------------------------
    def compute_unit_count(self, area_m2: float, structure_hint: str) -> int | None:

        if structure_hint == "SFU":
            return 1

        # ✅ MDU SMALL (2–4 units)
        if structure_hint == "MDU_SMALL":
            if area_m2 < 450:
                return 2
            elif area_m2 < 550:
                return 3
            else:
                return 4

        # ✅ MDU LARGE (5+ units)
        if structure_hint == "MDU_LARGE":
            return max(5, int(area_m2 / 120))

        # ✅ ANCHOR
        if structure_hint == "ANCHOR":
            return 1

        return None

    # ---------------------------------------------------------
    # ✅ MAIN FEATURE EXTRACTION
    # ---------------------------------------------------------
    def extract(self, match_result: dict[str, Any]) -> dict[str, Any]:

        if match_result is None:
            return self._empty_features()

        geom = match_result.get("geometry")
        area = match_result.get("area_m2") or self.compute_area(geom)

        elongation = self.compute_elongation(geom)

        return {
            "footprint_area_m2": round(area, 2),
            "elongation_ratio": round(elongation, 3),
            "footprint_source": match_result.get("source", "unknown"),
            "footprint_match_distance_m": round(float(match_result.get("distance_m", 0)), 2),
        }

    # ---------------------------------------------------------
    # ✅ POST CLASSIFICATION
    # ---------------------------------------------------------
    def add_post_classification(self, result: dict[str, Any]) -> dict[str, Any]:

        area = result.get("footprint_area_m2", 0)
        structure = result.get("structure_hint", "UNRESOLVED")

        result["floor_count"] = self.compute_floor_count_estimate(area, structure)
        result["unit_count"] = self.compute_unit_count(area, structure)

        return result

    def extract_batch(self, match_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [self.extract(r) for r in match_results]

    @staticmethod
    def _empty_features() -> dict[str, Any]:
        return {
            "footprint_area_m2": None,
            "elongation_ratio": None,
            "footprint_source": None,
            "footprint_match_distance_m": None,
            "floor_count": None,
            "unit_count": None,
        }

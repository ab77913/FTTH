"""Spatial indexing and footprint matching (SQLite STRtree or PostGIS)."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, List, Dict

import geopandas as gpd
import pandas as pd
from loguru import logger
from shapely.geometry import Point
from shapely.strtree import STRtree

from .utils import timer
from .loader import FootprintLoader


class SpatialIndex:
    """Build and query a spatial index over building footprints."""

    CRS_WGS84 = "EPSG:4326"
    CRS_METRE = "EPSG:3857"

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

        thresholds: dict[str, Any] = config.get("thresholds", {})

        # ✅ Progressive search radii
        self.search_radii = thresholds.get("search_radii_m", [15, 30, 50])

        self._gdf: gpd.GeoDataFrame | None = None
        self._tree: STRtree | None = None
        self._mode: str = "sqlite"

    # ------------------------------------------------------------------
    # ✅ NEW: AUTO LOAD + BUILD (KEY CHANGE)
    # ------------------------------------------------------------------
    def load_and_build(self, records: List[Dict[str, Any]]) -> None:
        if not records:
            logger.warning("No records provided")
            return

        from scripts.download_footprints import _state_from_lat_lon, download_by_lat_lon

        state_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)

        for rec in records:
            try:
                lat = float(rec["lat"])
                lon = float(rec["lon"])
                state = _state_from_lat_lon(lat, lon)
                state_groups[state].append(rec)
            except (KeyError, TypeError, ValueError) as exc:
                logger.warning(f"Skipping record with invalid lat/lon or state: {exc}")

        if not state_groups:
            logger.warning("No valid US state records found — index not built")
            return

        loader = FootprintLoader(self.config)
        save_dir = self.config.get("data", {}).get("footprints_dir", "data/footprints")
        gdfs: list[gpd.GeoDataFrame] = []
        loaded_states: list[str] = []

        for state, state_records in sorted(state_groups.items()):
            try:
                sample = state_records[0]
                lat = float(sample["lat"])
                lon = float(sample["lon"])

                path = download_by_lat_lon(lat, lon, save_dir)
                gdf = loader.load_ms_footprints(path)

                if gdf.empty:
                    logger.warning(f"No footprints loaded for state={state}")
                    continue

                gdfs.append(gdf)
                loaded_states.append(state)
                logger.info(
                    f"Loaded state={state} records={len(state_records)} footprints={len(gdf)}"
                )
            except Exception as exc:
                logger.warning(f"Failed to load footprints for state={state}: {exc}")

        if not gdfs:
            logger.warning("No footprints loaded for any state — index not built")
            return

        combined_gdf = gpd.GeoDataFrame(
            pd.concat(gdfs, ignore_index=True),
            crs=gdfs[0].crs,
        )
        logger.info(
            f"Building combined index from states={loaded_states} "
            f"total_footprints={len(combined_gdf)}"
        )
        self.build_sqlite(combined_gdf)

    # ------------------------------------------------------------------
    @timer
    def build_sqlite(self, gdf: gpd.GeoDataFrame) -> None:
        """Build STRtree index"""
        if gdf is None or gdf.empty:
            logger.warning("Empty GeoDataFrame — index not built")
            return

        self._gdf = gdf.to_crs(self.CRS_METRE).reset_index(drop=True)
        self._tree = STRtree(self._gdf.geometry.values)
        self._mode = "sqlite"

        logger.info(f"STRtree index built with {len(self._gdf)} footprints")

    # ------------------------------------------------------------------
    def match(self, lat: float, lon: float) -> dict[str, Any] | None:
        """Progressive radius search"""

        for radius_m in self.search_radii:
            result = self._do_match(lat, lon, radius_m)

            if result:
                result["matched_radius_m"] = radius_m
                return result

            logger.debug(f"No match at {radius_m}m")

        return None

    # ------------------------------------------------------------------
    def _do_match(self, lat: float, lon: float, radius_m: float) -> dict[str, Any] | None:
        return self._match_strtree(lat, lon, radius_m)

    # ------------------------------------------------------------------
    def _match_strtree(self, lat: float, lon: float, radius_m: float) -> dict[str, Any] | None:

        if self._tree is None or self._gdf is None:
            logger.warning("Index not built — call load_and_build() first")
            return None

        point_wgs = Point(lon, lat)

        point_m = (
            gpd.GeoSeries([point_wgs], crs=self.CRS_WGS84)
            .to_crs(self.CRS_METRE)
            .iloc[0]
        )

        # ✅ Query candidates
        candidate_idxs = self._tree.query(point_m.buffer(radius_m))

        if len(candidate_idxs) == 0:
            return None

        candidates = self._gdf.iloc[candidate_idxs]
        distances = candidates.geometry.distance(point_m)

        within = distances[distances <= radius_m]

        if within.empty:
            return None

        best_idx = within.idxmin()
        row = self._gdf.loc[best_idx]

        geom = row.geometry
        dist_m = float(distances.loc[best_idx])

        return self._build_result(row, geom, dist_m)

    # ------------------------------------------------------------------
    def _build_result(self, row: Any, geom: Any, dist_m: float) -> dict[str, Any]:

        area = float(geom.area) if geom else 0.0
        perimeter = float(geom.length) if geom else 0.0

        footprint_id = str(row.get("id", row.name))
        source = str(row.get("source", "unknown"))

        return {
            "footprint_id": footprint_id,
            "geometry": geom,
            "area_m2": area,
            "perimeter_m": perimeter,
            "source": source,
            "distance_m": dist_m,
        }

    # ------------------------------------------------------------------
    def batch_match(self, records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:

        results: List[Dict[str, Any]] = []

        for rec in records:
            try:
                lat = float(rec["lat"])
                lon = float(rec["lon"])
                match = self.match(lat, lon)
            except Exception as e:
                logger.warning(f"Match error: {e}")
                match = None

            results.append({**rec, "_match": match})

        return results
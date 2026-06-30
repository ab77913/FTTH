"""Footprint data loader for Microsoft and Google Open Buildings datasets."""

from __future__ import annotations

import glob
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
from loguru import logger
from shapely.geometry import Point

from .utils import haversine_distance


class FootprintLoader:
    """Load and standardise building footprints from MS and Google sources."""

    CRS_WGS84 = "EPSG:4326"
    CRS_METRE = "EPSG:3857"

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.prefer_source: str = config.get("prefer_source", "ms")

    # ---------------------------------------------------------
    # ✅ MICROSOFT (KEPT AS IS)
    # ---------------------------------------------------------

    def load_ms_footprints(self, path: str) -> gpd.GeoDataFrame:
        logger.info(f"Loading MS footprints from {path}")

        gdf = gpd.read_file(path)
        gdf = gdf[gdf.geometry.notna()].copy()

        gdf["source"] = "ms"
        gdf = gdf.to_crs(self.CRS_WGS84)

        logger.info(f"Loaded {len(gdf)} MS footprints")
        return gdf

    def load_ms_by_latlon(self, lat: float, lon: float) -> gpd.GeoDataFrame:
        import sys
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))

        from scripts.download_footprints import download_by_lat_lon

        save_dir = self.config.get("data", {}).get("footprints_dir", "data/footprints")
        path = download_by_lat_lon(lat, lon, save_dir)
        return self.load_ms_footprints(path)

    # ---------------------------------------------------------
    # ✅ GOOGLE (UPDATED)
    # ---------------------------------------------------------

    def load_google_buildings(self) -> gpd.GeoDataFrame:
        """
        Load Google Open Buildings dataset (POC version).
        Assumes a single CSV file path from config.
        """

        path = self.config.get("google_path", "data/google/google_buildings.csv")

        logger.info(f"Loading Google footprints from {path}")

        try:
            df = pd.read_csv(path)
        except Exception as e:
            logger.error(f"Failed to read Google file: {e}")
            raise

        required = {"latitude", "longitude"}
        missing = required - set(df.columns)

        if missing:
            raise ValueError(f"Google CSV missing columns: {missing}")

        # ✅ faster geometry creation
        geometry = gpd.points_from_xy(df["longitude"], df["latitude"])

        gdf = gpd.GeoDataFrame(df, geometry=geometry, crs=self.CRS_WGS84)

        # ✅ area column mapping
        if "area_in_meters" in gdf.columns:
            gdf = gdf.rename(columns={"area_in_meters": "area_m2"})

        gdf["source"] = "google"

        logger.info(f"Loaded {len(gdf)} Google footprints")
        return gdf

    # ---------------------------------------------------------
    # ✅ OPTIONAL: LOAD ALL (NOT USED IN POC)
    # ---------------------------------------------------------

    def load_all(self, ms_dir: str, google_dir: str) -> gpd.GeoDataFrame:

        gdfs: list[gpd.GeoDataFrame] = []

        for path in glob.glob(str(Path(ms_dir) / "*.geojson")):
            try:
                gdfs.append(self.load_ms_footprints(path))
            except Exception as exc:
                logger.warning(f"Skipping {path}: {exc}")

        for path in glob.glob(str(Path(google_dir) / "*.csv")):
            try:
                df = pd.read_csv(path)
                geometry = gpd.points_from_xy(df["longitude"], df["latitude"])
                gdf = gpd.GeoDataFrame(df, geometry=geometry, crs=self.CRS_WGS84)
                gdf["source"] = "google"
                gdfs.append(gdf)
            except Exception as exc:
                logger.warning(f"Skipping {path}: {exc}")

        if not gdfs:
            logger.warning("No footprint files found")
            return gpd.GeoDataFrame(columns=["geometry", "source"], crs=self.CRS_WGS84)

        combined = gpd.GeoDataFrame(pd.concat(gdfs, ignore_index=True), crs=self.CRS_WGS84)

        combined = self._deduplicate(combined)

        logger.info(f"Total footprints after dedup: {len(combined)}")
        return combined

    # ---------------------------------------------------------
    # ✅ DEDUPLICATION
    # ---------------------------------------------------------

    def _deduplicate(self, gdf: gpd.GeoDataFrame, threshold_m: float = 5.0) -> gpd.GeoDataFrame:

        preferred = self.prefer_source
        other_source = "google" if preferred == "ms" else "ms"

        preferred_gdf = gdf[gdf["source"] == preferred].copy()
        other_gdf = gdf[gdf["source"] == other_source].copy()

        if preferred_gdf.empty or other_gdf.empty:
            return gdf.reset_index(drop=True)

        preferred_centroids = preferred_gdf.geometry.centroid
        other_centroids = other_gdf.geometry.centroid

        keep_mask = []

        for geom in other_centroids:
            too_close = any(
                haversine_distance(geom.y, geom.x, c.y, c.x) < threshold_m
                for c in preferred_centroids
            )
            keep_mask.append(not too_close)

        filtered_other = other_gdf[keep_mask]

        result = gpd.GeoDataFrame(
            pd.concat([preferred_gdf, filtered_other], ignore_index=True),
            crs=self.CRS_WGS84,
        )

        removed = len(other_gdf) - len(filtered_other)

        logger.info(f"Deduplication removed {removed} {other_source} footprints")

        return result
"""CLI to load downloaded footprints into the configured spatial backend."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.loader import FootprintLoader
from src.spatial import SpatialIndex
from src.utils import load_config, setup_logger


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Load footprints into SQLite or PostGIS backend.")
    parser.add_argument("--source", choices=["ms", "google", "both"], default="both")
    parser.add_argument("--ms_dir", default="data/ms", help="Directory of MS GeoJSON files.")
    parser.add_argument("--google_dir", default="data/google", help="Directory of Google CSV files.")
    parser.add_argument("--config", default="config/settings.example.yaml")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config(args.config)
    setup_logger(
        level=config.get("log", {}).get("level", "INFO"),
        file=config.get("log", {}).get("file"),
    )

    loader = FootprintLoader(config)
    spatial = SpatialIndex(config)
    backend = config.get("backend", "sqlite")

    t0 = time.perf_counter()

    if args.source == "ms":
        gdf = loader.load_ms_footprints(args.ms_dir) if Path(args.ms_dir).is_file() else \
              loader.load_all(args.ms_dir, "")
    elif args.source == "google":
        gdf = loader.load_google_footprints(args.google_dir) if Path(args.google_dir).is_file() else \
              loader.load_all("", args.google_dir)
    else:
        gdf = loader.load_all(args.ms_dir, args.google_dir)

    print(f"Loaded {len(gdf)} footprint rows in {time.perf_counter() - t0:.2f}s")

    t1 = time.perf_counter()
    if backend == "postgis":
        conn_str = config.get("database", {}).get("postgis_conn", "")
        if not conn_str:
            print("[ERROR] postgis_conn not set in config", file=sys.stderr)
            sys.exit(1)
        spatial.build_postgis(gdf, conn_str)
    else:
        spatial.build_sqlite(gdf)

    elapsed = time.perf_counter() - t1
    print(f"Index built in {elapsed:.2f}s  backend={backend}")


if __name__ == "__main__":
    main()

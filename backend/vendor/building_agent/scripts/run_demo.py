"""Standalone demo — no external DB or API keys required."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd

from src.agent import BuildingDataAgent
from src.utils import load_config


SAMPLE_ADDRESSES = ROOT / "data" / "sample" / "sample_addresses.csv"
SAMPLE_FOOTPRINTS = ROOT / "data" / "sample" / "sample_footprints.geojson"
DEMO_OUTPUT = ROOT / "data" / "sample" / "demo_output.csv"
CONFIG_PATH = ROOT / "config" / "settings.example.yaml"


def main() -> None:
    if not SAMPLE_ADDRESSES.exists():
        print(f"[ERROR] Sample addresses not found: {SAMPLE_ADDRESSES}")
        sys.exit(1)
    if not SAMPLE_FOOTPRINTS.exists():
        print(f"[ERROR] Sample footprints not found: {SAMPLE_FOOTPRINTS}")
        sys.exit(1)

    config = load_config(str(CONFIG_PATH))
    config["data"]["sample_footprints"] = str(SAMPLE_FOOTPRINTS)
    config["backend"] = "sqlite"
    config.setdefault("data", {})["ms_dir"] = ""
    config["data"]["google_dir"] = ""

    agent = BuildingDataAgent.__new__(BuildingDataAgent)
    agent.config = config

    from src.utils import setup_logger
    setup_logger(level="INFO")

    from src.loader import FootprintLoader
    from src.spatial import SpatialIndex
    from src.features import FeatureExtractor
    from src.classifier import StructureHintClassifier
    from collections import Counter

    agent.loader = FootprintLoader(config)
    agent.spatial = SpatialIndex(config)
    agent.extractor = FeatureExtractor(config)
    agent.classifier = StructureHintClassifier(config)
    agent._stats = {"total_processed": 0, "matched": 0, "distances": [], "hints": Counter()}

    print(f"Loading footprints from {SAMPLE_FOOTPRINTS} ...")
    gdf = agent.loader.load_ms_footprints(str(SAMPLE_FOOTPRINTS))
    agent.spatial.build_sqlite(gdf)

    print(f"Loading addresses from {SAMPLE_ADDRESSES} ...")
    addr_df = pd.read_csv(SAMPLE_ADDRESSES)
    records = addr_df.to_dict(orient="records")

    print(f"Enriching {len(records)} addresses ...")
    results = agent.enrich_batch(records, workers=2)

    output_df = pd.DataFrame(results)
    display_cols = [c for c in [
        "address_id", "lat", "lon", "structure_hint",
        "hint_confidence", "footprint_area_m2", "elongation_ratio",
    ] if c in output_df.columns]

    print("\n--- Results ---")
    print(output_df[display_cols].to_string(index=False))

    stats = agent.get_stats()
    print("\n--- Stats ---")
    for key, val in stats.items():
        print(f"  {key}: {val}")

    DEMO_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    output_df.to_csv(DEMO_OUTPUT, index=False)
    print(f"\nSaved to {DEMO_OUTPUT}")


if __name__ == "__main__":
    main()

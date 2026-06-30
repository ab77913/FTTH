"""Download North Dakota MS Building Footprints, load into SpatialIndex, and run a test query."""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

import geopandas as gpd
import requests
from tqdm import tqdm

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src.spatial import SpatialIndex
from src.utils import load_config

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

ND_URL = (
    "https://minedbuildings.z5.web.core.windows.net/legacy/usbuildings-v2/"
    "NorthDakota.geojson.zip"
)
OUTPUT_DIR = ROOT / "data" / "real" / "nd"
ZIP_PATH = OUTPUT_DIR / "North_Dakota.geojson.zip"


# ---------------------------------------------------------------------------
# State detection
# ---------------------------------------------------------------------------

def detect_state(lat: float, lon: float) -> str:
    if 45.0 <= lat <= 49.0 and -105.0 <= lon <= -95.0:
        return "ND"
    return "UNKNOWN"


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def download_footprints(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        print(f"[skip] Already downloaded: {dest}")
        return

    print(f"Downloading: {url}")
    response = requests.get(url, stream=True, timeout=120)
    response.raise_for_status()

    total = int(response.headers.get("Content-Length", 0))
    with dest.open("wb") as fh, tqdm(
        total=total, unit="B", unit_scale=True, desc=dest.name
    ) as bar:
        for chunk in response.iter_content(chunk_size=65536):
            fh.write(chunk)
            bar.update(len(chunk))

    print(f"Saved to: {dest}")


# ---------------------------------------------------------------------------
# Extract ZIP
# ---------------------------------------------------------------------------

def extract_zip(zip_path: Path, output_dir: Path) -> list[Path]:
    print(f"Extracting: {zip_path}")
    geojson_files: list[Path] = []
    with zipfile.ZipFile(zip_path, "r") as zf:
        for member in zf.namelist():
            if member.endswith(".geojson") or member.endswith(".json"):
                zf.extract(member, output_dir)
                geojson_files.append(output_dir / member)
                print(f"  Extracted: {output_dir / member}")
    return geojson_files


# ---------------------------------------------------------------------------
# Load GeoDataFrame
# ---------------------------------------------------------------------------

def load_geojson(paths: list[Path]) -> gpd.GeoDataFrame:
    frames = []
    for p in paths:
        print(f"Loading: {p}")
        gdf = gpd.read_file(p)
        if "source" not in gdf.columns:
            gdf["source"] = "ms"
        frames.append(gdf)
    combined = gpd.pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]
    return combined


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    sample_lat, sample_lon = 46.81, -100.80
    state = detect_state(sample_lat, sample_lon)
    print(f"Detected state: {state} for lat={sample_lat}, lon={sample_lon}")

    if state != "ND":
        print("[ERROR] Coordinates not in North Dakota bounding box.")
        sys.exit(1)

    # 1. Download
    download_footprints(ND_URL, ZIP_PATH)

    # 2. Extract
    geojson_paths = extract_zip(ZIP_PATH, OUTPUT_DIR)
    if not geojson_paths:
        print("[ERROR] No GeoJSON files found in ZIP.")
        sys.exit(1)

    # 3. Load
    gdf = load_geojson(geojson_paths)
    print(f"\nBuildings loaded: {len(gdf):,}")
    print(f"Columns: {list(gdf.columns)}")

    # 4. Build spatial index
    config = load_config()
    spatial = SpatialIndex(config)
    spatial.build_sqlite(gdf)
    print(f"Spatial index ready ({len(gdf):,} footprints).")

    # 5. Example pipeline usage
    print("\n--- Running test query ---")
    from src.agent import BuildingDataAgent

    # Point agent at the real ND data by overriding config paths at runtime
    agent = BuildingDataAgent.__new__(BuildingDataAgent)
    agent.config = config
    from src.loader import FootprintLoader
    from src.features import FeatureExtractor
    from src.classifier import StructureHintClassifier
    from src.utils import setup_logger
    from collections import Counter
    from loguru import logger

    setup_logger(level="INFO")
    agent.loader = FootprintLoader(config)
    agent.spatial = spatial
    agent.extractor = FeatureExtractor(config)
    agent.classifier = StructureHintClassifier(config)
    agent._stats = {
        "total_processed": 0,
        "matched": 0,
        "distances": [],
        "hints": Counter(),
    }

    records = [
        {"address_id": "test", "lat": 46.81, "lon": -100.80}
    ]

    results = agent.enrich_batch(records)
    for r in results:
        print(r)


if __name__ == "__main__":
    main()

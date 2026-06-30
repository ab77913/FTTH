from __future__ import annotations

import io
import json
import os
import zipfile
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
import requests
from loguru import logger

MS_DATASET_LINKS_URL = (
    "https://minedbuildings.z5.web.core.windows.net/global-buildings/dataset-links.csv"
)
MS_LEGACY_BASE = "https://minedbuildings.z5.web.core.windows.net/legacy/usbuildings-v2"
DEFAULT_SAVE_DIR = "data/footprints"
INDEX_CACHE = Path(DEFAULT_SAVE_DIR) / "dataset-links.csv"

_SSL_VERIFY_WARNED = False


def _requests_verify() -> bool | str:
    """Respect FTTH_SSL_VERIFY / FTTH_CA_BUNDLE (same as geocoder agents)."""
    if os.environ.get("FTTH_SSL_VERIFY", "1").lower() in ("0", "false", "no", "off"):
        return False
    custom = os.environ.get("FTTH_CA_BUNDLE", "").strip()
    if custom:
        bundle = Path(custom)
        if bundle.is_file():
            return str(bundle)
        logger.warning("FTTH_CA_BUNDLE path not found: {}", custom)
    try:
        import certifi
        return certifi.where()
    except ImportError:
        return True


def _http_get(url: str, *, timeout: float = 120, stream: bool = False) -> requests.Response:
    global _SSL_VERIFY_WARNED
    verify = _requests_verify()
    if verify is False and not _SSL_VERIFY_WARNED:
        logger.warning(
            "FTTH_SSL_VERIFY=0 — HTTPS certificate verification disabled for footprint downloads"
        )
        _SSL_VERIFY_WARNED = True
    return requests.get(url, timeout=timeout, stream=stream, verify=verify)

# (lat_min, lat_max, lon_min, lon_max) -> Microsoft legacy state filename
US_STATE_BOUNDS: dict[str, tuple[float, float, float, float]] = {
    "Alabama": (30.2, 35.0, -88.5, -84.9),
    "Alaska": (51.0, 71.5, -179.0, -129.0),
    "Arizona": (31.3, 37.0, -114.8, -109.0),
    "Arkansas": (33.0, 36.5, -94.6, -89.6),
    "California": (32.5, 42.0, -124.5, -114.1),
    "Colorado": (37.0, 41.0, -109.1, -102.0),
    "Connecticut": (40.9, 42.1, -73.7, -71.8),
    "Delaware": (38.4, 39.8, -75.8, -75.0),
    "DistrictofColumbia": (38.79, 38.99, -77.12, -76.91),
    "Florida": (24.5, 31.0, -87.6, -80.0),
    "Georgia": (30.4, 35.0, -85.6, -80.8),
    "Hawaii": (18.9, 22.3, -160.3, -154.8),
    "Idaho": (42.0, 49.0, -117.2, -111.0),
    "Illinois": (37.0, 42.5, -91.5, -87.5),
    "Indiana": (37.8, 41.8, -88.1, -84.8),
    "Iowa": (40.4, 43.5, -96.6, -90.1),
    "Kansas": (37.0, 40.0, -102.1, -94.6),
    "Kentucky": (36.5, 39.1, -89.6, -81.9),
    "Louisiana": (29.0, 33.0, -94.0, -88.8),
    "Maine": (43.0, 47.5, -71.1, -66.9),
    "Maryland": (37.9, 39.7, -79.5, -75.0),
    "Massachusetts": (41.2, 42.9, -73.5, -69.9),
    "Michigan": (41.7, 48.3, -90.4, -82.4),
    "Minnesota": (43.5, 49.4, -97.2, -89.5),
    "Mississippi": (30.2, 35.0, -91.7, -88.1),
    "Missouri": (36.0, 40.6, -95.8, -89.1),
    "Montana": (44.4, 49.0, -116.0, -104.0),
    "Nebraska": (40.0, 43.0, -104.1, -95.3),
    "Nevada": (35.0, 42.0, -120.0, -114.0),
    "NewHampshire": (42.7, 45.3, -72.6, -70.6),
    "NewJersey": (38.9, 41.4, -75.6, -73.9),
    "NewMexico": (31.3, 37.0, -109.1, -103.0),
    "NewYork": (40.5, 45.0, -79.8, -71.9),
    "NorthCarolina": (33.8, 36.6, -84.3, -75.5),
    "NorthDakota": (45.9, 49.0, -104.1, -96.5),
    "Ohio": (38.4, 42.0, -84.8, -80.5),
    "Oklahoma": (33.6, 37.0, -103.0, -94.4),
    "Oregon": (42.0, 46.3, -124.6, -116.5),
    "Pennsylvania": (39.7, 42.3, -80.5, -74.7),
    "RhodeIsland": (41.1, 42.0, -71.9, -71.1),
    "SouthCarolina": (32.0, 35.2, -83.4, -78.5),
    "SouthDakota": (42.5, 45.9, -104.1, -96.4),
    "Tennessee": (35.0, 36.7, -90.3, -81.6),
    "Texas": (25.8, 36.5, -106.7, -93.5),
    "Utah": (37.0, 42.0, -114.1, -109.0),
    "Vermont": (42.7, 45.0, -73.4, -71.5),
    "Virginia": (36.5, 39.5, -83.7, -75.2),
    "Washington": (45.5, 49.0, -124.8, -116.9),
    "WestVirginia": (37.2, 40.6, -82.6, -77.7),
    "Wisconsin": (42.5, 47.1, -92.9, -86.8),
    "Wyoming": (41.0, 45.0, -111.1, -104.0),
}


def _fetch_dataset_links() -> pd.DataFrame:
    INDEX_CACHE.parent.mkdir(parents=True, exist_ok=True)

    if INDEX_CACHE.exists():
        age_hours = (pd.Timestamp.now().timestamp() - INDEX_CACHE.stat().st_mtime) / 3600
        if age_hours < 24:
            return pd.read_csv(INDEX_CACHE)

    logger.info("Fetching Microsoft dataset index...")
    response = _http_get(MS_DATASET_LINKS_URL, timeout=120)
    response.raise_for_status()
    INDEX_CACHE.write_bytes(response.content)
    return pd.read_csv(io.BytesIO(response.content))


def _filter_us_rows(df: pd.DataFrame) -> pd.DataFrame:
    mask = df["Location"].astype(str).str.contains(
        r"United\s*States|UnitedStates",
        case=False,
        na=False,
        regex=True,
    )
    return df[mask].copy()


def lat_lon_to_quadkey(lat: float, lon: float, level: int) -> str:
    import math

    lat = max(min(lat, 85.05112878), -85.05112878)
    x = int((lon + 180.0) / 360.0 * (1 << level))
    sin_lat = math.sin(math.radians(lat))
    y = int((0.5 - math.log((1 + sin_lat) / (1 - sin_lat)) / (4 * math.pi)) * (1 << level))

    quadkey = ""
    for i in range(level, 0, -1):
        digit = 0
        mask = 1 << (i - 1)
        if x & mask:
            digit += 1
        if y & mask:
            digit += 2
        quadkey += str(digit)
    return quadkey


def _state_from_lat_lon(lat: float, lon: float) -> str:
    matches = [
        name
        for name, (lat_min, lat_max, lon_min, lon_max) in US_STATE_BOUNDS.items()
        if lat_min <= lat <= lat_max and lon_min <= lon <= lon_max
    ]
    if not matches:
        raise ValueError(f"No US state found for lat={lat}, lon={lon}")
    return matches[0]


def _normalize_quadkey(value: Any) -> str:
    return str(value).strip().lstrip("0")


def _select_index_url(us_df: pd.DataFrame, lat: float, lon: float) -> str | None:
    for level in (8, 7):
        quadkey = lat_lon_to_quadkey(lat, lon, level)
        matched = us_df[
            us_df["QuadKey"].map(_normalize_quadkey) == _normalize_quadkey(quadkey)
        ]
        if not matched.empty:
            url = str(matched.iloc[0]["Url"])
            logger.info(f"Selected index tile quadkey={quadkey} url={url}")
            return url
    return None


def _legacy_state_url(state_name: str) -> str:
    return f"{MS_LEGACY_BASE}/{state_name}.geojson.zip"


def _download_bytes(url: str) -> bytes:
    logger.info(f"Downloading {url}")
    response = _http_get(url, timeout=300, stream=True)
    response.raise_for_status()
    return response.content


def _save_geojson_from_zip(content: bytes, dest: Path) -> None:
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        members = [m for m in zf.namelist() if m.lower().endswith((".geojson", ".json"))]
        if not members:
            raise RuntimeError("Zip archive does not contain GeoJSON")
        with zf.open(members[0]) as src, dest.open("wb") as out:
            out.write(src.read())


def _save_geojson_from_tile_gz(content: bytes, dest: Path) -> None:
    import gzip

    text = gzip.decompress(content).decode("utf-8")
    features = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        features.append(json.loads(line))

    collection = {"type": "FeatureCollection", "features": features}
    dest.write_text(json.dumps(collection), encoding="utf-8")


def download_by_lat_lon(lat: float, lon: float, save_dir: str = DEFAULT_SAVE_DIR) -> str:
    save_path = Path(save_dir)
    save_path.mkdir(parents=True, exist_ok=True)

    state_name = _state_from_lat_lon(lat, lon)
    file_path = save_path / f"{state_name}.geojson"

    if file_path.exists():
        logger.info(f"Using cached footprints: {file_path}")
        return str(file_path)

    df = _fetch_dataset_links()
    us_df = _filter_us_rows(df)
    if us_df.empty:
        raise RuntimeError("No United States rows found in dataset-links.csv")

    index_url = _select_index_url(us_df, lat, lon)
    if index_url and index_url.lower().endswith((".geojson", ".json")):
        content = _download_bytes(index_url)
        file_path.write_bytes(content)
    elif index_url and index_url.lower().endswith(".csv.gz"):
        content = _download_bytes(index_url)
        _save_geojson_from_tile_gz(content, file_path)
    else:
        legacy_url = _legacy_state_url(state_name)
        content = _download_bytes(legacy_url)
        _save_geojson_from_zip(content, file_path)

    logger.info(f"Saved footprints to {file_path}")
    return str(file_path)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Download Microsoft footprints by lat/lon.")
    parser.add_argument("--lat", type=float, required=True)
    parser.add_argument("--lon", type=float, required=True)
    parser.add_argument("--save-dir", default=DEFAULT_SAVE_DIR)
    args = parser.parse_args()

    path = download_by_lat_lon(args.lat, args.lon, args.save_dir)
    gdf = gpd.read_file(path)
    print(f"Downloaded {len(gdf)} footprints to {path}")

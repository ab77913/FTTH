"""
Fiber Address Vision Pipeline
-------------------------------
For each address (lat/lon) from JSON:
  1. Check PostgreSQL – skip if already processed
  2. Call Google Street View Metadata API – confirm imagery exists
  3a. If imagery available  → fetch 2 Street View images (road-facing + 90° offset)
  3b. If no imagery         → fallback to Google Maps Static API (satellite)
  4. Send image to Azure AI Vision for structured analysis
  5. Store full result + images in PostgreSQL

Usage:
  1. Copy .env.example → .env and fill in your keys
  2. py pipeline.py
"""

import os
import io
import json
import math
import time
import requests
import urllib3
import certifi
import sqlite3
from image_variants import generate_streetview_variants
from image_crops import generate_ocr_crops
from pathlib import Path
from datetime import datetime, timezone
from dotenv import load_dotenv

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

load_dotenv()

# ─── Configuration ────────────────────────────────────────────────────────────

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
AZURE_VISION_ENDPOINT = os.getenv("AZURE_VISION_ENDPOINT", "").rstrip("/")
AZURE_VISION_KEY = os.getenv("AZURE_VISION_KEY")
DATABASE_FILE = "google_street_view_analysis.db"
print("DB FILE =", DATABASE_FILE)

INPUT_JSON = r"output\addresses_extracted.json"
BATCH_SIZE = None  # change to None to process all records
IMAGE_SIZE = "640x640"
SV_ZOOM = 90  # Street View field-of-view degrees
SAT_ZOOM = 21  # Satellite zoom level

# ─── Disk Cache ───────────────────────────────────────────────────────────────
# Avoids repeat Google API calls and Azure Vision charges on re-runs.
# Layout:
#   cache/metadata/<lat>_<lon>.json
#   cache/images/<lat>_<lon>_<type>.jpg
#   cache/vision/<lat>_<lon>.json


CACHE_DIR = Path("cache")

CACHE_METADATA_DIR = CACHE_DIR / "metadata"
CACHE_IMAGES_DIR = CACHE_DIR / "images"
CACHE_VISION_DIR = CACHE_DIR / "vision"


def _setup_cache_dirs():

    for d in (CACHE_METADATA_DIR, CACHE_IMAGES_DIR, CACHE_VISION_DIR):

        d.mkdir(parents=True, exist_ok=True)


def _coord_key(lat: float, lon: float) -> str:
    """Filesystem-safe key from coordinates, e.g. '36.123456_-86.654321'."""
    return f"{lat:.6f}_{lon:.6f}"


# ─── Database ─────────────────────────────────────────────────────────────────

DDL = """
CREATE TABLE IF NOT EXISTS address_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    latitude REAL NOT NULL,
    longitude REAL NOT NULL,

    address TEXT,
    city TEXT,
    postal_code TEXT,
    zip_code TEXT,

    imagery_source TEXT,

    structure_type TEXT,
    visible_units_min INTEGER,
    visible_units_max INTEGER,
    floor_count INTEGER,

    multiple_entrances INTEGER,
    multiple_mailboxes INTEGER,
    commercial_signage INTEGER,
    under_construction INTEGER,

    image_quality TEXT,

    vision_score REAL,
    metadata_score REAL,
    agreement_score REAL,
    ocr_score REAL,
    quality_score REAL,
    aerial_score REAL,

    confidence INTEGER,

    raw_vision TEXT,

    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    UNIQUE(latitude, longitude)
);

CREATE TABLE IF NOT EXISTS address_images (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    result_id INTEGER,
    image_type TEXT,
    image_url TEXT,
    image_data BLOB,

    FOREIGN KEY(result_id)
    REFERENCES address_results(id)
    ON DELETE CASCADE
);
"""


def get_connection():
    conn = sqlite3.connect(DATABASE_FILE)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def setup_database(conn):

    statements = DDL.split(";")

    cur = conn.cursor()

    for stmt in statements:

        stmt = stmt.strip()

        if stmt:
            cur.execute(stmt)

    conn.commit()

    print("[DB] Tables ready.")


def already_processed(conn, lat: float, lon: float):

    cur = conn.cursor()

    cur.execute(
        "SELECT id FROM address_results WHERE latitude=? AND longitude=?",
        (lat, lon),
    )

    row = cur.fetchone()

    return row[0] if row else None


def save_result(
    conn,
    record: dict,
    imagery_source: str,
    structured: dict,
    raw_vision: dict,
    images: list,
):
    """Insert analysis result and image blobs; return the new row id."""

    cur = conn.cursor()

    # ── Insert main analysis row ─────────────────────────────

    cur.execute(
        """
        INSERT OR IGNORE INTO address_results (
            latitude,
            longitude,
            address,
            city,
            postal_code,
            zip_code,

            imagery_source,

            structure_type,
            visible_units_min,
            visible_units_max,
            floor_count,

            multiple_entrances,
            multiple_mailboxes,
            commercial_signage,
            under_construction,

            image_quality,

            vision_score,
            metadata_score,
            agreement_score,
            ocr_score,
            quality_score,
            aerial_score,

            confidence,

            raw_vision

        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            float(record["latitude"]),
            float(record["longitude"]),
            record.get("address"),
            record.get("city"),
            record.get("postal_code"),
            record.get("zip_code"),
            imagery_source,
            structured["structure_type"],
            structured["visible_units_min"],
            structured["visible_units_max"],
            structured["floor_count"],
            int(structured["multiple_entrances"]),
            int(structured["multiple_mailboxes"]),
            int(structured["commercial_signage"]),
            int(structured["under_construction"]),
            structured["image_quality"],
            structured["vision_score"],
            structured["metadata_score"],
            structured["agreement_score"],
            structured["ocr_score"],
            structured["quality_score"],
            structured["aerial_score"],
            structured["confidence"],
            json.dumps(raw_vision),
        ),
    )

    # ── Get inserted row id ─────────────────────────────────

    result_id = cur.lastrowid

    # Duplicate record
    if result_id == 0:

        conn.rollback()

        return None

    # ── Insert images ───────────────────────────────────────

    print("IMAGE TYPES BEFORE DB:", [x[0] for x in images])

    for img_type, img_bytes, img_url in images:

        cur.execute(
            """
            INSERT INTO address_images (
                result_id,
                image_type,
                image_url,
                image_data
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                result_id,
                img_type,
                img_url,
                img_bytes,
            ),
        )

    conn.commit()

    return result_id


# ─── Cache helpers ────────────────────────────────────────────────────────────


def load_metadata_cache(lat: float, lon: float) -> dict | None:
    path = os.path.join(CACHE_METADATA_DIR, f"{_coord_key(lat, lon)}.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return None


def save_metadata_cache(lat: float, lon: float, data: dict) -> None:
    path = os.path.join(CACHE_METADATA_DIR, f"{_coord_key(lat, lon)}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


def load_image_cache(lat: float, lon: float, img_type: str) -> bytes | None:
    """img_type: 'streetview_primary' | 'streetview_90' | 'satellite'"""
    path = os.path.join(CACHE_IMAGES_DIR, f"{_coord_key(lat, lon)}_{img_type}.jpg")
    if os.path.exists(path):
        with open(path, "rb") as f:
            return f.read()
    return None


def save_image_cache(lat: float, lon: float, img_type: str, data: bytes) -> None:
    path = os.path.join(CACHE_IMAGES_DIR, f"{_coord_key(lat, lon)}_{img_type}.jpg")
    with open(path, "wb") as f:
        f.write(data)


def load_vision_cache(lat: float, lon: float) -> dict | None:
    path = os.path.join(CACHE_VISION_DIR, f"{_coord_key(lat, lon)}.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return None


def save_vision_cache(lat: float, lon: float, data: dict) -> None:
    path = os.path.join(CACHE_VISION_DIR, f"{_coord_key(lat, lon)}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


def load_satellite_vision_cache(lat: float, lon: float) -> dict | None:
    path = os.path.join(CACHE_VISION_DIR, f"{_coord_key(lat, lon)}_satellite.json")

    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    return None


def save_satellite_vision_cache(lat: float, lon: float, data: dict) -> None:

    path = os.path.join(CACHE_VISION_DIR, f"{_coord_key(lat, lon)}_satellite.json")

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


def save_ocr_crop_cache(lat, lon, crop_name, data):

    path = CACHE_DIR / "vision" / f"{_coord_key(lat, lon)}_{crop_name}.json"

    with open(path, "w", encoding="utf-8") as f:

        json.dump(data, f)


def load_ocr_crop_cache(lat, lon, crop_name):

    path = CACHE_DIR / "vision" / f"{_coord_key(lat, lon)}_{crop_name}.json"

    if path.exists():

        with open(path, "r", encoding="utf-8") as f:

            return json.load(f)

    return None


# ─── Google Street View ───────────────────────────────────────────────────────


def streetview_metadata(lat: float, lon: float) -> tuple[bool, dict]:
    """Return (available, metadata_dict) for the closest Street View panorama."""
    cached = load_metadata_cache(lat, lon)
    if cached is not None:
        print(f"    [CACHE] SV metadata hit  ({_coord_key(lat, lon)})")
        return cached.get("status") == "OK", cached

    resp = requests.get(
        "https://maps.googleapis.com/maps/api/streetview/metadata",
        params={"location": f"{lat},{lon}", "key": GOOGLE_API_KEY},
        timeout=10,
        verify=False,
    )

    data = resp.json()
    save_metadata_cache(lat, lon, data)
    return data.get("status") == "OK", data


def _bearing(lat1, lon1, lat2, lon2) -> float:
    """Compass bearing (degrees) from point-1 to point-2."""
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    d_lon = lon2 - lon1
    x = math.sin(d_lon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(
        d_lon
    )
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def road_facing_heading(sv_meta: dict, target_lat: float, target_lon: float) -> float:
    """
    Heading from the Street View camera position toward the structure.
    Falls back to 0° if metadata location is missing.
    """
    loc = sv_meta.get("location", {})
    cam_lat = loc.get("lat")
    cam_lon = loc.get("lng")
    if cam_lat is not None and cam_lon is not None:
        return _bearing(cam_lat, cam_lon, target_lat, target_lon)
    return 0.0


def fetch_streetview_image(
    lat: float, lon: float, heading: float, fov: int = 90
) -> tuple[bytes | None, str]:
    """Download one Street View Static image; return (bytes, url)."""
    # _s2 suffix ensures scale=2 (1280×1280) images are cached separately
    # from any old scale=1 (640×640) files written before this was added.
    img_type = f"sv_{round(heading, 1)}_fov{fov}_s2"

    cached = load_image_cache(lat, lon, img_type)

    if cached is not None:
        print(f"    [CACHE] Image hit  ({img_type})")

        params = {
            "size": IMAGE_SIZE,
            "scale": 2,
            "source": "outdoor",
            "location": f"{lat},{lon}",
            "heading": round(heading, 1),
            "pitch": "0",
            "fov": str(fov),
            "key": GOOGLE_API_KEY,
        }

        cached_url = (
            requests.Request(
                "GET", "https://maps.googleapis.com/maps/api/streetview", params=params
            )
            .prepare()
            .url
        )
        safe_cached_url = cached_url.split("&key=")[0] + "&key=HIDDEN"
        return cached, safe_cached_url

    # scale=2 with size=640x640 → 1280×1280 high-DPI image (same API cost as scale=1)
    # source=outdoor ensures only outdoor panoramas are used
    params = {
        "size": IMAGE_SIZE,
        "scale": 2,
        "source": "outdoor",
        "location": f"{lat},{lon}",
        "heading": round(heading, 1),
        "pitch": "0",
        "fov": str(fov),
        "key": GOOGLE_API_KEY,
    }

    resp = requests.get(
        "https://maps.googleapis.com/maps/api/streetview",
        params=params,
        timeout=20,
        verify=False,
    )

    url = resp.url
    safe_url = url.split("&key=")[0] + "&key=HIDDEN"

    if resp.status_code == 200 and resp.headers.get("content-type", "").startswith(
        "image"
    ):
        save_image_cache(lat, lon, img_type, resp.content)
        return resp.content, safe_url

    return None, safe_url


def fetch_satellite_image(lat: float, lon: float) -> tuple[bytes | None, str]:
    """Download a satellite tile via Google Maps Static API."""

    cached = load_image_cache(lat, lon, "satellite")

    if cached is not None and len(cached) > 0:

        print(f"    [CACHE] Satellite image hit  ({_coord_key(lat, lon)})")

        params = {
            "center": f"{lat},{lon}",
            "zoom": SAT_ZOOM,
            "size": IMAGE_SIZE,
            "scale": 2,
            "maptype": "satellite",
            "key": GOOGLE_API_KEY,
        }

        cached_url = (
            requests.Request(
                "GET", "https://maps.googleapis.com/maps/api/staticmap", params=params
            )
            .prepare()
            .url
        )

        print("DEBUG SAT CACHE LENGTH:", len(cached))

        safe_cached_url = cached_url.split("&key=")[0] + "&key=HIDDEN"
        return cached, safe_cached_url

    params = {
        "center": f"{lat},{lon}",
        "zoom": SAT_ZOOM,
        "size": IMAGE_SIZE,
        "scale": 2,
        "maptype": "satellite",
        "key": GOOGLE_API_KEY,
    }

    sat_url = "https://maps.googleapis.com/maps/api/staticmap"
    try:
        resp = requests.get(sat_url, params=params, timeout=20, verify=False)
    except requests.exceptions.RequestException as exc:
        print(f"    [SAT] Connection error — {exc}")
        safe_url = (
            requests.Request(
                "GET", sat_url, params={k: v for k, v in params.items() if k != "key"}
            )
            .prepare()
            .url
        )
        return None, safe_url

    url = resp.url
    safe_url = url.split("&key=")[0] + "&key=HIDDEN"
    content_type = resp.headers.get("content-type", "")

    print(
        f"    [SAT] status={resp.status_code}  content-type={content_type}  size={len(resp.content)} bytes"
    )

    if resp.status_code == 200 and content_type.startswith("image"):
        save_image_cache(lat, lon, "satellite", resp.content)
        return resp.content, safe_url

    if resp.status_code != 200:
        print(
            f"    [SAT] Google satellite failed — HTTP {resp.status_code}. "
            f"Check that 'Maps Static API' is enabled and billing is active for your key."
        )
    else:
        print(
            f"    [SAT] Google satellite returned non-image content — "
            f"response preview: {resp.content[:200]}"
        )

    return None, safe_url


def fetch_esri_satellite_image(lat: float, lon: float) -> tuple[bytes | None, str]:
    """Download ESRI World Imagery tile."""

    cache_key = "esri_satellite"

    cached = load_image_cache(lat, lon, cache_key)

    if cached is not None and len(cached) > 0:

        print(f"    [CACHE] ESRI satellite hit " f"({_coord_key(lat, lon)})")

        return cached, "cached_esri"

    bbox_offset = 0.0005

    url = (
        "https://services.arcgisonline.com/"
        "ArcGIS/rest/services/"
        "World_Imagery/MapServer/export"
    )

    params = {
        "bbox": f"{lon-bbox_offset},"
        f"{lat-bbox_offset},"
        f"{lon+bbox_offset},"
        f"{lat+bbox_offset}",
        "bboxSR": "4326",
        "imageSR": "4326",
        "size": "1280,1280",
        "format": "jpg",
        "f": "image",
    }

    try:
        resp = requests.get(url, params=params, timeout=20, verify=False)
    except requests.exceptions.RequestException as exc:
        print(f"    [ESRI] Connection error — {exc}")
        return None, url

    content_type = resp.headers.get("content-type", "")

    print(
        f"    [ESRI] status={resp.status_code}  content-type={content_type}  size={len(resp.content)} bytes"
    )

    if resp.status_code == 200 and content_type.startswith("image"):

        save_image_cache(lat, lon, cache_key, resp.content)

        return resp.content, resp.url

    if resp.status_code != 200:
        print(f"    [ESRI] Satellite failed — HTTP {resp.status_code}.")
    else:
        print(
            f"    [ESRI] Satellite returned non-image content — "
            f"response preview: {resp.content[:200]}"
        )

    return None, resp.url


# ─── Azure AI Vision ──────────────────────────────────────────────────────────


def azure_vision_analyze(image_bytes: bytes) -> dict:
    url = f"{AZURE_VISION_ENDPOINT}/computervision/imageanalysis:analyze"

    resp = requests.post(
        url,
        params={
            "api-version": "2024-02-01",
            "features": "objects,tags,read",
            "language": "en",
        },
        headers={
            "Ocp-Apim-Subscription-Key": AZURE_VISION_KEY,
            "Content-Type": "application/octet-stream",
        },
        data=image_bytes,
        timeout=30,
        verify=False,
    )

    print("Azure response:", resp.status_code)
    print(resp.text)

    resp.raise_for_status()
    return resp.json()


def get_tag_confidence(visions: list[dict], keywords: set[str]) -> float:
    """
    Returns highest Azure tag confidence across multiple images.
    """
    max_conf = 0.0

    for vision in visions:
        if not vision:
            continue

        for tag in vision.get("tagsResult", {}).get("values", []):
            name = tag["name"].lower()
            conf = float(tag["confidence"])

            if name in keywords:
                max_conf = max(max_conf, conf)

    return max_conf


def classify_structure_from_tags(tags: set[str]):
    HIGH_RISE = {"high-rise", "skyscraper", "apartment complex", "condo tower"}
    LOW_RISE = {"apartment building", "apartment", "low-rise", "flat", "condominium"}
    TOWNHOUSE = {"townhouse", "row house", "terraced house", "rowhouse"}
    HOUSE = {
        "house",
        "home",
        "bungalow",
        "cottage",
        "residential",
        "single family",
        "single-family",
        "detached",
    }
    COMMERCIAL = {
        "store",
        "shop",
        "commercial",
        "office",
        "retail",
        "restaurant",
        "business",
        "storefront",
    }
    INDUSTRIAL = {"industrial", "warehouse", "factory", "manufacturing"}

    if tags & HIGH_RISE:
        return "high_rise_apt"
    elif tags & LOW_RISE:
        return "low_rise_apt"
    elif tags & TOWNHOUSE:
        return "townhouse"
    elif tags & HOUSE:
        return "detached_house"
    elif tags & COMMERCIAL:
        return "commercial"
    elif tags & INDUSTRIAL:
        return "industrial"

    return "unclear"


def extract_ocr_text(vision_result: dict) -> str:

    texts = []

    read_blocks = vision_result.get("readResult", {}).get("blocks", [])

    for block in read_blocks:

        for line in block.get("lines", []):

            txt = line.get("text", "").strip()

            if txt:

                texts.append(txt)

    return " ".join(texts)


def parse_vision_output(
    vision: dict,
    record: dict,
    second_vision: dict | None = None,
    sat_vision: dict | None = None,
    ocr_crop_results=None,
    esri_vision=None,
    ocr_match_found=False,
) -> dict:
    """
    Map Azure AI Vision response → structured fields matching the pipeline spec.

    Returns:
        structure_type      : detached_house | townhouse | low_rise_apt |
                              high_rise_apt | commercial | mixed_use |
                              industrial | unclear
        visible_units_min   : int or None
        visible_units_max   : int or None
        floor_count         : int or None
        multiple_entrances  : bool
        multiple_mailboxes  : bool
        commercial_signage  : bool
        under_construction  : bool
        image_quality       : good | partial | obstructed | no_structure
    """
    # Collect all signals
    tags = {t["name"].lower() for t in vision.get("tagsResult", {}).get("values", [])}
    objects = {
        o["tags"][0]["name"].lower()
        for o in vision.get("objectsResult", {}).get("values", [])
    }
    caption = ""
    cap_conf = 1.0
    dense = []
    all_text = ""

    read_blocks = vision.get("readResult", {}).get("blocks", [])
    ocr_lines = []

    for block in read_blocks:
        for line in block.get("lines", []):
            txt = line.get("text", "").lower()
            if txt:
                ocr_lines.append(txt)

    all_text = " ".join(ocr_lines)
    address_type = (record.get("address_type") or "").lower()
    unit_number = (record.get("unit_number") or "").lower()

    # ── Structure type ──────────────────────────────────────────────────────
    structure_type = "unclear"

    HIGH_RISE = {"high-rise", "skyscraper", "apartment complex", "condo tower"}
    LOW_RISE = {"apartment building", "apartment", "low-rise", "flat", "condominium"}
    TOWNHOUSE = {"townhouse", "row house", "terraced house", "rowhouse"}
    HOUSE = {
        "house",
        "home",
        "bungalow",
        "cottage",
        "residential",
        "single family",
        "single-family",
        "detached",
    }
    COMMERCIAL = {
        "store",
        "shop",
        "commercial",
        "office",
        "retail",
        "restaurant",
        "business",
        "storefront",
    }
    INDUSTRIAL = {"industrial", "warehouse", "factory", "manufacturing"}

    if tags & HIGH_RISE or any(k in all_text for k in HIGH_RISE):
        structure_type = "high_rise_apt"
    elif tags & LOW_RISE or any(k in all_text for k in LOW_RISE):
        structure_type = "low_rise_apt"
    elif tags & TOWNHOUSE or any(k in all_text for k in TOWNHOUSE):
        structure_type = "townhouse"
    elif tags & HOUSE or any(k in all_text for k in HOUSE):
        structure_type = "detached_house"
    elif tags & COMMERCIAL or any(k in all_text for k in COMMERCIAL):
        structure_type = "commercial"
    elif tags & INDUSTRIAL or any(k in all_text for k in INDUSTRIAL):
        structure_type = "industrial"

    # ── Floor count (heuristic from dense captions) ─────────────────────────
    floor_map = {
        "one-story": 1,
        "single-story": 1,
        "one story": 1,
        "two-story": 2,
        "double-story": 2,
        "two story": 2,
        "three-story": 3,
        "three story": 3,
        "four-story": 4,
        "four story": 4,
        "five-story": 5,
    }
    floor_count = None
    for phrase, num in floor_map.items():
        if phrase in all_text:
            floor_count = num
            break

    # ── Visible units range ─────────────────────────────────────────────────
    unit_ranges = {
        "high_rise_apt": (10, 50),
        "low_rise_apt": (2, 8),
        "townhouse": (2, 4),
        "detached_house": (1, 1),
        "commercial": (1, 1),
        "mixed_use": (2, 10),
        "industrial": (1, 1),
        "unclear": (None, None),
    }
    visible_units_min, visible_units_max = unit_ranges.get(structure_type, (None, None))

    # ── Boolean indicators ──────────────────────────────────────────────────
    multiple_entrances = len(
        [o for o in objects if "door" in o or "entrance" in o or "gate" in o]
    ) > 1 or any(
        k in all_text for k in ["multiple entrances", "several doors", "multiple doors"]
    )
    multiple_mailboxes = any(
        k in all_text for k in ["mailbox", "mailboxes", "letter box", "mail slot"]
    )
    commercial_signage = bool(
        tags & {"sign", "signage", "billboard", "banner", "logo", "advertisement"}
        or any(k in all_text for k in ["sign on", "storefront sign", "business sign"])
    )
    under_construction = bool(
        tags
        & {
            "construction",
            "scaffold",
            "scaffolding",
            "crane",
            "building site",
            "construction site",
            "under construction",
        }
        or any(
            k in all_text
            for k in ["under construction", "being built", "construction site"]
        )
    )

    # ── Image quality ───────────────────────────────────────────────────────
    image_quality = "good"
    poor_signals = {
        "blurry",
        "dark",
        "obstructed",
        "blocked",
        "unclear",
        "fog",
        "night",
        "low visibility",
    }
    if not tags and not objects:
        image_quality = "no_structure"
    elif tags & poor_signals or any(k in all_text for k in poor_signals):
        image_quality = "obstructed"
    elif cap_conf < 0.35 and caption:
        image_quality = "partial"
    # =========================
    # HYBRID CONFIDENCE SCORING
    # =========================

    vision_score = 0.0
    metadata_score = 0.0
    aerial_score = 0.0
    agreement_score = 0.0
    if ocr_match_found:

        ocr_score = 25

    else:

        ocr_score = 0
    quality_score = 0.0

    # ---- 1. Vision score (50 max) ----
    structure_keywords = {
        "detached_house": {"house", "home", "cottage", "bungalow"},
        "townhouse": {"townhouse", "row house"},
        "low_rise_apt": {"apartment", "condominium"},
        "high_rise_apt": {"high-rise", "skyscraper"},
        "commercial": {"store", "office", "restaurant", "shop"},
        "industrial": {"warehouse", "factory"},
    }

    if structure_type in structure_keywords:
        strongest = get_tag_confidence(
            [vision, second_vision], structure_keywords[structure_type]
        )
        vision_score = strongest * 50

    # ---- 2A. Metadata validation (20 max) ----
    if "residential" in address_type and structure_type in [
        "detached_house",
        "townhouse",
        "low_rise_apt",
        "high_rise_apt",
    ]:
        metadata_score = 20

    elif "commercial" in address_type and structure_type == "commercial":
        metadata_score = 20

    # ---- 2B. Aerial evidence (15 max) ----

    if sat_vision:

        aerial_tags = {
            t["name"].lower()
            for t in sat_vision.get("tagsResult", {}).get("values", [])
        }

        # ── Merge ESRI tags ─────────────────────

        if esri_vision:

            esri_tags = {
                t["name"].lower()
                for t in esri_vision.get("tagsResult", {}).get("values", [])
            }

            aerial_tags |= esri_tags

        residential_aerial = {
            "house",
            "property",
            "yard",
            "driveway",
            "roof",
            "residential area",
        }

        commercial_aerial = {
            "parking lot",
            "office",
            "warehouse",
            "industrial",
            "commercial building",
        }

        if structure_type in [
            "detached_house",
            "townhouse",
            "low_rise_apt",
            "high_rise_apt",
        ]:

            matches = len(aerial_tags & residential_aerial)

            aerial_score = min(matches * 3, 15)

        elif structure_type == "commercial":

            matches = len(aerial_tags & commercial_aerial)

            aerial_score = min(matches * 3, 15)

    # ---- 2C. OCR crop residential boost (10 max) ----

    crop_boost = 0

    if ocr_crop_results:

        residential_crop_tags = {
            "home",
            "house",
            "property",
            "building",
            "yard",
            "porch",
            "window",
            "real estate",
        }

        best_crop_score = 0

        for crop_name, crop_vision in ocr_crop_results:

            crop_tags = crop_vision.get("tagsResult", {}).get("values", [])

            crop_matches = 0

            for tag in crop_tags:

                tag_name = tag.get("name", "").lower()

                tag_conf = tag.get("confidence", 0)

                if tag_name in residential_crop_tags and tag_conf >= 0.85:

                    crop_matches += 1

            best_crop_score = max(best_crop_score, crop_matches)

        crop_boost = min(best_crop_score * 2, 10)

        vision_score += crop_boost

    # ---- 3. Multi-angle agreement (10 max) ----

    agreement_matches = 0

    for v in [vision, second_vision]:

        if not v:
            continue

        v_tags = {t["name"].lower() for t in v.get("tagsResult", {}).get("values", [])}

        v_struct = classify_structure_from_tags(v_tags)

        if v_struct == structure_type and structure_type != "unclear":
            agreement_matches += 1

    agreement_score = min(agreement_matches * 5, 10)

    # ---- 4. OCR validation (10 max) ----
    commercial_words = {"office", "store", "shop", "suite"}
    residential_words = {"apt", "apartment", "unit"}

    if any(word in all_text for word in commercial_words):
        if structure_type == "commercial":
            ocr_score = 10

    elif any(word in all_text for word in residential_words):
        if structure_type in ["low_rise_apt", "high_rise_apt", "townhouse"]:
            ocr_score = 10

    # ---- 5. Image quality (10 max) ----
    if image_quality == "good":
        quality_score = 10
    elif image_quality == "partial":
        quality_score = 5

    # ---- Final confidence ----
    confidence = round(
        min(
            vision_score
            + metadata_score
            + aerial_score
            + agreement_score
            + ocr_score
            + quality_score,
            100,
        )
    )

    return {
        "structure_type": structure_type,
        "visible_units_min": visible_units_min,
        "visible_units_max": visible_units_max,
        "floor_count": floor_count,
        "multiple_entrances": multiple_entrances,
        "multiple_mailboxes": multiple_mailboxes,
        "commercial_signage": commercial_signage,
        "under_construction": under_construction,
        "image_quality": image_quality,
        "vision_score": vision_score,
        "metadata_score": metadata_score,
        "aerial_score": aerial_score,
        "agreement_score": agreement_score,
        "ocr_score": ocr_score,
        "quality_score": quality_score,
        "confidence": confidence,
        "confidence_breakdown": {
            "vision_score": round(vision_score, 2),
            "metadata_score": metadata_score,
            "aerial_score": aerial_score,
            "agreement_score": agreement_score,
            "ocr_score": ocr_score,
            "quality_score": quality_score,
        },
    }


# ─── Single Record Pipeline ───────────────────────────────────────────────────


def process_record(conn, record: dict):
    lat = float(record["latitude"])
    lon = float(record["longitude"])
    addr = record.get("address", f"{lat},{lon}")

    # ── Step 1: Skip if already in DB ──────────────────────────────────────
    existing_id = already_processed(conn, lat, lon)
    if existing_id:
        print(f"    [SKIP] Already in DB (id={existing_id}): {addr}")
        return "skipped"

    print(f"    Address : {addr}")
    print(f"    Coords  : {lat}, {lon}")

    # ── Step 2: Street View metadata check ─────────────────────────────────
    sv_available, sv_meta = streetview_metadata(lat, lon)
    print(f"    StreetView available: {sv_available}")

    images = []
    imagery_source = ""
    primary_image = None  # bytes sent to Azure Vision
    secondary_image = None
    variants = []

    # ── Step 3a: Street View images ─────────────────────────────────────────
    if sv_available:

        imagery_source = "streetview"

        heading = road_facing_heading(sv_meta, lat, lon)

        variants = generate_streetview_variants(
            lat, lon, heading, fetch_streetview_image
        )

        images.extend(variants)

        if variants:
            primary_image = variants[0][1]

        if len(variants) > 1:
            secondary_image = variants[1][1]

    # ── Step 3b: Satellite image (always fetch for aerial evidence) ─────────

    satellite_image = None
    satellite_url = None

    img_sat, url_sat = fetch_satellite_image(lat, lon)
    print(f"    Satellite URL : {url_sat}")

    # ── ESRI satellite ─────────────────────────────

    img_esri, url_esri = fetch_esri_satellite_image(lat, lon)

    if img_esri:

        images.append(("esri_satellite", img_esri, url_esri))

        print(f"    ESRI satellite " f"({len(img_esri)//1024} KB)")

    # ── Google satellite ───────────────────────────

    if img_sat:

        satellite_image = img_sat

        satellite_url = url_sat

        images.append(("satellite", img_sat, url_sat))

        print(f"    Satellite image " f"({len(img_sat)//1024} KB)")

        # fallback only if Street View missing
        if not sv_available or primary_image is None:

            imagery_source = "satellite"

            primary_image = img_sat

    if primary_image is None:

        print(f"    [WARN] No image obtained — skipping.")

        return "skipped"

    # ── Step 4: Azure AI Vision (with cache) ───────────────────────────────

    raw_vision = load_vision_cache(lat, lon)

    second_vision = None
    sat_vision = None
    esri_vision = None

    variant_visions = []

    ocr_crop_results = []

    global_ocr_results = []

    best_ocr_crop = None

    best_ocr_text_len = 0

    if raw_vision is not None:

        print(f"    [CACHE] Vision hit  " f"({_coord_key(lat, lon)})")

        second_vision = raw_vision

    else:

        for image_type, img_bytes, img_url in variants:

            vision_result = azure_vision_analyze(img_bytes)

            if vision_result:

                extracted_text = extract_ocr_text(vision_result)

                if extracted_text:

                    global_ocr_results.append((image_type, extracted_text))

                    print(f"    OCR ({image_type}) : " f"{extracted_text}")

                variant_visions.append((image_type, vision_result, img_url))

                print(f"    Vision analyzed : " f"{image_type}")

        best_vision = None

        best_house_conf = 0

        for image_type, vision, img_url in variant_visions:

            tags = vision.get("tagsResult", {}).get("values", [])

            for tag in tags:

                tag_name = tag.get("name", "").lower()

                tag_conf = tag.get("confidence", 0)

                if tag_name in ["house", "home", "property"]:

                    if tag_conf > best_house_conf:

                        best_house_conf = tag_conf

                        best_vision = vision

                        print(
                            f"    BEST VIEW -> "
                            f"{image_type} "
                            f"({round(tag_conf * 100, 1)}%)"
                        )

        if best_vision is not None:

            raw_vision = best_vision

            save_vision_cache(lat, lon, raw_vision)

        elif variant_visions:
            # Fallback: no house/home/property tag found (e.g. commercial address).
            # Pick the variant with the highest total tag count so we still have
            # a valid vision dict for scoring and DB insert.
            raw_vision = max(
                variant_visions,
                key=lambda x: len(x[1].get("tagsResult", {}).get("values", [])),
            )[1]

            print(
                f"    [FALLBACK] No house/home/property tag — "
                f"using richest-tag variant for analysis."
            )

            save_vision_cache(lat, lon, raw_vision)

    # ── Satellite Vision ─────────────────────────────

    sat_vision = load_satellite_vision_cache(lat, lon)

    if sat_vision is not None:

        print(f"    [CACHE] Satellite vision hit " f"({_coord_key(lat, lon)})")

    elif satellite_image:

        sat_vision = azure_vision_analyze(satellite_image)

        sat_text = extract_ocr_text(sat_vision)

        if sat_text:

            global_ocr_results.append(("google_satellite", sat_text))

            print(f"    OCR (Google Satellite) : " f"{sat_text}")

        save_satellite_vision_cache(lat, lon, sat_vision)

    # ── ESRI Vision ─────────────────────────────

    if img_esri:

        print("    Analyzing ESRI imagery...")

        esri_vision = azure_vision_analyze(img_esri)

        esri_text = extract_ocr_text(esri_vision)

        if esri_text:

            global_ocr_results.append(("esri_satellite", esri_text))

            print(f"    OCR (ESRI) : " f"{esri_text}")

    if len(variant_visions) > 1:

        second_vision = variant_visions[1][1]

    # ── OCR crop analysis ─────────────────────────────────

    best_image_bytes = None

    for img_type, img_bytes, img_url in images:

        if img_type == "streetview_zoom2":

            best_image_bytes = img_bytes

            break

    if best_image_bytes:

        crops = generate_ocr_crops(best_image_bytes)

        for crop_name, crop_bytes in crops:

            print(f"    OCR Crop : " f"{crop_name}")

            crop_vision = load_ocr_crop_cache(lat, lon, crop_name)

            if crop_vision is not None:

                print(f"    [CACHE] OCR crop hit " f"({crop_name})")

            else:

                crop_vision = azure_vision_analyze(crop_bytes)

                if crop_vision:

                    save_ocr_crop_cache(lat, lon, crop_name, crop_vision)

            if crop_vision:

                ocr_crop_results.append((crop_name, crop_vision))

                ocr_text = json.dumps(crop_vision)

                if len(ocr_text) > best_ocr_text_len:

                    best_ocr_text_len = len(ocr_text)

                    best_ocr_crop = crop_name

    # ── Best OCR crop ─────────────────────────────

    if best_ocr_crop:

        print(f"    BEST OCR VIEW : " f"{best_ocr_crop}")

    # ── House number matching ─────────────────────

    detected_house_number = None

    ocr_match_found = False

    expected_house_number = record.get("house_number", "").strip()

    for source_name, extracted_text in global_ocr_results:

        print(f"    OCR TEXT ({source_name}) : " f"{extracted_text}")

        if expected_house_number and expected_house_number in extracted_text:

            detected_house_number = expected_house_number

            print(
                f"    HOUSE NUMBER MATCH "
                f"({source_name}) : "
                f"{detected_house_number}"
            )

            ocr_match_found = True

            break

    structured = parse_vision_output(
        raw_vision,
        record,
        second_vision,
        sat_vision,
        ocr_crop_results,
        esri_vision,
        ocr_match_found,
    )

    caption = raw_vision.get("captionResult", {}).get("text", "n/a")

    print(f"    Caption : {caption}")

    print(f"    Result  : {structured}")

    # ── Step 5: Store in PostgreSQL ─────────────────────────────────────────
    result_id = save_result(
        conn, record, imagery_source, structured, raw_vision, images
    )

    if result_id:
        print(f"    [OK] Stored → id={result_id}")
        return "ok"
    else:
        print(f"    [WARN] Duplicate detected, skipped insert.")
        return "skipped"


# ─── Main ─────────────────────────────────────────────────────────────────────


def main():
    # Validate config
    missing = [
        k
        for k, v in {
            "GOOGLE_API_KEY": GOOGLE_API_KEY,
            "AZURE_VISION_ENDPOINT": AZURE_VISION_ENDPOINT,
            "AZURE_VISION_KEY": AZURE_VISION_KEY,
        }.items()
        if not v
    ]
    if missing:
        raise SystemExit(
            f"[ERROR] Missing env vars: {', '.join(missing)}\n"
            "Copy .env.example → .env and fill in your keys."
        )

    # Load JSON records
    with open(INPUT_JSON, encoding="utf-8") as f:
        all_records = json.load(f)

    batch = all_records[:BATCH_SIZE] if BATCH_SIZE else all_records
    print(f"[INFO] Processing {len(batch)} of {len(all_records)} records\n")

    _setup_cache_dirs()
    conn = get_connection()
    setup_database(conn)

    ok = skipped = errors = 0

    for i, record in enumerate(batch, 1):
        print(f"\n[{i}/{len(batch)}] ──────────────────────────")

        try:
            result = process_record(conn, record)

            if result == "ok":
                ok += 1
            elif result == "skipped":
                skipped += 1

        except Exception as exc:
            print(f"    [ERROR] {record.get('address','?')}: {exc}")
            errors += 1

        time.sleep(0.4)  # gentle rate limiting
    conn.close()
    print(f"\n[DONE] ok={ok}  skipped={skipped}  errors={errors}")


if __name__ == "__main__":
    main()

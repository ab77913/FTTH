"""
Agent 5 vision pipeline — ported from reference/google_street_view_analysis.

Hybrid confidence scoring with OCR house-number validation (90% threshold)
and a final confidence floor of 90 when a high-confidence house number match is found.
"""

from __future__ import annotations
from data_ingestion.config.paths import PROJECT_ROOT, STREET_VIEW_VENDOR

import json
import logging
import math
import os
import time
from pathlib import Path
from typing import Any, Callable

import requests

from data_ingestion.utils.agent5_image_utils import (
    CLASSIFICATION_ANGLE_OFFSETS,
    CLASSIFICATION_FOV,
    clarify_image,
    ensure_rgb_jpeg_bytes,
    fetch_variant,
    generate_ocr_crops,
)
from data_ingestion.utils.agent5_input import simplify_structure_type
from data_ingestion.utils.agent5_iterative_search import (
    MAX_ITERATIONS,
    iterative_house_number_search,
)
from data_ingestion.utils.agent5_paddle_ocr import (
    detect_house_number_with_paddle,
    detect_house_number_with_tesseract,
)
from data_ingestion.utils.agent5_paddleocr_scan import PaddleScanAccumulator
from data_ingestion.utils.agent5_logging import configure_agent5_file_logging, log_api_call

logger = logging.getLogger(__name__)

try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env", override=True)
except ImportError:
    pass

_ENV_FILE = PROJECT_ROOT / ".env"

IMAGE_SIZE = "640x640"
SAT_ZOOM = 21
HOUSE_NUMBER_CONF_THRESHOLD = 0.90
# An *exact* full house-number match (every digit equals the expected number) is
# trusted even below the OCR confidence threshold: an exact multi-digit string
# match is itself strong evidence. This floor only rejects illegible noise.
EXACT_MATCH_MIN_CONF = 0.50
MIN_RESULT_CONFIDENCE = 90
OCR_NOISE_WORDS = frozenset({"google", "@", "©", "guoglo", "googlo"})

_CACHE_ROOT = PROJECT_ROOT / "cache" / "agent5"
_REF_CACHE_ROOT = STREET_VIEW_VENDOR / "cache"
_CACHE_METADATA_DIR = _CACHE_ROOT / "metadata"
_CACHE_IMAGES_DIR = _CACHE_ROOT / "images"
_CACHE_VISION_DIR = _CACHE_ROOT / "vision"


def _cache_read_roots() -> list[Path]:
    roots = [_CACHE_ROOT]
    if _REF_CACHE_ROOT.is_dir():
        roots.append(_REF_CACHE_ROOT)
    return roots


def _read_json_from_cache(subdir: str, filename: str) -> dict | None:
    for root in _cache_read_roots():
        path = root / subdir / filename
        if path.is_file():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
    return None


def _read_bytes_from_cache(subdir: str, filename: str) -> bytes | None:
    for root in _cache_read_roots():
        path = root / subdir / filename
        if path.is_file():
            try:
                data = path.read_bytes()
                return data if len(data) > 0 else None
            except OSError:
                continue
    return None


def _setup_cache_dirs() -> None:
    for directory in (_CACHE_METADATA_DIR, _CACHE_IMAGES_DIR, _CACHE_VISION_DIR):
        directory.mkdir(parents=True, exist_ok=True)


def _dotenv_values() -> dict[str, str]:
    if not _ENV_FILE.exists():
        return {}
    try:
        from dotenv import dotenv_values
        return {
            str(key): str(value).strip()
            for key, value in dotenv_values(_ENV_FILE).items()
            if value is not None and str(value).strip()
        }
    except Exception:
        logger.debug("Agent5 could not read .env values from %s", _ENV_FILE, exc_info=True)
        return {}


def _config_value(*names: str) -> str:
    values = _dotenv_values()
    for name in names:
        value = values.get(name)
        if value:
            return value
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def _google_key() -> str:
    return _config_value("GOOGLE_MAPS_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GEOCODING_API_KEY")


def _azure_endpoint() -> str:
    return _config_value("AZURE_VISION_ENDPOINT").rstrip("/")


def _azure_key() -> str:
    return _config_value("AZURE_VISION_KEY")


def _ssl_verify() -> bool:
    return os.environ.get("FTTH_SSL_VERIFY", "1").strip().lower() not in {"0", "false", "no"}


def _coord_key(lat: float, lon: float) -> str:
    return f"{lat:.6f}_{lon:.6f}"


def bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    d_lon = lon2 - lon1
    x = math.sin(d_lon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(d_lon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def streetview_metadata(lat: float, lon: float, addr: Any = None) -> tuple[bool, dict]:
    cache_name = f"{_coord_key(lat, lon)}.json"
    cached = _read_json_from_cache("metadata", cache_name)
    if cached is not None:
        if cached.get("status") == "OK":
            log_api_call(
                logger, "google_streetview_metadata",
                request={"location": f"{lat},{lon}"}, response=cached, status="cache",
            )
            return True, cached
        logger.info(
            "Ignoring cached Street View metadata error for lat=%.6f lon=%.6f status=%s",
            lat,
            lon,
            cached.get("status"),
        )

    key = _google_key()
    if not key:
        return False, {}

    request_payload = {"location": f"{lat},{lon}"}
    try:
        resp = requests.get(
            "https://maps.googleapis.com/maps/api/streetview/metadata",
            params={**request_payload, "key": key},
            timeout=10,
            verify=_ssl_verify(),
        )
        data = resp.json()
        log_api_call(
            logger, "google_streetview_metadata",
            request=request_payload, response=data, status=resp.status_code,
        )
    except Exception as exc:
        logger.debug("Street View metadata fetch error: %s", exc)
        return False, {}
    if data.get("status") == "OK":
        _setup_cache_dirs()
        (_CACHE_METADATA_DIR / cache_name).write_text(json.dumps(data), encoding="utf-8")
    return data.get("status") == "OK", data


def road_facing_heading(sv_meta: dict, target_lat: float, target_lon: float) -> float:
    loc = sv_meta.get("location", {})
    cam_lat = loc.get("lat")
    cam_lon = loc.get("lng")
    if cam_lat is not None and cam_lon is not None:
        return bearing(cam_lat, cam_lon, target_lat, target_lon)
    return 0.0


def _load_image_cache(lat: float, lon: float, img_type: str) -> bytes | None:
    cached = _read_bytes_from_cache("images", f"{_coord_key(lat, lon)}_{img_type}.jpg")
    if cached is None:
        return None
    if _is_streetview_placeholder(cached):
        _purge_placeholder_cache(lat, lon, img_type)
        return None
    return cached


# Placeholder / error Street View responses are tiny; real captures are much larger.
_MIN_STREETVIEW_BYTES = 15_000


def _is_streetview_placeholder(data: bytes | None) -> bool:
    """True for Google's gray 'Sorry, we have no imagery here' JPEG tile."""
    if not data:
        return True
    if len(data) < _MIN_STREETVIEW_BYTES:
        return True
    lower = data.lower()
    if b"sorry" in lower and b"imagery" in lower:
        return True
    return False


def _purge_placeholder_cache(lat: float, lon: float, img_type: str) -> None:
    path = _CACHE_IMAGES_DIR / f"{_coord_key(lat, lon)}_{img_type}.jpg"
    if path.is_file():
        try:
            path.unlink()
            logger.debug("Removed Street View placeholder cache: %s", path.name)
        except OSError:
            pass


def _find_any_cached_streetview_image(lat: float, lon: float) -> bytes | None:
    """Return the largest cached Street View JPEG for these coordinates (any heading/fov)."""
    prefix = f"{_coord_key(lat, lon)}_sv_"
    best: bytes | None = None
    best_len = 0
    for root in _cache_read_roots():
        images_dir = root / "images"
        if not images_dir.is_dir():
            continue
        for path in images_dir.glob(f"{prefix}*.jpg"):
            try:
                data = path.read_bytes()
            except OSError:
                continue
            if len(data) < _MIN_STREETVIEW_BYTES:
                continue
            if len(data) > best_len:
                best = data
                best_len = len(data)
    return best


def _pick_primary_streetview_image(
    images: list[tuple[str, bytes, str]],
) -> tuple[bytes | None, str | None]:
    """Use the first non-satellite capture when iterative search did not set primary_image."""
    preferred = (
        "streetview_primary",
        "streetview_zoom2",
        "streetview_oblique_left",
        "streetview_oblique_right",
    )
    by_type = {img_type: img_bytes for img_type, img_bytes, _url in images if img_bytes}
    for img_type in preferred:
        img_bytes = by_type.get(img_type)
        if img_bytes and not _is_streetview_placeholder(img_bytes):
            return img_bytes, img_type
    for img_type, img_bytes, _url in images:
        if img_type not in {"satellite", "esri_satellite"} and img_bytes:
            if not _is_streetview_placeholder(img_bytes):
                return img_bytes, img_type
    return None, None


def _save_image_cache(lat: float, lon: float, img_type: str, data: bytes) -> None:
    _setup_cache_dirs()
    path = _CACHE_IMAGES_DIR / f"{_coord_key(lat, lon)}_{img_type}.jpg"
    path.write_bytes(data)


def fetch_streetview_image(lat: float, lon: float, heading: float, fov: int = 90) -> tuple[bytes | None, str]:
    img_type = f"sv_{round(heading, 1)}_fov{fov}_s2"
    cached = _load_image_cache(lat, lon, img_type)
    key = _google_key()
    params = {
        "size": IMAGE_SIZE,
        "scale": 2,
        "source": "outdoor",
        "location": f"{lat},{lon}",
        "heading": round(heading, 1),
        "pitch": "0",
        "fov": str(fov),
        "key": key,
    }
    safe_url = requests.Request(
        "GET", "https://maps.googleapis.com/maps/api/streetview", params=params
    ).prepare().url
    safe_url = (safe_url or "").split("&key=")[0] + "&key=HIDDEN"
    if cached is not None:
        log_api_call(
            logger, "google_streetview_image",
            request={"url": safe_url, "heading": round(heading, 1), "fov": fov},
            response={"bytes": len(cached)}, status="cache",
        )
        return cached, safe_url

    if not key:
        return None, safe_url

    resp = requests.get(
        "https://maps.googleapis.com/maps/api/streetview",
        params=params,
        timeout=20,
        verify=_ssl_verify(),
    )
    content_type = resp.headers.get("content-type", "")
    is_image = resp.status_code == 200 and content_type.startswith("image")
    is_placeholder = is_image and _is_streetview_placeholder(resp.content)
    log_api_call(
        logger, "google_streetview_image",
        request={"url": safe_url, "heading": round(heading, 1), "fov": fov},
        response={
            "bytes": len(resp.content),
            "content_type": content_type,
            "placeholder": is_placeholder,
            "body": None if is_image else resp.text,
        },
        status=resp.status_code,
    )
    if is_image and not is_placeholder:
        _save_image_cache(lat, lon, img_type, resp.content)
        return resp.content, safe_url

    if is_placeholder:
        logger.info(
            "Street View placeholder rejected (no imagery) lat=%.6f lon=%.6f heading=%.1f fov=%d",
            lat,
            lon,
            heading,
            fov,
        )
        _purge_placeholder_cache(lat, lon, img_type)

    fallback = _find_any_cached_streetview_image(lat, lon)
    if fallback is not None:
        log_api_call(
            logger, "google_streetview_image",
            request={"url": safe_url, "heading": round(heading, 1), "fov": fov},
            response={"bytes": len(fallback), "source": "cache_fallback"},
            status="cache",
        )
        return fallback, safe_url
    return None, safe_url


def fetch_satellite_image(lat: float, lon: float) -> tuple[bytes | None, str]:
    cached = _load_image_cache(lat, lon, "satellite")
    key = _google_key()
    params = {
        "center": f"{lat},{lon}",
        "zoom": SAT_ZOOM,
        "size": IMAGE_SIZE,
        "scale": 2,
        "maptype": "satellite",
        "key": key,
    }
    safe_url = requests.Request(
        "GET", "https://maps.googleapis.com/maps/api/staticmap", params=params
    ).prepare().url
    safe_url = (safe_url or "").split("&key=")[0] + "&key=HIDDEN"
    if cached:
        normalized = ensure_rgb_jpeg_bytes(cached)
        log_api_call(logger, "google_satellite",
                     request={"url": safe_url}, response={"bytes": len(normalized)}, status="cache")
        return normalized, safe_url
    if not key:
        return None, safe_url

    try:
        resp = requests.get(
            "https://maps.googleapis.com/maps/api/staticmap",
            params=params,
            timeout=20,
            verify=_ssl_verify(),
        )
    except requests.RequestException as exc:
        logger.debug("Satellite fetch error: %s", exc)
        return None, safe_url

    content_type = resp.headers.get("content-type", "")
    is_image = resp.status_code == 200 and content_type.startswith("image")
    log_api_call(
        logger, "google_satellite",
        request={"url": safe_url},
        response={"bytes": len(resp.content), "content_type": content_type,
                  "body": None if is_image else resp.text},
        status=resp.status_code,
    )
    if is_image:
        normalized = ensure_rgb_jpeg_bytes(resp.content)
        _save_image_cache(lat, lon, "satellite", normalized)
        return normalized, safe_url
    return None, safe_url


def fetch_esri_satellite_image(lat: float, lon: float) -> tuple[bytes | None, str]:
    cache_key = "esri_satellite"
    cached = _load_image_cache(lat, lon, cache_key)
    if cached:
        log_api_call(logger, "esri_satellite",
                     request={"lat": lat, "lon": lon}, response={"bytes": len(cached)}, status="cache")
        return cached, "cached_esri"

    bbox_offset = 0.0005
    url = "https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/export"
    params = {
        "bbox": f"{lon - bbox_offset},{lat - bbox_offset},{lon + bbox_offset},{lat + bbox_offset}",
        "bboxSR": "4326",
        "imageSR": "4326",
        "size": "640,640",
        "format": "jpg",
        "f": "image",
    }
    try:
        resp = requests.get(url, params=params, timeout=20, verify=_ssl_verify())
    except requests.RequestException as exc:
        logger.debug("ESRI fetch error: %s", exc)
        return None, url

    content_type = resp.headers.get("content-type", "")
    is_image = resp.status_code == 200 and content_type.startswith("image")
    log_api_call(
        logger, "esri_satellite",
        request={"url": url, "params": params},
        response={"bytes": len(resp.content), "content_type": content_type,
                  "body": None if is_image else resp.text},
        status=resp.status_code,
    )
    if is_image:
        _save_image_cache(lat, lon, cache_key, resp.content)
        return resp.content, resp.url
    return None, url


def azure_vision_analyze(image_bytes: bytes) -> dict | None:
    endpoint = _azure_endpoint()
    key = _azure_key()
    if not endpoint or not key:
        logger.warning("Azure Vision credentials not configured")
        return None

    url = f"{endpoint}/computervision/imageanalysis:analyze"
    params = {"api-version": "2024-02-01", "features": "objects,tags,read", "language": "en"}
    resp = None
    try:
        resp = requests.post(
            url,
            params=params,
            headers={"Ocp-Apim-Subscription-Key": key, "Content-Type": "application/octet-stream"},
            data=image_bytes,
            timeout=30,
            verify=_ssl_verify(),
        )
        resp.raise_for_status()
        data = resp.json()
        log_api_call(
            logger, "azure_vision",
            request={"url": url, "params": params, "image_bytes": len(image_bytes)},
            response=data, status=resp.status_code,
        )
        return data
    except Exception as exc:
        status = getattr(resp, "status_code", None)
        body_text = getattr(resp, "text", "") if resp is not None else ""
        log_api_call(
            logger, "azure_vision",
            request={"url": url, "params": params, "image_bytes": len(image_bytes)},
            response=body_text or str(exc), status=status,
        )
        logger.warning("Azure Vision error: status=%s err=%s", status, exc)
        return None


def _load_vision_cache(lat: float, lon: float, suffix: str = "") -> dict | None:
    return _read_json_from_cache("vision", f"{_coord_key(lat, lon)}{suffix}.json")


def _save_vision_cache(lat: float, lon: float, data: dict, suffix: str = "") -> None:
    _setup_cache_dirs()
    path = _CACHE_VISION_DIR / f"{_coord_key(lat, lon)}{suffix}.json"
    path.write_text(json.dumps(data), encoding="utf-8")


def get_tag_confidence(visions: list[dict | None], keywords: set[str]) -> float:
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


def classify_structure_from_tags(tags: set[str]) -> str:
    high_rise = {"high-rise", "skyscraper", "apartment complex", "condo tower"}
    low_rise = {"apartment building", "apartment", "low-rise", "flat", "condominium"}
    townhouse = {"townhouse", "row house", "terraced house", "rowhouse"}
    house = {"house", "home", "bungalow", "cottage", "residential", "single family", "single-family", "detached"}
    commercial = {"store", "shop", "commercial", "office", "retail", "restaurant", "business", "storefront"}
    industrial = {"industrial", "warehouse", "factory", "manufacturing"}

    if tags & high_rise:
        return "high_rise_apt"
    if tags & low_rise:
        return "low_rise_apt"
    if tags & townhouse:
        return "townhouse"
    if tags & house:
        return "detached_house"
    if tags & commercial:
        return "commercial"
    if tags & industrial:
        return "industrial"
    return "unclear"


def extract_ocr_text(vision_result: dict) -> str:
    texts: list[str] = []
    for block in vision_result.get("readResult", {}).get("blocks", []):
        for line in block.get("lines", []):
            txt = line.get("text", "").strip()
            if txt:
                texts.append(txt)
    return " ".join(texts)


def _normalize_ocr_token(text: str) -> str:
    return text.strip().lower().lstrip("@").strip()


def _digits_only(text: str) -> str:
    return "".join(ch for ch in text if ch.isdigit())


def detect_house_number(
    vision_result: dict,
    expected: str,
    threshold: float = HOUSE_NUMBER_CONF_THRESHOLD,
) -> tuple[bool, float]:
    expected = (expected or "").strip()
    if not expected or not vision_result:
        return False, 0.0
    expected_digits = _digits_only(expected)

    best_conf = 0.0
    for block in vision_result.get("readResult", {}).get("blocks", []):
        for line in block.get("lines", []):
            line_words = []
            for word in line.get("words", []):
                raw = word.get("text", "")
                token = _normalize_ocr_token(raw)
                if not token or token in OCR_NOISE_WORDS:
                    continue
                conf = float(word.get("confidence", 0))
                line_words.append((token, conf, raw))
                # Exact full-number read: trust it even below `threshold`, and
                # treat it as high confidence so the downstream scoring credits it.
                exact = bool(expected_digits) and _digits_only(token) == expected_digits
                if exact and conf >= EXACT_MATCH_MIN_CONF:
                    return True, max(conf, threshold)
                # Partial / substring match: keep the stricter confidence bar.
                if expected in raw or expected == token:
                    if conf >= threshold:
                        return True, conf
                    best_conf = max(best_conf, conf)

            line_text = line.get("text", "")
            if expected in line_text and line_words:
                relevant = [c for t, c, r in line_words if expected in r or expected == t]
                if relevant:
                    line_conf = max(relevant)
                    if line_conf >= threshold:
                        return True, line_conf
                    best_conf = max(best_conf, line_conf)
    return False, best_conf


def parse_vision_output(
    vision: dict,
    record: dict,
    second_vision: dict | None = None,
    sat_vision: dict | None = None,
    ocr_crop_results: list[tuple[str, dict]] | None = None,
    esri_vision: dict | None = None,
    ocr_match_found: bool = False,
    house_number_conf: float = 0.0,
    imagery_source: str = "none",
) -> dict[str, Any]:
    tags = {t["name"].lower() for t in vision.get("tagsResult", {}).get("values", [])}
    objects = {o["tags"][0]["name"].lower() for o in vision.get("objectsResult", {}).get("values", []) if o.get("tags")}
    caption = ""
    cap_conf = 1.0

    ocr_lines = []
    for block in vision.get("readResult", {}).get("blocks", []):
        for line in block.get("lines", []):
            txt = line.get("text", "").lower()
            if txt:
                ocr_lines.append(txt)
    all_text = " ".join(ocr_lines)
    address_type = (record.get("address_type") or "").lower()
    house_number = (record.get("house_number") or "").strip()

    structure_type = "unclear"
    high_rise = {"high-rise", "skyscraper", "apartment complex", "condo tower"}
    low_rise = {"apartment building", "apartment", "low-rise", "flat", "condominium"}
    townhouse = {"townhouse", "row house", "terraced house", "rowhouse"}
    house = {"house", "home", "bungalow", "cottage", "residential", "single family", "single-family", "detached"}
    commercial = {"store", "shop", "commercial", "office", "retail", "restaurant", "business", "storefront"}
    industrial = {"industrial", "warehouse", "factory", "manufacturing"}

    if tags & high_rise or any(k in all_text for k in high_rise):
        structure_type = "high_rise_apt"
    elif tags & low_rise or any(k in all_text for k in low_rise):
        structure_type = "low_rise_apt"
    elif tags & townhouse or any(k in all_text for k in townhouse):
        structure_type = "townhouse"
    elif tags & house or any(k in all_text for k in house):
        structure_type = "detached_house"
    elif tags & commercial or any(k in all_text for k in commercial):
        structure_type = "commercial"
    elif tags & industrial or any(k in all_text for k in industrial):
        structure_type = "industrial"

    floor_map = {
        "one-story": 1, "single-story": 1, "one story": 1,
        "two-story": 2, "double-story": 2, "two story": 2,
        "three-story": 3, "three story": 3,
        "four-story": 4, "four story": 4,
        "five-story": 5,
    }
    floor_count = None
    for phrase, num in floor_map.items():
        if phrase in all_text:
            floor_count = num
            break

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

    multiple_entrances = (
        len([o for o in objects if "door" in o or "entrance" in o or "gate" in o]) > 1
        or any(k in all_text for k in ["multiple entrances", "several doors", "multiple doors"])
    )
    multiple_mailboxes = any(k in all_text for k in ["mailbox", "mailboxes", "letter box", "mail slot"])
    commercial_signage = bool(
        tags & {"sign", "signage", "billboard", "banner", "logo", "advertisement"}
        or any(k in all_text for k in ["sign on", "storefront sign", "business sign"])
    )
    under_construction = bool(
        tags & {"construction", "scaffold", "scaffolding", "crane", "building site", "construction site", "under construction"}
        or any(k in all_text for k in ["under construction", "being built", "construction site"])
    )

    image_quality = "good"
    poor_signals = {"blurry", "dark", "obstructed", "blocked", "unclear", "fog", "night", "low visibility"}
    if not tags and not objects:
        image_quality = "no_structure"
    elif tags & poor_signals or any(k in all_text for k in poor_signals):
        image_quality = "obstructed"
    elif cap_conf < 0.35 and caption:
        image_quality = "partial"

    vision_score = 0.0
    metadata_score = 0.0
    aerial_score = 0.0
    agreement_score = 0.0
    ocr_score = 25 if ocr_match_found else 0
    quality_score = 0.0

    high_conf_house_number = ocr_match_found and house_number_conf >= HOUSE_NUMBER_CONF_THRESHOLD

    structure_keywords = {
        "detached_house": {"house", "home", "cottage", "bungalow"},
        "townhouse": {"townhouse", "row house"},
        "low_rise_apt": {"apartment", "condominium"},
        "high_rise_apt": {"high-rise", "skyscraper"},
        "commercial": {"store", "office", "restaurant", "shop"},
        "industrial": {"warehouse", "factory"},
    }
    if structure_type in structure_keywords:
        vision_score = get_tag_confidence([vision, second_vision], structure_keywords[structure_type]) * 50

    if "residential" in address_type and structure_type in {"detached_house", "townhouse", "low_rise_apt", "high_rise_apt"}:
        metadata_score = 20
    elif "commercial" in address_type and structure_type == "commercial":
        metadata_score = 20
    elif high_conf_house_number and structure_type in {"detached_house", "townhouse", "low_rise_apt", "high_rise_apt"}:
        metadata_score = 20

    if sat_vision:
        aerial_tags = {t["name"].lower() for t in sat_vision.get("tagsResult", {}).get("values", [])}
        if esri_vision:
            aerial_tags |= {t["name"].lower() for t in esri_vision.get("tagsResult", {}).get("values", [])}
        residential_aerial = {"house", "property", "yard", "driveway", "roof", "residential area"}
        commercial_aerial = {"parking lot", "office", "warehouse", "industrial", "commercial building"}
        if structure_type in {"detached_house", "townhouse", "low_rise_apt", "high_rise_apt"}:
            aerial_score = min(len(aerial_tags & residential_aerial) * 3, 15)
        elif structure_type == "commercial":
            aerial_score = min(len(aerial_tags & commercial_aerial) * 3, 15)

    crop_boost = 0
    if ocr_crop_results:
        residential_crop_tags = {"home", "house", "property", "building", "yard", "porch", "window", "real estate"}
        best_crop_score = 0
        for _crop_name, crop_vision in ocr_crop_results:
            crop_matches = 0
            for tag in crop_vision.get("tagsResult", {}).get("values", []):
                tag_name = tag.get("name", "").lower()
                tag_conf = tag.get("confidence", 0)
                if tag_name in residential_crop_tags and tag_conf >= 0.85:
                    crop_matches += 1
            best_crop_score = max(best_crop_score, crop_matches)
        crop_boost = min(best_crop_score * 2, 10)
        vision_score += crop_boost

    agreement_matches = 0
    for v in [vision, second_vision]:
        if not v:
            continue
        v_tags = {t["name"].lower() for t in v.get("tagsResult", {}).get("values", [])}
        if classify_structure_from_tags(v_tags) == structure_type and structure_type != "unclear":
            agreement_matches += 1
    agreement_score = min(agreement_matches * 5, 10)

    commercial_words = {"office", "store", "shop", "suite"}
    residential_words = {"apt", "apartment", "unit"}
    if any(word in all_text for word in commercial_words) and structure_type == "commercial":
        ocr_score = 10
    elif any(word in all_text for word in residential_words) and structure_type in {"low_rise_apt", "high_rise_apt", "townhouse"}:
        ocr_score = 10

    if image_quality == "good":
        quality_score = 10
    elif image_quality == "partial":
        quality_score = 5

    confidence = round(min(
        vision_score + metadata_score + aerial_score + agreement_score + ocr_score + quality_score,
        100,
    ))

    # ── pipeline.py confidence floors (reference lines 1131–1132 + FTTH rules) ──
    if high_conf_house_number:
        confidence = max(confidence, MIN_RESULT_CONFIDENCE)

    residential_streetview = (
        structure_type == "unclear"
        and imagery_source == "streetview"
        and house_number
        and "residential" in address_type
    )
    if residential_streetview:
        structure_type = "detached_house"
        visible_units_min, visible_units_max = 1, 1
        metadata_score = max(metadata_score, 20)
        confidence = round(min(
            vision_score + metadata_score + aerial_score + agreement_score + ocr_score + quality_score,
            100,
        ))
        confidence = max(confidence, MIN_RESULT_CONFIDENCE)
        image_quality = "good"
    elif (
        structure_type == "detached_house"
        and imagery_source == "streetview"
        and image_quality == "good"
        and confidence >= 80
    ):
        confidence = max(confidence, MIN_RESULT_CONFIDENCE)
    elif structure_type == "unclear" and vision_score < 1 and not ocr_match_found:
        confidence = 0
        image_quality = "no_structure"

    return {
        "structure_type_detail": structure_type,
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
        "ocr_match_found": ocr_match_found,
        "house_number_conf": round(house_number_conf * 100, 1) if house_number_conf else 0.0,
        "high_conf_house_number": high_conf_house_number,
        "confidence_breakdown": {
            "vision_score": round(vision_score, 2),
            "metadata_score": metadata_score,
            "aerial_score": aerial_score,
            "agreement_score": agreement_score,
            "ocr_score": ocr_score,
            "quality_score": quality_score,
        },
    }


def _finalize_analyzed_result(
    structured: dict[str, Any],
    imagery_source: str,
    record: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Map structure detail to SFH/MDU display fields and default unit counts."""
    detail = structured.get("structure_type_detail", "unclear")
    simple_type, is_mdu = simplify_structure_type(detail)

    if simple_type == "SFH":
        structured["visible_units_min"] = structured.get("visible_units_min") or 1
        structured["visible_units_max"] = structured.get("visible_units_max") or 1
        structured["floor_count"] = structured.get("floor_count") or 1
    elif is_mdu:
        structured["visible_units_min"] = structured.get("visible_units_min") or 2
        structured["visible_units_max"] = structured.get("visible_units_max") or structured["visible_units_min"]

    structured.update({
        "status": "analyzed",
        "structure_type": simple_type,
        "is_mdu": is_mdu,
        "imagery_source": imagery_source,
    })
    return structured


def _confidence_percent(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    try:
        parsed = float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
    if 0 < parsed <= 1:
        parsed *= 100.0
    return max(0.0, min(parsed, 100.0))


def _apply_house_number_verdict(result: dict[str, Any]) -> dict[str, Any]:
    """Use house-number confidence as the Agent 5 review/accept score."""
    image_confidence = _confidence_percent(result.get("image_confidence", result.get("confidence")))
    house_confidence = _confidence_percent(result.get("house_number_conf"))
    original_status = str(result.get("status") or "").strip()

    result["image_confidence"] = round(image_confidence, 1)
    result["house_number_conf"] = round(house_confidence, 1)
    result["confidence"] = round(house_confidence, 1)
    if original_status and original_status.upper() not in {"ACCEPT", "REVIEW"}:
        result["analysis_status"] = original_status
    result["status"] = (
        "ACCEPT"
        if house_confidence >= HOUSE_NUMBER_CONF_THRESHOLD * 100
        else "REVIEW"
    )
    return result


def _pick_best_structure_vision(variant_visions: list[tuple[str, dict, str]]) -> dict | None:
    best_vision = None
    best_house_conf = 0.0
    for _image_type, vision, _url in variant_visions:
        for tag in vision.get("tagsResult", {}).get("values", []):
            tag_name = tag.get("name", "").lower()
            tag_conf = float(tag.get("confidence", 0))
            if tag_name in ("house", "home", "property") and tag_conf > best_house_conf:
                best_house_conf = tag_conf
                best_vision = vision
    if best_vision is None and variant_visions:
        best_vision = max(
            variant_visions,
            key=lambda x: len(x[1].get("tagsResult", {}).get("values", [])),
        )[1]
    return best_vision


def _analyze_for_structure(
    image_type: str,
    img_bytes: bytes,
    img_url: str,
    variant_visions: list[tuple[str, dict, str]],
    global_ocr_results: list[tuple[str, str]],
    analyze_fn: Callable[[bytes], dict | None],
) -> dict | None:
    vision_result = analyze_fn(img_bytes)
    if not vision_result:
        return None
    extracted_text = extract_ocr_text(vision_result)
    if extracted_text:
        global_ocr_results.append((image_type, extracted_text))
    variant_visions.append((image_type, vision_result, img_url))
    return vision_result


def analyze_address(
    record: dict[str, Any],
    *,
    sleep_seconds: float | None = None,
    agent_options: dict[str, bool] | None = None,
    addr: Any = None,
) -> dict[str, Any]:
    """
    Run the full reference Street View pipeline for one address record.
    Returns a dict ready to store in AgentResult.data.
    """
    configure_agent5_file_logging()
    from data_ingestion.config.settings import get_settings
    from data_ingestion.utils.pipeline_options import DEFAULT_AGENT5_OPTIONS

    settings = get_settings()
    opts = {**DEFAULT_AGENT5_OPTIONS, **(agent_options or {})}
    analysis_mode = str(opts.get("analysis_mode") or "hybrid").strip().lower()
    if analysis_mode in {"offline", "local"}:
        opts["azure_vision"] = False
        opts["gpt_vision"] = True
        opts["llm_provider"] = "offline"
    elif analysis_mode in {"online", "cloud"}:
        opts["azure_vision"] = True
        opts["gpt_vision"] = True
        opts["llm_provider"] = "online"
    elif analysis_mode not in {"hybrid", "mixed"}:
        analysis_mode = "hybrid"
    fast_mode = bool(opts.get("fast_mode", settings.agent5_fast_mode))
    use_street_view = opts.get("street_view", True)
    use_satellite = opts.get("satellite_fallback", True)
    use_azure = opts.get("azure_vision", True)
    use_gpt = opts.get("gpt_vision", True)
    llm_provider = str(opts.get("llm_provider") or "online").strip().lower()
    if llm_provider in {"disabled", "none", "off"}:
        use_gpt = False
    ollama_vision_model = str(opts.get("ollama_vision_model") or os.environ.get("OLLAMA_VISION_MODEL") or "qwen2.5vl:latest").strip()
    if sleep_seconds is None:
        sleep_seconds = 0.0 if fast_mode else 0.3
    # House-number detection runs as an iterative, vision-LLM-guided search (see
    # iterative_house_number_search); the old fixed zoom/pan ladder is retired.
    lat = float(record["latitude"])
    lon = float(record["longitude"])
    expected_house_number = (record.get("house_number") or "").strip()

    logger.info(
        "Agent5 analyze: address=%r house_number=%r lat=%.6f lon=%.6f "
        "street_view=%s satellite=%s azure=%s llm=%s/%s mode=%s fast_mode=%s",
        record.get("raw_address") or record.get("address"),
        expected_house_number,
        lat,
        lon,
        use_street_view,
        use_satellite,
        use_azure,
        bool(use_gpt),
        llm_provider,
        analysis_mode,
        fast_mode,
    )
    logger.debug("Agent5 input record: %s | options: %s", record, opts)

    sv_available, sv_meta = streetview_metadata(lat, lon, addr=addr)
    if not sv_available and use_street_view and _find_any_cached_streetview_image(lat, lon):
        sv_available = True
        sv_meta = sv_meta or {"status": "OK", "location": {"lat": lat, "lng": lon}}
        logger.info(
            "Agent5: Street View metadata unavailable but cached imagery found for lat=%.6f lon=%.6f",
            lat,
            lon,
        )
    images: list[tuple[str, bytes, str]] = []
    imagery_source = "none"
    primary_image = None
    variant_visions: list[tuple[str, dict, str]] = []
    global_ocr_results: list[tuple[str, str]] = []
    ocr_match_found = False
    house_number_conf = 0.0
    paddle_ocr_used = False
    paddle_ocr_match_found = False
    paddle_ocr_text = ""
    tesseract_ocr_used = False
    tesseract_ocr_match_found = False
    tesseract_ocr_text = ""
    gpt_ocr_used = False
    gpt_ocr_match_found = False
    gpt_house_number_text = ""
    gpt_obstruction = False
    gpt_obstruction_type = "none"
    search_iterations = 0
    search_trace: list[dict[str, Any]] = []
    winning_step = None
    heading = 0.0
    paddle_candidates: list[tuple[str, bytes]] = []
    paddle_scan = PaddleScanAccumulator(expected=expected_house_number) if use_street_view else None

    def _analyze(image_bytes: bytes) -> dict | None:
        if not use_azure:
            return None
        result = azure_vision_analyze(image_bytes)
        if sleep_seconds:
            time.sleep(sleep_seconds)
        return result

    if use_street_view and sv_available:
        imagery_source = "streetview"
        heading = road_facing_heading(sv_meta, lat, lon)

        # Iterative, vision-LLM-guided house-number search (replaces the old fixed
        # zoom ladder). It captures the Street View image, runs PaddleOCR -> Azure
        # Vision OCR -> Tesseract -> GPT vision, and on failure uses the LLM's
        # obstruction/locate guidance to nudge coordinates, re-aim and zoom in for
        # up to MAX_ITERATIONS passes. Fetched images + Azure visions are collected
        # into the same lists the scoring path reads.
        search = iterative_house_number_search(
            lat=lat,
            lon=lon,
            base_heading=heading,
            expected_house_number=expected_house_number,
            address=record.get("raw_address") or record.get("address") or "",
            fetch_streetview_image=fetch_streetview_image,
            azure_analyze=_analyze,
            detect_house_number=detect_house_number,
            extract_ocr_text=extract_ocr_text,
            images=images,
            variant_visions=variant_visions,
            global_ocr_results=global_ocr_results,
            max_iterations=MAX_ITERATIONS,
            allow_variants=True,
            azure_threshold=HOUSE_NUMBER_CONF_THRESHOLD,
            gpt_enabled=use_gpt,
            llm_provider=llm_provider,
            ollama_vision_model=ollama_vision_model,
            sleep_seconds=sleep_seconds or 0.0,
            fast_mode=fast_mode,
            paddle_scan=paddle_scan,
        )
        if search.get("primary_image") is not None:
            primary_image = search["primary_image"]
        ocr_match_found = bool(search.get("found"))
        house_number_conf = float(search.get("house_number_conf") or 0.0)
        winning_step = search.get("winning_step")
        paddle_ocr_used = bool(search.get("paddle_ocr_used"))
        paddle_ocr_match_found = bool(search.get("paddle_ocr_match_found"))
        paddle_ocr_text = search.get("paddle_ocr_text") or ""
        tesseract_ocr_used = bool(search.get("tesseract_ocr_used"))
        tesseract_ocr_match_found = bool(search.get("tesseract_ocr_match_found"))
        tesseract_ocr_text = search.get("tesseract_ocr_text") or ""
        gpt_ocr_used = bool(search.get("gpt_used"))
        gpt_ocr_match_found = bool(search.get("gpt_match_found"))
        gpt_house_number_text = search.get("gpt_house_number_text") or ""
        gpt_obstruction = bool(search.get("gpt_obstruction"))
        gpt_obstruction_type = search.get("gpt_obstruction_type") or "none"
        search_iterations = int(search.get("iterations_used") or 0)
        search_trace = search.get("search_trace") or []
        # PaddleOCR already ran on every street-view image inside the loop, so the
        # downstream Paddle fallback only needs satellite / ESRI / crop candidates.

        # Extra oblique Street View angles for structure scoring only.
        if not (fast_mode and ocr_match_found):
            for image_type, angle_offset in CLASSIFICATION_ANGLE_OFFSETS:
                if any(x[0] == image_type for x in images):
                    continue
                entry = fetch_variant(
                    lat, lon, heading, image_type, angle_offset, CLASSIFICATION_FOV, fetch_streetview_image
                )
                if not entry:
                    continue
                image_type, img, url = entry
                img = clarify_image(img)
                images.append((image_type, img, url))
                _analyze_for_structure(
                    image_type, img, url, variant_visions, global_ocr_results, _analyze
                )

    # Always fetch satellite/ESRI for aerial evidence (reference pipeline behavior).
    img_esri, url_esri = fetch_esri_satellite_image(lat, lon) if use_satellite else (None, "")
    if img_esri:
        images.append(("esri_satellite", img_esri, url_esri))
        if primary_image is None:
            imagery_source = "esri_satellite"
            primary_image = img_esri

    img_sat, url_sat = fetch_satellite_image(lat, lon) if use_satellite else (None, "")
    satellite_image = img_sat
    if img_sat:
        images.append(("satellite", img_sat, url_sat))
        if not use_street_view or not sv_available or primary_image is None:
            imagery_source = "satellite"
            primary_image = img_sat

    if primary_image is None:
        fallback_img, fallback_type = _pick_primary_streetview_image(images)
        if fallback_img is not None:
            primary_image = fallback_img
            imagery_source = "streetview"
            logger.info(
                "Agent5: using cached/alternate street view %r as primary imagery",
                fallback_type,
            )

    if primary_image is None:
        simple_type, is_mdu = simplify_structure_type("unclear")
        return _apply_house_number_verdict({
            "status": "no_imagery",
            "reason": "no street view or satellite available",
            "structure_type": simple_type,
            "structure_type_detail": "unclear",
            "is_mdu": is_mdu,
            "confidence": 0,
            "image_confidence": 0,
            "house_number_conf": 0,
            "imagery_source": "none",
            "latitude": lat,
            "longitude": lon,
        })

    raw_vision = _pick_best_structure_vision(variant_visions)
    if raw_vision is None:
        raw_vision = _load_vision_cache(lat, lon)
    if raw_vision:
        _save_vision_cache(lat, lon, raw_vision)

    second_vision = variant_visions[1][1] if len(variant_visions) > 1 else None
    sat_vision = _load_vision_cache(lat, lon, "_satellite")
    need_satellite_vision = satellite_image and (
        not (fast_mode and sv_available and primary_image)
        or (expected_house_number and not ocr_match_found)
    )
    if sat_vision is None and need_satellite_vision:
        sat_vision = _analyze(satellite_image)
        if sat_vision:
            sat_text = extract_ocr_text(sat_vision)
            if sat_text:
                global_ocr_results.append(("google_satellite", sat_text))
            _save_vision_cache(lat, lon, sat_vision, "_satellite")

    esri_vision = None
    if img_esri:
        esri_vision = _analyze(img_esri)
        if esri_vision:
            esri_text = extract_ocr_text(esri_vision)
            if esri_text:
                global_ocr_results.append(("esri_satellite", esri_text))

    if not ocr_match_found and expected_house_number and sat_vision:
        found, conf = detect_house_number(sat_vision, expected_house_number)
        if found:
            ocr_match_found = True
            house_number_conf = conf
            winning_step = "satellite"
        elif satellite_image:
            paddle_candidates.append(("satellite", satellite_image))
            tess_found, tess_conf, tess_text = detect_house_number_with_tesseract(
                satellite_image, expected_house_number
            )
            tesseract_ocr_used = tesseract_ocr_used or bool(tess_text)
            tesseract_ocr_text = tesseract_ocr_text or tess_text
            if tess_found:
                ocr_match_found = True
                tesseract_ocr_match_found = True
                house_number_conf = max(house_number_conf, tess_conf)
                winning_step = "satellite"

    if not ocr_match_found and expected_house_number and esri_vision and img_esri:
        found, conf = detect_house_number(esri_vision, expected_house_number)
        if found:
            ocr_match_found = True
            house_number_conf = conf
            winning_step = "esri_satellite"
        else:
            paddle_candidates.append(("esri_satellite", img_esri))
            tess_found, tess_conf, tess_text = detect_house_number_with_tesseract(
                img_esri, expected_house_number
            )
            tesseract_ocr_used = tesseract_ocr_used or bool(tess_text)
            tesseract_ocr_text = tesseract_ocr_text or tess_text
            if tess_found:
                ocr_match_found = True
                tesseract_ocr_match_found = True
                house_number_conf = max(house_number_conf, tess_conf)
                winning_step = "esri_satellite"

    ocr_crop_results: list[tuple[str, dict]] = []
    best_image_bytes = None
    for preferred in (winning_step, "streetview_zoom2", "streetview_primary"):
        for img_type, img_bytes, _url in images:
            if img_type == preferred:
                best_image_bytes = img_bytes
                break
        if best_image_bytes:
            break

    if best_image_bytes:
        try:
            ocr_crop_items = generate_ocr_crops(best_image_bytes)
        except Exception as exc:
            logger.warning("Agent5 OCR crop generation failed: %s", exc)
            ocr_crop_items = []
        for crop_name, crop_bytes in ocr_crop_items:
            crop_vision = _load_vision_cache(lat, lon, f"_{crop_name}")
            if crop_vision is None:
                crop_vision = _analyze(crop_bytes)
                if crop_vision:
                    _save_vision_cache(lat, lon, crop_vision, f"_{crop_name}")
            if crop_vision:
                ocr_crop_results.append((crop_name, crop_vision))
                if not ocr_match_found and expected_house_number:
                    found, conf = detect_house_number(crop_vision, expected_house_number)
                    if found:
                        ocr_match_found = True
                        house_number_conf = conf
                        winning_step = crop_name
                    else:
                        paddle_candidates.append((crop_name, crop_bytes))
                        tess_found, tess_conf, tess_text = detect_house_number_with_tesseract(
                            crop_bytes, expected_house_number
                        )
                        tesseract_ocr_used = tesseract_ocr_used or bool(tess_text)
                        tesseract_ocr_text = tesseract_ocr_text or tess_text
                        if tess_found:
                            ocr_match_found = True
                            tesseract_ocr_match_found = True
                            house_number_conf = max(house_number_conf, tess_conf)
                            winning_step = crop_name

    if not ocr_match_found and expected_house_number and paddle_candidates:
        seen_candidate_names: set[str] = set()
        for candidate_name, candidate_bytes in paddle_candidates:
            if candidate_name in seen_candidate_names:
                continue
            seen_candidate_names.add(candidate_name)
            paddle_found, paddle_conf, paddle_text = detect_house_number_with_paddle(
                candidate_bytes, expected_house_number
            )
            paddle_ocr_used = paddle_ocr_used or bool(paddle_text)
            paddle_ocr_text = paddle_ocr_text or paddle_text
            if paddle_found:
                ocr_match_found = True
                paddle_ocr_match_found = True
                house_number_conf = max(house_number_conf, paddle_conf)
                winning_step = candidate_name
                break

    if raw_vision is None and sat_vision is not None:
        raw_vision = sat_vision

    if raw_vision is None:
        simple_type, is_mdu = simplify_structure_type("unclear")
        return _apply_house_number_verdict({
            "status": "no_vision",
            "reason": "vision analysis returned no results — set AZURE_VISION_ENDPOINT and AZURE_VISION_KEY",
            "structure_type": simple_type,
            "structure_type_detail": "unclear",
            "is_mdu": is_mdu,
            "confidence": 0,
            "image_confidence": 0,
            "house_number_conf": 0,
            "imagery_source": imagery_source,
            "image_quality": "no_structure",
            "latitude": lat,
            "longitude": lon,
        })

    structured = parse_vision_output(
        raw_vision,
        record,
        second_vision,
        sat_vision,
        ocr_crop_results,
        esri_vision,
        ocr_match_found,
        house_number_conf,
        imagery_source=imagery_source,
    )

    result = _finalize_analyzed_result(structured, imagery_source, record)
    result.update({
        "latitude": lat,
        "longitude": lon,
        "streetview_metadata": sv_meta if isinstance(sv_meta, dict) else {},
        "winning_step": winning_step,
        "images_fetched": len(images),
        "paddle_ocr_used": paddle_ocr_used,
        "paddle_ocr_match_found": paddle_ocr_match_found,
        "paddle_ocr_text": paddle_ocr_text[:500],
        "tesseract_ocr_used": tesseract_ocr_used,
        "tesseract_ocr_match_found": tesseract_ocr_match_found,
        "tesseract_ocr_text": tesseract_ocr_text[:500],
        "gpt_ocr_used": gpt_ocr_used,
        "gpt_ocr_match_found": gpt_ocr_match_found,
        "gpt_house_number_text": gpt_house_number_text[:500],
        "gpt_obstruction": gpt_obstruction,
        "gpt_obstruction_type": gpt_obstruction_type,
        "search_iterations": search_iterations,
        "search_trace": search_trace,
    })
    if paddle_scan is not None:
        result.update(paddle_scan.summary())
    _apply_house_number_verdict(result)
    detected = bool(result.get("ocr_match_found"))
    detected_via = (
        "paddle" if result.get("paddle_ocr_match_found")
        else "tesseract" if result.get("tesseract_ocr_match_found")
        else "gpt" if result.get("gpt_ocr_match_found")
        else "azure_ocr" if detected
        else "none"
    )
    logger.info(
        "Agent5 VERDICT: address=%r house_number=%r -> %s "
        "(via=%s conf=%.2f winning_step=%s) | status=%s structure=%s confidence=%s imagery=%s",
        record.get("raw_address") or record.get("address"),
        expected_house_number,
        "DETECTED" if detected else "NOT DETECTED",
        detected_via,
        float(result.get("house_number_conf") or 0.0),
        result.get("winning_step"),
        result.get("status"),
        result.get("structure_type"),
        result.get("confidence"),
        result.get("imagery_source"),
    )
    return result


# Backward-compatible aliases for unit tests
_sv_meta = streetview_metadata
_bearing = bearing

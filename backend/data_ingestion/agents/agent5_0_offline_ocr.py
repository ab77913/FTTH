"""
Agent 5-0 - Offline house-number validation with local Ollama vision + PaddleOCR fallback.

This agent is intentionally separate from Agent 5.  It does not call Azure,
OpenAI, or any cloud LLM.  It uses Google Street View only as an imagery source
when a Google Maps key is configured, then performs local analysis with:

  OCR Engine modes (controlled by the ``ocr_engine`` config field):

  - ``vision_primary``  (default): Ollama vision model (qwen2.5vl) reads the
    house number first; PaddleOCR runs only as a fallback when Ollama confidence
    is below ``ollama_ocr_threshold``.
  - ``paddle_primary``: Legacy order - PaddleOCR first, Ollama guidance-only
    for camera-angle recommendations.
  - ``vision_only``: Ollama vision only; PaddleOCR is skipped entirely.

Results are stored in agent_results with agent_name="agent5_0_offline_ocr".
The Excel runner can also process standalone coordinate spreadsheets.
"""
from __future__ import annotations
from data_ingestion.config.paths import PROJECT_ROOT

import base64
import hashlib
import json
import logging
import math
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
from sqlalchemy import select as _sel

from data_ingestion.database.db import get_session_factory
from data_ingestion.database.models import Address, AgentResult, AgentTable
from data_ingestion.utils.agent_logging import configure_agent_logger, log_payload
from data_ingestion.utils.agent5_image_utils import clarify_image, generate_ocr_crops
from data_ingestion.utils.agent5_paddle_ocr import (
    detect_house_number_with_paddle,
    paddleocr_runtime_status,
)
from data_ingestion.utils.json_utils import parse_llm_json_object

logger = logging.getLogger(__name__)

_PROJECT_ROOT = PROJECT_ROOT
_CACHE_ROOT = _PROJECT_ROOT / "cache" / "agent5_0_offline"
_AGENT_NAME = "agent5_0_offline_ocr"
_DISPLAY_NAME = "Agent 5-0: Offline Vision OCR + PaddleOCR Fallback"
_OLLAMA_OCR_ACCEPT_CONF = 0.70   # Ollama must reach this to skip PaddleOCR
_SV_METADATA_URL = "https://maps.googleapis.com/maps/api/streetview/metadata"
_SV_IMAGE_URL = "https://maps.googleapis.com/maps/api/streetview"
_MIN_STREETVIEW_BYTES = 15_000
_SSL_VERIFY_WARNED = False


@dataclass(frozen=True)
class Agent50Config:
    max_workers: int = 4
    max_iterations: int = 6
    paddle_threshold: float = 0.90
    streetview_size: str = "640x640"
    streetview_scale: int = 2
    fov_ladder: tuple[int, ...] = (70, 60, 48, 38, 30, 24)
    heading_offsets: tuple[float, ...] = (0.0, -15.0, 15.0, -30.0, 30.0, -45.0, 45.0)
    lateral_steps_m: tuple[float, ...] = (0.0, 3.0, -3.0, 6.0, -6.0, 10.0, -10.0)
    use_ollama: bool = True
    use_cache: bool = False
    ollama_host: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen2.5vl:latest"
    request_timeout_s: int = 20
    streetview_radius_m: int = 100
    # OCR engine strategy:
    #   vision_primary  - Ollama reads first; PaddleOCR is the fallback (default)
    #   paddle_primary  - PaddleOCR first; Ollama only guides camera angles
    #   vision_only     - Ollama only; PaddleOCR entirely skipped
    ocr_engine: str = "vision_primary"
    ollama_ocr_threshold: float = 0.70   # min Ollama conf to accept without PaddleOCR


def _env_int(name: str, default: int, *, min_value: int = 1, max_value: int = 64) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(min_value, min(max_value, value))


def _env_float(name: str, default: float, *, min_value: float = 0.0, max_value: float = 1.0) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(min_value, min(max_value, value))


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def default_config() -> Agent50Config:
    return Agent50Config(
        max_workers=_env_int("FTTH_AGENT50_WORKERS", 4, min_value=1, max_value=16),
        max_iterations=_env_int("FTTH_AGENT50_MAX_ITERATIONS", 6, min_value=1, max_value=10),
        paddle_threshold=_env_float("FTTH_AGENT50_PADDLE_THRESHOLD", 0.90),
        use_ollama=_env_bool("FTTH_AGENT50_USE_OLLAMA", True),
        use_cache=_env_bool("FTTH_AGENT50_USE_CACHE", False),
        ollama_host=os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/"),
        ollama_model=os.environ.get("OLLAMA_VISION_MODEL", "qwen2.5vl:latest"),
        request_timeout_s=_env_int("FTTH_AGENT50_TIMEOUT_S", 20, min_value=3, max_value=120),
        streetview_radius_m=_env_int("FTTH_AGENT50_STREETVIEW_RADIUS_M", 100, min_value=1, max_value=500),
        ocr_engine=os.environ.get("FTTH_AGENT50_OCR_ENGINE", "vision_primary").strip().lower(),
        ollama_ocr_threshold=_env_float("FTTH_AGENT50_OLLAMA_OCR_THRESHOLD", 0.70),
    )


def _dotenv_values() -> dict[str, str]:
    env_file = _PROJECT_ROOT / ".env"
    if not env_file.exists():
        return {}
    try:
        from dotenv import dotenv_values

        return {
            str(k): str(v).strip()
            for k, v in dotenv_values(env_file).items()
            if v is not None and str(v).strip()
        }
    except Exception:
        return {}


def _config_value(*names: str) -> str:
    values = _dotenv_values()
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
        if values.get(name):
            return values[name]
    return ""


def _google_key() -> str:
    return _config_value(
        "GOOGLE_STREETVIEW_API_KEY",
        "GOOGLE_API_KEY",
        "GOOGLE_MAPS_API_KEY",
        "GOOGLE_GEOCODING_API_KEY",
    )


def _ssl_verify() -> bool | str:
    """CA bundle for Google HTTPS calls, with corporate-network escape hatches."""
    if os.environ.get("FTTH_SSL_VERIFY", "1").lower() in {"0", "false", "no", "off"}:
        return False
    custom = os.environ.get("FTTH_CA_BUNDLE", "").strip()
    if custom:
        p = Path(custom)
        if p.is_file():
            return str(p)
        logger.warning("Agent5-0 FTTH_CA_BUNDLE path not found: %s", custom)
    try:
        import certifi

        return certifi.where()
    except ImportError:
        return True


def _google_get(url: str, *, params: dict[str, Any], timeout: float) -> requests.Response:
    """GET Google HTTPS endpoints, retrying once without verification on SSL trust failure."""
    global _SSL_VERIFY_WARNED
    verify = _ssl_verify()
    if verify is False and not _SSL_VERIFY_WARNED:
        logger.warning("Agent5-0 FTTH_SSL_VERIFY=0; HTTPS certificate verification is disabled")
        _SSL_VERIFY_WARNED = True
    try:
        resp = requests.get(url, params=params, timeout=timeout, verify=verify)
        resp.raise_for_status()
        return resp
    except requests.exceptions.SSLError as exc:
        allow_fallback = os.environ.get("FTTH_SSL_INSECURE_FALLBACK", "1").lower() not in {
            "0",
            "false",
            "no",
            "off",
        }
        if not allow_fallback or verify is False:
            raise
        logger.warning(
            "Agent5-0 HTTPS certificate verification failed (%s). Retrying without verification. "
            "Fix by setting FTTH_CA_BUNDLE to your corporate root PEM, or FTTH_SSL_VERIFY=0.",
            exc,
        )
        try:
            import urllib3

            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        except Exception:
            pass
        resp = requests.get(url, params=params, timeout=timeout, verify=False)
        resp.raise_for_status()
        return resp


def _safe_key(data: str) -> str:
    return hashlib.sha1(data.encode("utf-8")).hexdigest()[:24]


def _cache_path(kind: str, key: str, suffix: str) -> Path:
    path = _CACHE_ROOT / kind
    path.mkdir(parents=True, exist_ok=True)
    return path / f"{key}.{suffix}"


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return None


def _write_json(path: Path, data: dict[str, Any]) -> None:
    try:
        path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    except Exception:
        logger.debug("Agent5-0 cache write failed: %s", path)


def _read_bytes(path: Path) -> bytes | None:
    try:
        if path.exists():
            data = path.read_bytes()
            return data if len(data) >= _MIN_STREETVIEW_BYTES else None
    except Exception:
        return None
    return None


def _write_bytes(path: Path, data: bytes) -> None:
    try:
        path.write_bytes(data)
    except Exception:
        logger.debug("Agent5-0 image cache write failed: %s", path)


def _house_number(address: str) -> str:
    match = re.match(r"\s*(\d+[A-Za-z]?)\b", address or "")
    return match.group(1).upper() if match else ""


def _digits(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    d_lon = lon2 - lon1
    x = math.sin(d_lon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(d_lon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def _nudge_coords(lat: float, lon: float, heading_deg: float, lateral_m: float) -> tuple[float, float]:
    if not lateral_m:
        return lat, lon
    h = math.radians(heading_deg + 90.0)
    north = lateral_m * math.cos(h)
    east = lateral_m * math.sin(h)
    meters_per_deg_lat = 111_320.0
    dlat = north / meters_per_deg_lat
    dlon = east / (meters_per_deg_lat * max(math.cos(math.radians(lat)), 1e-6))
    return lat + dlat, lon + dlon


def streetview_metadata(
    lat: float,
    lon: float,
    *,
    timeout_s: int = 20,
    use_cache: bool = False,
) -> tuple[bool, dict[str, Any]]:
    key = _google_key()
    if not key:
        return False, {"status": "NO_GOOGLE_KEY"}
    cache_key = _safe_key(f"{lat:.6f},{lon:.6f}")
    cache_file = _cache_path("metadata", cache_key, "json")
    cached = _read_json(cache_file) if use_cache else None
    if cached:
        return cached.get("status") == "OK", cached

    params = {
        "location": f"{lat},{lon}",
        "radius": "100",
        "source": "outdoor",
        "key": key,
    }
    try:
        resp = _google_get(_SV_METADATA_URL, params=params, timeout=timeout_s)
        data = resp.json()
        _write_json(cache_file, data)
        return data.get("status") == "OK", data
    except Exception as exc:
        return False, {"status": "ERROR", "error": str(exc)[:200]}


def _road_facing_heading(meta: dict[str, Any], target_lat: float, target_lon: float) -> float:
    loc = meta.get("location") or {}
    try:
        cam_lat = float(loc["lat"])
        cam_lon = float(loc["lng"])
    except Exception:
        return 0.0
    return _bearing(cam_lat, cam_lon, target_lat, target_lon)


def fetch_streetview_image(
    lat: float,
    lon: float,
    heading: float,
    fov: int,
    *,
    config: Agent50Config,
) -> tuple[bytes | None, str]:
    key = _google_key()
    if not key:
        return None, "NO_GOOGLE_KEY"
    cache_key = _safe_key(f"{lat:.7f},{lon:.7f},{heading:.1f},{fov},{config.streetview_size},s{config.streetview_scale}")
    cache_file = _cache_path("images", cache_key, "jpg")
    cached = _read_bytes(cache_file) if config.use_cache else None
    params = {
        "size": config.streetview_size,
        "scale": config.streetview_scale,
        "source": "outdoor",
        "location": f"{lat},{lon}",
        "heading": round(heading, 1),
        "pitch": "0",
        "fov": str(fov),
        "radius": str(config.streetview_radius_m),
        "key": key,
        "return_error_code": "true",
    }
    safe_url = requests.Request("GET", _SV_IMAGE_URL, params=params).prepare().url or ""
    safe_url = safe_url.split("&key=")[0] + "&key=HIDDEN"
    if cached:
        return cached, safe_url
    try:
        resp = _google_get(_SV_IMAGE_URL, params=params, timeout=config.request_timeout_s)
        content_type = resp.headers.get("Content-Type", "")
        if (
            resp.status_code != 200
            or not content_type.startswith("image/")
            or len(resp.content) < _MIN_STREETVIEW_BYTES
        ):
            return None, safe_url
        _write_bytes(cache_file, resp.content)
        return resp.content, safe_url
    except Exception:
        return None, safe_url


def _paddle_variants(image_bytes: bytes) -> list[tuple[str, bytes]]:
    variants: list[tuple[str, bytes]] = [("full", image_bytes)]
    try:
        variants.append(("clarified", clarify_image(image_bytes)))
    except Exception:
        pass
    try:
        variants.extend(generate_ocr_crops(image_bytes))
    except Exception:
        pass
    return variants


def _run_paddle_on_image(
    image_bytes: bytes,
    expected: str,
    *,
    threshold: float,
) -> dict[str, Any]:
    best_text = ""
    best_conf = 0.0
    variants_run = 0
    for name, data in _paddle_variants(image_bytes):
        variants_run += 1
        matched, conf, text = detect_house_number_with_paddle(data, expected, threshold=threshold)
        if text and (conf >= best_conf or not best_text):
            best_text = text
            best_conf = conf
        if matched:
            return {
                "matched": True,
                "confidence": conf,
                "text": text,
                "variant": name,
                "variants_run": variants_run,
            }
    return {
        "matched": False,
        "confidence": best_conf,
        "text": best_text,
        "variant": "",
        "variants_run": variants_run,
    }


def _ask_local_ollama(
    image_bytes: bytes,
    *,
    expected: str,
    address: str,
    config: Agent50Config,
    ocr_mode: bool = False,
) -> dict[str, Any] | None:
    """Call the local Ollama vision model.

    When *ocr_mode* is True the prompt asks for a direct house-number read
    (primary OCR role).  When False it asks only for camera-guidance hints
    (legacy guidance-only role used in paddle_primary mode).
    """
    if not config.use_ollama:
        return None
    if ocr_mode:
        prompt = (
            "Read the house number visible in this Street View image. "
            "Do not guess. Return JSON only with keys: "
            "house_number_text, house_number_visible, confidence."
        )
    else:
        prompt = (
            "You are helping a local offline OCR loop find a house number in a Street View image. "
            f"Expected house number: {expected!r}. Address: {address!r}. "
            "Do not invent a number. Return only JSON with keys: "
            "house_number_visible (bool), house_number_text (string), confidence (0..1), "
            "obstruction (bool), obstruction_type (none/tree/bush/vehicle/sign/other), "
            "recommended_fov (integer 10..60), recommended_heading_delta_deg (number -15..15), "
            "recommended_lateral_move_meters (number -10..10), structure_type "
            "(SFH/MDU/Commercial/Unknown), notes (short string)."
        )
    body = {
        "model": config.ollama_model,
        "prompt": prompt,
        "images": [base64.b64encode(image_bytes).decode("ascii")],
        "stream": False,
        "format": "json",
        "options": {"temperature": 0, "num_predict": 80 if ocr_mode else 240},
    }
    try:
        resp = requests.post(
            f"{config.ollama_host.rstrip('/')}/api/generate",
            json=body,
            timeout=max(60 if ocr_mode else 45, config.request_timeout_s),
        )
        resp.raise_for_status()
        text = str((resp.json() or {}).get("response") or "").strip()
        if not text:
            return None
        data = parse_llm_json_object(text)
        return data if data else None
    except Exception as exc:
        logger.debug("Agent5-0 Ollama unavailable/error: %s", exc)
        return None


def _run_vision_ocr(
    image_bytes: bytes,
    *,
    expected: str,
    address: str,
    config: Agent50Config,
) -> dict[str, Any]:
    """Ask Ollama to read the house number directly (primary OCR mode).

    Returns a dict with:
      matched (bool), confidence (float), text (str), raw_guidance (dict|None)
    """
    guidance = _ask_local_ollama(
        image_bytes,
        expected=expected,
        address=address,
        config=config,
        ocr_mode=True,
    )
    if not guidance:
        return {"matched": False, "confidence": 0.0, "text": "", "raw_guidance": None}

    ocr_text = str(
        guidance.get("house_number_text")
        or guidance.get("house_number")
        or guidance.get("number")
        or guidance.get("text")
        or ""
    ).strip()
    # Strip non-digit chars for comparison
    ocr_digits = re.sub(r"\D+", "", ocr_text)
    expected_digits = re.sub(r"\D+", "", expected)
    conf = _as_float(guidance.get("confidence"), 0.0)
    if conf > 1.0:
        conf = conf / 100.0

    matched = bool(
        ocr_text
        and conf >= config.ollama_ocr_threshold
        and expected
        and (
            expected.upper() in ocr_text.upper()
            or (expected_digits and expected_digits in ocr_digits)
        )
    )
    logger.debug(
        "Agent5-0 Vision OCR: expected=%r text=%r conf=%.3f matched=%s",
        expected, ocr_text, conf, matched,
    )
    return {
        "matched": matched,
        "confidence": conf,
        "text": ocr_text,
        "raw_guidance": guidance,
    }


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int, low: int, high: int) -> int:
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        parsed = default
    return max(low, min(high, parsed))


def _normalize_structure(value: Any) -> str:
    raw = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if raw in {"sfh", "single_family", "house", "detached_house"}:
        return "SFH"
    if raw in {"mdu", "multi_family", "multifamily", "apartment", "apartments"}:
        return "MDU"
    if raw in {"commercial", "industrial", "mixed_use"}:
        return "Commercial"
    return "Unknown"


def _streetview_unavailable_reason(metadata: dict[str, Any]) -> str:
    status = str(metadata.get("status") or "UNKNOWN").strip() or "UNKNOWN"
    error = str(metadata.get("error_message") or "").strip()
    if error:
        return f"street view unavailable: {status} - {error[:240]}"
    return f"street view unavailable: {status}"


def analyze_record(record: dict[str, Any], *, config: Agent50Config | None = None) -> dict[str, Any]:
    """Analyze one address/coordinate record using only local OCR/LLM analysis.

    OCR engine selection is controlled by ``config.ocr_engine``:
      - ``vision_primary``  Ollama reads first; PaddleOCR is the fallback.
      - ``paddle_primary``  PaddleOCR first; Ollama only guides camera angles.
      - ``vision_only``     Ollama only; PaddleOCR is skipped.
    """
    config = config or default_config()
    ocr_engine = (config.ocr_engine or "vision_primary").strip().lower()
    address = str(record.get("address") or record.get("raw_address") or "").strip()
    expected = str(record.get("house_number") or _house_number(address)).strip().upper()
    lat = _as_float(record.get("latitude"))
    lon = _as_float(record.get("longitude"))
    started = time.monotonic()

    base = {
        "agent_name": _AGENT_NAME,
        "address": address,
        "expected_house_number": expected,
        "latitude": lat,
        "longitude": lon,
        "analysis_mode": "offline",
        "ocr_engine": ocr_engine,
        "llm_provider": "ollama_local" if config.use_ollama else "disabled",
        "cloud_llm_used": False,
        "azure_vision_used": False,
        "openai_used": False,
    }
    _skip_base = {
        "confidence": 0,
        "structure_type": "Unknown",
        "ocr_match_found": False,
        "paddle_ocr_used": False,
        "paddle_ocr_match_found": False,
        "paddle_ocr_text": "",
        "ollama_ocr_used": False,
        "ollama_ocr_match_found": False,
        "ollama_ocr_text": "",
        "ollama_ocr_confidence": 0.0,
        "ocr_engine_used": "none",
    }
    if not expected:
        return {
            **base,
            **_skip_base,
            "status": "skipped",
            "reason": "no house number in address",
        }
    if not lat or not lon:
        return {
            **base,
            **_skip_base,
            "status": "skipped",
            "reason": "missing coordinates",
        }

    sv_available, sv_meta = streetview_metadata(
        lat,
        lon,
        timeout_s=config.request_timeout_s,
        use_cache=config.use_cache,
    )
    if not sv_available:
        return {
            **base,
            **_skip_base,
            "status": "no_imagery",
            "reason": _streetview_unavailable_reason(sv_meta),
            "streetview_metadata": sv_meta,
            "latency_ms": int((time.monotonic() - started) * 1000),
        }

    base_heading = _road_facing_heading(sv_meta, lat, lon)
    images_fetched = 0
    paddle_runs = 0
    best_text = ""
    best_conf = 0.0
    best_step = ""
    last_guidance: dict[str, Any] | None = None
    structure_votes: list[str] = []
    trace: list[dict[str, Any]] = []
    # Ollama OCR accumulators
    ollama_ocr_used = False
    ollama_best_text = ""
    ollama_best_conf = 0.0

    for iteration in range(config.max_iterations):
        fov = config.fov_ladder[min(iteration, len(config.fov_ladder) - 1)]
        if last_guidance:
            fov = _as_int(last_guidance.get("recommended_fov"), fov, 10, fov)
        lateral = config.lateral_steps_m[min(iteration, len(config.lateral_steps_m) - 1)]
        if last_guidance:
            lateral = _as_float(last_guidance.get("recommended_lateral_move_meters"), lateral)
        req_lat, req_lon = _nudge_coords(lat, lon, base_heading, lateral)
        llm_delta = _as_float((last_guidance or {}).get("recommended_heading_delta_deg"), 0.0)
        headings = [base_heading + llm_delta + offset for offset in config.heading_offsets]

        for angle_idx, heading in enumerate(headings):
            step = f"iter{iteration + 1}_angle{angle_idx + 1}_fov{fov}"
            image_bytes, image_url = fetch_streetview_image(req_lat, req_lon, heading, fov, config=config)
            if not image_bytes:
                trace.append({"step": step, "status": "no_image", "heading": heading, "fov": fov})
                continue
            images_fetched += 1

            trace_entry: dict[str, Any] = {
                "step": step,
                "heading": round(heading, 1),
                "fov": fov,
                "lateral_m": lateral,
                "image_url": image_url,
            }

            # vision_primary / vision_only: Ollama reads the number FIRST
            if ocr_engine in ("vision_primary", "vision_only") and config.use_ollama:
                vision_result = _run_vision_ocr(
                    image_bytes, expected=expected, address=address, config=config
                )
                ollama_ocr_used = True
                v_text = vision_result["text"]
                v_conf = vision_result["confidence"]
                if v_text and v_conf > ollama_best_conf:
                    ollama_best_text = v_text
                    ollama_best_conf = v_conf

                raw_g = vision_result.get("raw_guidance") or {}
                trace_entry["ollama_ocr"] = {
                    "house_number_text": v_text,
                    "confidence": v_conf,
                    "matched": vision_result["matched"],
                    "obstruction": bool(raw_g.get("obstruction")),
                    "obstruction_type": str(raw_g.get("obstruction_type") or "none")[:40],
                    "recommended_fov": raw_g.get("recommended_fov"),
                    "recommended_heading_delta_deg": raw_g.get("recommended_heading_delta_deg"),
                    "recommended_lateral_move_meters": raw_g.get("recommended_lateral_move_meters"),
                    "structure_type": _normalize_structure(raw_g.get("structure_type")),
                }
                # Use Ollama guidance for next angle even if it didn't match
                if raw_g:
                    last_guidance = raw_g
                    structure = _normalize_structure(raw_g.get("structure_type"))
                    if structure != "Unknown":
                        structure_votes.append(structure)

                if vision_result["matched"]:
                    confidence = max(70, int(v_conf * 100))
                    trace.append(trace_entry)
                    return {
                        **base,
                        "status": "analyzed",
                        "structure_type": structure_votes[-1] if structure_votes else "Unknown",
                        "confidence": confidence,
                        "house_number_conf": v_conf,
                        "high_conf_house_number": True,
                        "ocr_match_found": True,
                        "paddle_ocr_used": False,
                        "paddle_ocr_match_found": False,
                        "paddle_ocr_text": "",
                        "ollama_ocr_used": True,
                        "ollama_ocr_match_found": True,
                        "ollama_ocr_text": v_text[:500],
                        "ollama_ocr_confidence": v_conf,
                        "ocr_engine_used": "ollama_vision",
                        "winning_step": step,
                        "images_fetched": images_fetched,
                        "paddle_runs": paddle_runs,
                        "iterations_used": iteration + 1,
                        "streetview_heading": base_heading,
                        "streetview_metadata": sv_meta if isinstance(sv_meta, dict) else {},
                        "search_trace": trace,
                        "latency_ms": int((time.monotonic() - started) * 1000),
                    }

                # vision_only: no PaddleOCR fallback - go to next image
                if ocr_engine == "vision_only":
                    trace.append(trace_entry)
                    if last_guidance and last_guidance.get("house_number_visible"):
                        break
                    continue

                # vision_primary: fall through to PaddleOCR as fallback

            # PaddleOCR is primary in paddle_primary, fallback otherwise.
            paddle = _run_paddle_on_image(image_bytes, expected, threshold=config.paddle_threshold)
            paddle_runs += int(paddle.get("variants_run") or 0)
            if paddle.get("text"):
                p_conf = float(paddle.get("confidence") or 0.0)
                if p_conf >= best_conf or not best_text:
                    best_text = str(paddle.get("text") or "")
                    best_conf = p_conf
                    best_step = step
            trace_entry["paddle_match"] = bool(paddle.get("matched"))
            trace_entry["paddle_confidence"] = paddle.get("confidence", 0)
            trace_entry["paddle_variant"] = paddle.get("variant", "")

            if paddle.get("matched"):
                p_conf = float(paddle.get("confidence") or 0.0)
                confidence = max(90, int(p_conf * 100))
                trace.append(trace_entry)
                return {
                    **base,
                    "status": "analyzed",
                    "structure_type": structure_votes[-1] if structure_votes else "Unknown",
                    "confidence": confidence,
                    "house_number_conf": p_conf,
                    "high_conf_house_number": True,
                    "ocr_match_found": True,
                    "paddle_ocr_used": True,
                    "paddle_ocr_match_found": True,
                    "paddle_ocr_text": str(paddle.get("text") or "")[:500],
                    "ollama_ocr_used": ollama_ocr_used,
                    "ollama_ocr_match_found": False,
                    "ollama_ocr_text": ollama_best_text[:500],
                    "ollama_ocr_confidence": ollama_best_conf,
                    "ocr_engine_used": "paddle_fallback" if ocr_engine == "vision_primary" else "paddle",
                    "winning_step": step,
                    "winning_variant": paddle.get("variant", ""),
                    "images_fetched": images_fetched,
                    "paddle_runs": paddle_runs,
                    "iterations_used": iteration + 1,
                    "streetview_heading": base_heading,
                    "streetview_metadata": sv_meta if isinstance(sv_meta, dict) else {},
                    "search_trace": trace,
                    "latency_ms": int((time.monotonic() - started) * 1000),
                }

            # paddle_primary: use Ollama only for guidance (angle/zoom hints)
            if ocr_engine == "paddle_primary" and config.use_ollama:
                guidance = _ask_local_ollama(
                    image_bytes, expected=expected, address=address, config=config, ocr_mode=False
                )
                if guidance:
                    last_guidance = guidance
                    structure = _normalize_structure(guidance.get("structure_type"))
                    if structure != "Unknown":
                        structure_votes.append(structure)
                    trace_entry["ollama_guidance"] = {
                        "house_number_visible": bool(guidance.get("house_number_visible")),
                        "house_number_text": str(guidance.get("house_number_text") or "")[:80],
                        "confidence": _as_float(guidance.get("confidence"), 0.0),
                        "obstruction": bool(guidance.get("obstruction")),
                        "obstruction_type": str(guidance.get("obstruction_type") or "none")[:40],
                        "recommended_fov": guidance.get("recommended_fov"),
                        "recommended_heading_delta_deg": guidance.get("recommended_heading_delta_deg"),
                        "recommended_lateral_move_meters": guidance.get("recommended_lateral_move_meters"),
                        "structure_type": _normalize_structure(guidance.get("structure_type")),
                    }

            trace.append(trace_entry)
            if last_guidance and last_guidance.get("house_number_visible"):
                break

    structure_type = structure_votes[-1] if structure_votes else "Unknown"
    status = "review" if images_fetched else "no_imagery"
    confidence = int(best_conf * 100) if best_conf else (int(ollama_best_conf * 100) if ollama_best_conf else (45 if images_fetched else 0))
    ocr_engine_used_final = "none"
    if paddle_runs > 0 and best_text:
        ocr_engine_used_final = "paddle"
    elif ollama_ocr_used and ollama_best_text:
        ocr_engine_used_final = "ollama_vision"
    unmatched_reason = {
        "vision_primary": "house number not confirmed by Ollama vision or PaddleOCR fallback",
        "paddle_primary": "house number not matched by PaddleOCR",
        "vision_only": "house number not confirmed by Ollama vision",
    }.get(ocr_engine, "house number not matched")
    return {
        **base,
        "status": status,
        "reason": unmatched_reason if images_fetched else "no usable street view image",
        "structure_type": structure_type,
        "confidence": confidence,
        "house_number_conf": max(best_conf, ollama_best_conf),
        "high_conf_house_number": False,
        "ocr_match_found": False,
        "paddle_ocr_used": paddle_runs > 0,
        "paddle_ocr_match_found": False,
        "paddle_ocr_text": best_text[:500],
        "ollama_ocr_used": ollama_ocr_used,
        "ollama_ocr_match_found": False,
        "ollama_ocr_text": ollama_best_text[:500],
        "ollama_ocr_confidence": ollama_best_conf,
        "ocr_engine_used": ocr_engine_used_final,
        "winning_step": best_step,
        "images_fetched": images_fetched,
        "paddle_runs": paddle_runs,
        "iterations_used": config.max_iterations,
        "streetview_heading": base_heading,
        "streetview_metadata": sv_meta if isinstance(sv_meta, dict) else {},
        "search_trace": trace,
        "latency_ms": int((time.monotonic() - started) * 1000),
    }


def _ensure_table(session) -> None:
    if session.execute(_sel(AgentTable).where(AgentTable.agent_name == _AGENT_NAME)).scalar_one_or_none():
        return
    session.add(AgentTable(
        agent_name=_AGENT_NAME,
        display_name=_DISPLAY_NAME,
        owner="system",
        description="Offline local Ollama vision OCR (primary) + PaddleOCR fallback house-number validation",
        color_rules=[
            {"field": "ocr_match_found", "value": True, "color": "#16a34a", "label": "OCR Match"},
            {"field": "status", "value": "review", "color": "#d97706", "label": "Review"},
            {"field": "status", "value": "no_imagery", "color": "#6b7280", "label": "No Imagery"},
        ],
    ))
    session.commit()


def _persist_streetview_api_on_address(session, address_id: int, data: dict[str, Any]) -> None:
    sv_meta = data.get("streetview_metadata")
    if not isinstance(sv_meta, dict) or not sv_meta:
        return
    from sqlalchemy.orm.attributes import flag_modified

    from data_ingestion.utils.agent_api_key_status import persist_streetview_api_summary

    addr = session.get(Address, address_id)
    if not addr:
        return
    addr.raw_metadata = persist_streetview_api_summary(dict(addr.raw_metadata or {}), sv_meta)
    flag_modified(addr, "raw_metadata")


def _upsert(session, job_id: str, address_id: int, data: dict[str, Any]) -> None:
    from sqlalchemy.dialects.postgresql import insert as _pg_insert

    now = datetime.utcnow()
    stmt = _pg_insert(AgentResult).values(
        agent_name=_AGENT_NAME,
        job_id=job_id,
        address_id=address_id,
        data=data,
        created_at=now,
        updated_at=now,
    ).on_conflict_do_update(
        constraint="uq_agent_results_agent_address",
        set_={"data": data, "updated_at": now, "job_id": job_id},
    )
    session.execute(stmt)
    _persist_streetview_api_on_address(session, address_id, data)


def _summary(total: int = 0) -> dict[str, Any]:
    return {
        "total": total,
        "analyzed": 0,
        "matched": 0,
        "review": 0,
        "no_imagery": 0,
        "skipped": 0,
        "failed": 0,
        "agent_name": _AGENT_NAME,
        "paddle": paddleocr_runtime_status(),
    }


def _update_summary(summary: dict[str, Any], result: dict[str, Any]) -> None:
    status = result.get("status")
    if status == "analyzed":
        summary["analyzed"] += 1
    elif status == "review":
        summary["review"] += 1
    elif status == "no_imagery":
        summary["no_imagery"] += 1
    elif status == "skipped":
        summary["skipped"] += 1
    if result.get("ocr_match_found"):
        summary["matched"] += 1


def run_agent50_for_job(
    job_id: str,
    address_ids: list[int] | None = None,
    progress_callback=None,
    config: Agent50Config | None = None,
) -> dict[str, Any]:
    """Run Agent 5-0 for DB addresses and write agent_results rows."""
    config = config or default_config()
    log_path = configure_agent_logger(logger, _AGENT_NAME)
    logger.info(
        "Agent 5-0 start | job_id=%s | address_ids=%s | ocr_engine=%s | log=%s",
        job_id,
        len(address_ids) if address_ids else "all",
        config.ocr_engine,
        log_path,
    )
    log_payload(logger, "agent50_config", config.__dict__)
    session = get_session_factory()()
    try:
        _ensure_table(session)
        stmt = _sel(Address).where(Address.job_id == job_id).order_by(Address.id)
        if address_ids:
            stmt = stmt.where(Address.id.in_(address_ids))
        addresses = session.scalars(stmt).all()
        records = [
            {
                "address_id": addr.id,
                "address": addr.validated_raw_address or addr.source_raw_address or addr.raw_address or "",
                "latitude": addr.validated_latitude if addr.validated_latitude is not None else addr.latitude,
                "longitude": addr.validated_longitude if addr.validated_longitude is not None else addr.longitude,
            }
            for addr in addresses
        ]
        summary = _summary(len(records))
        completed = 0
        with ThreadPoolExecutor(max_workers=min(config.max_workers, max(1, len(records)))) as pool:
            future_map = {
                pool.submit(analyze_record, record, config=config): record
                for record in records
            }
            for future in as_completed(future_map):
                record = future_map[future]
                completed += 1
                try:
                    result = future.result()
                    _upsert(session, job_id, int(record["address_id"]), result)
                    session.commit()
                    _update_summary(summary, result)
                except Exception as exc:
                    session.rollback()
                    summary["failed"] += 1
                    _upsert(session, job_id, int(record["address_id"]), {
                        "agent_name": _AGENT_NAME,
                        "status": "error",
                        "error": str(exc)[:200],
                        "confidence": 0,
                    })
                    session.commit()
                if progress_callback:
                    progress_callback(completed, len(records))
        return summary
    finally:
        session.close()


def _row_value(row: dict[str, Any], names: tuple[str, ...]) -> Any:
    norm = {str(k).strip().lower().replace(" ", "_"): v for k, v in row.items()}
    for name in names:
        key = name.lower().replace(" ", "_")
        if key in norm and norm[key] not in (None, ""):
            return norm[key]
    return None


def load_excel_records(path: str | Path) -> list[dict[str, Any]]:
    import openpyxl

    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = workbook.active
        rows = sheet.iter_rows(values_only=True)
        headers = [str(value or "").strip() for value in next(rows)]
        records: list[dict[str, Any]] = []
        for idx, values in enumerate(rows, 2):
            row = dict(zip(headers, values))
            address = _row_value(row, ("ADDRESS", "address", "raw_address"))
            lat = _row_value(row, ("LATITUDE", "lat", "latitude"))
            lon = _row_value(row, ("LONGITUDE", "lon", "lng", "longitude"))
            if address is None and lat is None and lon is None:
                continue
            records.append({
                "row_number": idx,
                "address": str(address or "").strip(),
                "latitude": lat,
                "longitude": lon,
            })
        return records
    finally:
        workbook.close()


def run_agent50_for_excel(
    input_path: str | Path,
    *,
    output_path: str | Path | None = None,
    config: Agent50Config | None = None,
) -> dict[str, Any]:
    """Run Agent 5-0 on an Excel file with ADDRESS/LATITUDE/LONGITUDE columns."""
    config = config or default_config()
    log_path = configure_agent_logger(logger, _AGENT_NAME)
    logger.info(
        "Agent 5-0 excel start | input=%s | output=%s | log=%s",
        input_path,
        output_path,
        log_path,
    )
    records = load_excel_records(input_path)
    results: list[dict[str, Any]] = []
    summary = _summary(len(records))
    with ThreadPoolExecutor(max_workers=min(config.max_workers, max(1, len(records)))) as pool:
        future_map = {pool.submit(analyze_record, record, config=config): record for record in records}
        for future in as_completed(future_map):
            record = future_map[future]
            try:
                result = future.result()
                result["row_number"] = record.get("row_number")
                results.append(result)
                _update_summary(summary, result)
            except Exception as exc:
                summary["failed"] += 1
                results.append({
                    "row_number": record.get("row_number"),
                    "address": record.get("address"),
                    "status": "error",
                    "error": str(exc)[:200],
                    "confidence": 0,
                })

    output = Path(output_path) if output_path else Path(input_path).with_name(
        f"{Path(input_path).stem}_agent5_0_results.xlsx"
    )
    write_excel_results(output, sorted(results, key=lambda r: int(r.get("row_number") or 0)))
    summary["output_path"] = str(output)
    return summary


def write_excel_results(path: str | Path, results: list[dict[str, Any]]) -> None:
    import openpyxl

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "agent5_0_results"
    headers = [
        "row_number",
        "address",
        "expected_house_number",
        "latitude",
        "longitude",
        "status",
        "ocr_match_found",
        "house_number_conf",
        "confidence",
        "paddle_ocr_text",
        "winning_step",
        "images_fetched",
        "paddle_runs",
        "iterations_used",
        "structure_type",
        "reason",
        "latency_ms",
    ]
    sheet.append(headers)
    for result in results:
        sheet.append([result.get(key, "") for key in headers])
    workbook.save(path)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run Agent 5-0 offline PaddleOCR/Ollama validation.")
    parser.add_argument("--excel", help="Excel file with ADDRESS/LATITUDE/LONGITUDE columns")
    parser.add_argument("--output", help="Optional output .xlsx path")
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument("--max-iterations", type=int, default=None)
    parser.add_argument("--no-ollama", action="store_true")
    args = parser.parse_args()

    cfg = default_config()
    cfg = Agent50Config(
        **{
            **cfg.__dict__,
            "max_workers": args.workers or cfg.max_workers,
            "max_iterations": args.max_iterations or cfg.max_iterations,
            "use_ollama": False if args.no_ollama else cfg.use_ollama,
        }
    )
    if not args.excel:
        parser.error("--excel is required for CLI use")
    summary = run_agent50_for_excel(args.excel, output_path=args.output, config=cfg)
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

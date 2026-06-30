"""Write Agent 5 detected house numbers to the reference pipeline output JSON."""

from __future__ import annotations
from data_ingestion.config.paths import STREET_VIEW_VENDOR

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_WRITE_LOCK = threading.Lock()
_REFERENCE_OUTPUT = STREET_VIEW_VENDOR / "output" / "house_numbers.json"


def default_output_paths() -> list[Path]:
    """JSON path updated on every Agent 5 run (reference pipeline output only)."""
    override = os.environ.get("FTTH_AGENT5_HOUSE_NUMBERS_JSON", "").strip()
    if override:
        return [Path(override)]
    return [_REFERENCE_OUTPUT]


def _normalize_view_name(view: str) -> str:
    base = str(view or "").strip()
    for suffix in ("_azure",):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
    return base


def _image_filename(result: dict[str, Any], lat: float, lon: float) -> str:
    view = _normalize_view_name(
        result.get("paddleocr_scan_view")
        or result.get("winning_step")
        or "streetview"
    )
    trace = result.get("search_trace") or []
    for entry in trace:
        entry_view = str(entry.get("view") or "")
        if entry_view == view or _normalize_view_name(entry_view) == view:
            heading = float(entry.get("heading", 0))
            fov = int(entry.get("fov", 60))
            return f"{lat:.6f}_{lon:.6f}_sv_{heading}_fov{fov}_s2.jpg"
    safe_view = view.replace("/", "_")
    return f"{lat:.6f}_{lon:.6f}_{safe_view}.jpg"


def build_house_number_entry(
    result: dict[str, Any],
    record: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Build one JSON row when a house number was detected for an address."""
    if result.get("status") != "analyzed":
        return None

    recognized = (result.get("paddleocr_scan_recognized") or "").strip()
    expected = (result.get("house_number") or (record or {}).get("house_number") or "").strip()
    if not recognized:
        if not result.get("ocr_match_found"):
            return None
        recognized = expected
    if not recognized:
        return None

    lat = float(result.get("latitude") or (record or {}).get("latitude") or 0)
    lon = float(result.get("longitude") or (record or {}).get("longitude") or 0)
    conf = float(result.get("paddleocr_scan_confidence") or 0)
    if conf <= 0:
        conf = float(result.get("house_number_conf") or 0)

    return {
        "image": _image_filename(result, lat, lon),
        "house_number": recognized,
        "confidence": round(conf, 4),
    }


def write_house_numbers_json(
    entries: list[dict[str, Any]],
    output_paths: list[Path] | Path | None = None,
) -> list[Path]:
    """Overwrite house_numbers.json at every configured output path."""
    paths = (
        [output_paths]
        if isinstance(output_paths, Path)
        else list(output_paths or default_output_paths())
    )
    payload = json.dumps(entries, indent=2) + "\n"
    written: list[Path] = []

    with _WRITE_LOCK:
        for path in paths:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(payload, encoding="utf-8")
            written.append(path.resolve())

    agent_logger = logging.getLogger("data_ingestion.agents.agent5_streetview")
    agent_logger.info(
        "Agent5 wrote %d house number(s) to %s",
        len(entries),
        ", ".join(str(p) for p in written),
    )
    return written

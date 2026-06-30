from __future__ import annotations

import os
from typing import Any

from .streetview import get_images, get_metadata
from .vision import analyze_images


VISUAL_TO_CLASS = {
    "detached_house": "SFU",
    "townhouse": "SFU",
    "low_rise_apt": "MDU",
    "high_rise_apt": "MDU",
    "commercial": "ANCHOR",
    "mixed_use": "MXU",
    "industrial": "ANCHOR",
    "unclear": None,
}


def _top2_diff(probs: dict[str, float] | None) -> float:
    if not probs:
        return 1.0
    values = sorted((float(value) for value in probs.values()), reverse=True)
    if len(values) < 2:
        return 1.0
    return values[0] - values[1]


def _should_run(ml_result: dict[str, Any]) -> bool:
    confidence = float(ml_result.get("confidence", 0.0))
    probs = ml_result.get("probs")
    missing_assessor_data = bool(ml_result.get("missing_assessor_data", False))
    return confidence < 0.75 or _top2_diff(probs) < 0.15 or missing_assessor_data


def _ml_class(ml_result: dict[str, Any]) -> str | None:
    return ml_result.get("class") or ml_result.get("structure_hint") or ml_result.get("final_class")


def _boost(confidence: float) -> float:
    return min(round(confidence + 0.12, 3), 0.98)


def validate_with_streetview(lat: float, lon: float, ml_result: dict[str, Any]) -> dict[str, Any]:
    ml_class = _ml_class(ml_result)
    ml_confidence = float(ml_result.get("confidence", 0.0))

    if not _should_run(ml_result):
        return {
            "sv_used": False,
            "final_class": ml_class,
            "confidence": ml_confidence,
            "vision_output": None,
            "status": "validated",
        }

    api_key = ml_result.get("streetview_api_key") or os.environ.get("GOOGLE_STREETVIEW_API_KEY")
    if not api_key:
        return {
            "sv_used": False,
            "final_class": ml_class,
            "confidence": ml_confidence,
            "vision_output": None,
            "status": "review_required",
        }

    metadata = get_metadata(lat, lon, api_key)
    if not metadata.get("usable"):
        return {
            "sv_used": False,
            "final_class": ml_class,
            "confidence": ml_confidence,
            "vision_output": None,
            "status": "review_required",
        }

    image_paths = get_images(lat, lon, api_key)
    vision_output = analyze_images(image_paths)
    visual_class = VISUAL_TO_CLASS.get(vision_output.get("structure_type"))

    if vision_output.get("image_quality") != "good" or visual_class is None:
        return {
            "sv_used": True,
            "final_class": ml_class,
            "confidence": ml_confidence,
            "vision_output": vision_output,
            "status": "review_required",
        }

    if visual_class == ml_class:
        return {
            "sv_used": True,
            "final_class": ml_class,
            "confidence": _boost(ml_confidence),
            "vision_output": vision_output,
            "status": "validated",
        }

    return {
        "sv_used": True,
        "final_class": ml_class,
        "confidence": ml_confidence,
        "vision_output": vision_output,
        "status": "review_required",
    }

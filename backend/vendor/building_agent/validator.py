from __future__ import annotations

from typing import Any


def validate_with_streetview(lat: float, lon: float, ml_result: dict[str, Any]) -> dict[str, Any]:
    return {
        "sv_used": False,
        "final_class": ml_result.get("class"),
        "confidence": float(ml_result.get("confidence", 0.0)),
        "status": "skipped",
    }

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any

import requests


OPENAI_URL = "https://api.openai.com/v1/chat/completions"
STRUCTURE_TYPES = {
    "detached_house",
    "townhouse",
    "low_rise_apt",
    "high_rise_apt",
    "commercial",
    "mixed_use",
    "industrial",
    "unclear",
}
IMAGE_QUALITIES = {"good", "partial", "obstructed", "no_structure"}
DEFAULT_OUTPUT = {
    "structure_type": "unclear",
    "visible_units_min": 0,
    "visible_units_max": 0,
    "floor_count": 0,
    "multiple_entrances": False,
    "multiple_mailboxes": False,
    "commercial_signage": False,
    "under_construction": False,
    "image_quality": "no_structure",
}


def _encode_image(path: str | Path) -> str:
    data = Path(path).read_bytes()
    return base64.b64encode(data).decode("ascii")


def _coerce_output(payload: dict[str, Any]) -> dict[str, Any]:
    output = DEFAULT_OUTPUT | payload
    if output["structure_type"] not in STRUCTURE_TYPES:
        output["structure_type"] = "unclear"
    if output["image_quality"] not in IMAGE_QUALITIES:
        output["image_quality"] = "obstructed"

    for key in ("visible_units_min", "visible_units_max", "floor_count"):
        try:
            output[key] = int(output[key])
        except (TypeError, ValueError):
            output[key] = 0

    for key in ("multiple_entrances", "multiple_mailboxes", "commercial_signage", "under_construction"):
        output[key] = bool(output[key])

    return output


def analyze_images(image_paths: list[str]) -> dict[str, Any]:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return DEFAULT_OUTPUT.copy()

    content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                "Analyze these Street View images and return only valid JSON with keys: "
                "structure_type, visible_units_min, visible_units_max, floor_count, "
                "multiple_entrances, multiple_mailboxes, commercial_signage, "
                "under_construction, image_quality."
            ),
        }
    ]

    for path in image_paths:
        content.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{_encode_image(path)}",
                },
            }
        )

    response = requests.post(
        OPENAI_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": content}],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        },
        timeout=60,
    )
    response.raise_for_status()
    message = response.json()["choices"][0]["message"]["content"]
    return _coerce_output(json.loads(message))

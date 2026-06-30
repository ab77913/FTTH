from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import requests


METADATA_URL = "https://maps.googleapis.com/maps/api/streetview/metadata"
IMAGE_URL = "https://maps.googleapis.com/maps/api/streetview"
IMAGE_DIR = Path(__file__).resolve().parents[1] / "data" / "streetview"


def _is_recent(image_date: str | None, max_age_years: int = 3) -> bool:
    if not image_date:
        return False
    try:
        year = int(image_date.split("-")[0])
    except (TypeError, ValueError):
        return False
    return date.today().year - year <= max_age_years


def get_metadata(lat: float, lon: float, api_key: str) -> dict[str, Any]:
    response = requests.get(
        METADATA_URL,
        params={
            "location": f"{lat},{lon}",
            "key": api_key,
        },
        timeout=15,
    )
    response.raise_for_status()
    payload = response.json()
    status = payload.get("status")
    image_date = payload.get("date")

    return {
        "status": status,
        "date": image_date,
        "usable": status == "OK" and _is_recent(image_date),
    }


def _download_image(lat: float, lon: float, api_key: str, heading: int, output_path: Path) -> Path:
    response = requests.get(
        IMAGE_URL,
        params={
            "size": "640x480",
            "location": f"{lat},{lon}",
            "heading": heading,
            "pitch": 0,
            "fov": 90,
            "key": api_key,
        },
        timeout=30,
    )
    response.raise_for_status()
    output_path.write_bytes(response.content)
    return output_path


def get_images(lat: float, lon: float, api_key: str) -> list[str]:
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    base_name = f"{lat:.6f}_{lon:.6f}".replace("-", "m").replace(".", "_")
    headings = (0, 90)
    paths = []

    for heading in headings:
        output_path = IMAGE_DIR / f"{base_name}_{heading}.jpg"
        paths.append(str(_download_image(lat, lon, api_key, heading, output_path)))

    return paths

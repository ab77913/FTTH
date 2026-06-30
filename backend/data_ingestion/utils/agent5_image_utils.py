"""Street View image preprocessing, crops, and variant fetch helpers."""

from __future__ import annotations

from io import BytesIO
import math

from PIL import Image, ImageEnhance, ImageFilter

PRIMARY_FOV = 90
ZOOM_FOV = {1: 60, 2: 70, 3: 50}

HOUSE_NUMBER_STEPS = [
    ("streetview_zoom1", 0, 60),
    ("streetview_zoom2", 0, 70),
    ("streetview_zoom3", 0, 50),
    ("streetview_right20_zoom1", 20, 90),
    ("streetview_right20_zoom2", 20, 70),
    ("streetview_right20_zoom3", 20, 50),
    ("streetview_left20_zoom1", -20, 90),
    ("streetview_left20_zoom2", -20, 70),
    ("streetview_left20_zoom3", -20, 50),
]

# Document steps b–j (center/right/left zoom ladder). Never truncate in fast mode.
HOUSE_NUMBER_STEP_LABELS = {
    "streetview_zoom1": "b: center 1x zoom",
    "streetview_zoom2": "c: center 2x zoom",
    "streetview_zoom3": "d: center 3x zoom",
    "streetview_right20_zoom1": "e: right 20° 1x zoom",
    "streetview_right20_zoom2": "f: right 20° 2x zoom",
    "streetview_right20_zoom3": "g: right 20° 3x zoom",
    "streetview_left20_zoom1": "h: left 20° 1x zoom",
    "streetview_left20_zoom2": "i: left 20° 2x zoom",
    "streetview_left20_zoom3": "j: left 20° 3x zoom",
}

# Oblique views of the *same* target house (small offsets around the road-facing
# heading) used to help structure classification. Kept narrow on purpose so the
# shots stay on the target property rather than the road / side houses.
CLASSIFICATION_ANGLE_OFFSETS = [
    ("streetview_oblique_left", -25),
    ("streetview_oblique_right", 25),
]
# Field of view for the oblique classification shots (focused on the building).
CLASSIFICATION_FOV = 60


def open_image_rgb(image_bytes: bytes) -> Image.Image:
    """Open image bytes as RGB (Google satellite tiles are often palette PNG)."""
    img = Image.open(BytesIO(image_bytes))
    if img.mode in ("RGBA", "LA", "P"):
        return img.convert("RGB")
    if img.mode != "RGB":
        return img.convert("RGB")
    return img


def ensure_rgb_jpeg_bytes(image_bytes: bytes, *, quality: int = 95) -> bytes:
    """Normalize arbitrary image bytes to JPEG RGB for APIs that reject palette PNG."""
    img = open_image_rgb(image_bytes)
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def clarify_image(image_bytes: bytes) -> bytes:
    img = open_image_rgb(image_bytes)
    img = img.filter(ImageFilter.UnsharpMask(radius=2, percent=150, threshold=3))
    img = ImageEnhance.Contrast(img).enhance(1.12)
    img = ImageEnhance.Sharpness(img).enhance(1.25)
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def generate_ocr_crops(image_bytes: bytes) -> list[tuple[str, bytes]]:
    img = open_image_rgb(image_bytes)
    width, height = img.size
    crop_specs = [
        ("center_crop", (width * 0.25, height * 0.25, width * 0.75, height * 0.75)),
        ("lower_crop", (width * 0.20, height * 0.50, width * 0.80, height * 0.95)),
        ("right_crop", (width * 0.55, height * 0.20, width * 0.95, height * 0.85)),
    ]
    crops: list[tuple[str, bytes]] = []
    for crop_name, box in crop_specs:
        buf = BytesIO()
        img.crop(box).convert("RGB").save(buf, format="JPEG", quality=95)
        crops.append((crop_name, buf.getvalue()))
    return crops


def analyze_hybrid_map_labels(
    vision_result: dict,
    image_bytes: bytes,
    expected_house_number: str = "",
) -> dict:
    """Summarize map-label OCR numbers, preferring the label nearest image center."""
    expected = "".join(ch for ch in str(expected_house_number or "") if ch.isdigit())
    img = open_image_rgb(image_bytes)
    cx = img.width / 2.0
    cy = img.height / 2.0
    numbers: list[dict] = []

    for block in vision_result.get("readResult", {}).get("blocks", []) or []:
        for line in block.get("lines", []) or []:
            for word in line.get("words", []) or []:
                text = str(word.get("text") or "").strip()
                digits = "".join(ch for ch in text if ch.isdigit())
                if not digits:
                    continue
                box = word.get("boundingBox") or word.get("bounding_polygon") or []
                wx, wy = _bbox_center(box, cx, cy)
                numbers.append({
                    "text": digits,
                    "raw_text": text,
                    "confidence": float(word.get("confidence") or 0.0),
                    "distance_to_center": math.hypot(wx - cx, wy - cy),
                })

    numbers.sort(key=lambda item: item["distance_to_center"])
    center_label = numbers[0]["text"] if numbers else ""
    matches = bool(expected and center_label == expected)
    return {
        "status": "match" if matches else ("no_match" if center_label else "no_labels"),
        "center_label": center_label,
        "matches_expected": matches,
        "visible_numbers": [item["text"] for item in numbers],
        "labels": numbers,
    }


def _bbox_center(box, default_x: float, default_y: float) -> tuple[float, float]:
    try:
        coords = [float(value) for value in box]
    except (TypeError, ValueError):
        return default_x, default_y
    if len(coords) < 4:
        return default_x, default_y
    xs = coords[0::2]
    ys = coords[1::2]
    return sum(xs) / len(xs), sum(ys) / len(ys)


def fetch_variant(
    lat: float,
    lon: float,
    base_heading: float,
    image_type: str,
    angle_offset: float,
    fov: int,
    fetch_streetview_image,
) -> tuple[str, bytes, str] | None:
    heading = (base_heading + angle_offset) % 360
    img, url = fetch_streetview_image(lat, lon, heading, fov)
    if not img:
        return None
    return image_type, img, url

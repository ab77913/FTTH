"""Local Ollama house-number extractor for low-quality Street View images.

Usage:
    python -m data_ingestion.utils.ollama_house_number_ocr images/example.jpg
    python -m data_ingestion.utils.ollama_house_number_ocr images/example.jpg --model moondream:latest

The utility sends the original image plus enhanced crops to a local Ollama
vision model and returns a consensus list of digit sequences. It is intentionally
standalone so it can be used for debugging Agent 5 image crops without touching
the production pipeline.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any

import requests
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

from data_ingestion.utils.json_utils import parse_llm_json_object


DEFAULT_MODEL = os.environ.get("OLLAMA_VISION_MODEL", "qwen2.5vl:latest")
DEFAULT_URL = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")


PROMPT = """
You are reading a house number from a low-quality Google Street View crop.
Look only for digits physically printed on the building, door, porch, mailbox,
post, curb, or address plaque. Ignore the Google watermark, map labels, dates,
coordinates, and any UI text.

Return strict JSON only:
{
  "numbers": ["digits only, most likely first"],
  "best_number": "digits only or empty string",
  "confidence": 0-100,
  "evidence": "short description of where the number appears"
}

If uncertain, still provide the best digit sequence you can see and lower the
confidence. Do not invent street names or extra text.
""".strip()


def _jpg_bytes(image: Image.Image, *, quality: int = 95) -> bytes:
    buf = io.BytesIO()
    image.convert("RGB").save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def _upscale(image: Image.Image, factor: int = 2) -> Image.Image:
    width, height = image.size
    return image.resize((width * factor, height * factor), Image.Resampling.LANCZOS)


def _high_contrast_number_plate(image: Image.Image) -> Image.Image:
    gray = ImageOps.grayscale(image)
    gray = ImageOps.autocontrast(gray, cutoff=1)
    gray = ImageEnhance.Contrast(gray).enhance(2.8)
    gray = ImageEnhance.Sharpness(gray).enhance(3.0)
    gray = gray.filter(ImageFilter.UnsharpMask(radius=1.2, percent=260, threshold=1))
    return gray.convert("RGB")


def _threshold_number_plate(image: Image.Image) -> Image.Image:
    gray = ImageOps.grayscale(image)
    gray = ImageOps.autocontrast(gray, cutoff=1)
    gray = ImageEnhance.Contrast(gray).enhance(3.2)
    thresholded = gray.point(lambda px: 255 if px > 126 else 0)
    thresholded = thresholded.filter(ImageFilter.MedianFilter(size=3))
    return thresholded.convert("RGB")


def _default_variant_dir(path: Path) -> Path:
    images_root = path.parent if path.parent.name.lower() == "images" else Path.cwd() / "images"
    return images_root / "ollama_house_number_debug" / path.stem


def build_variants(path: Path, *, save_dir: Path | None = None) -> list[tuple[str, bytes]]:
    """Create original, enhanced, grayscale, and likely house-number crops."""
    img = Image.open(path).convert("RGB")
    width, height = img.size
    variants: list[tuple[str, Image.Image]] = [
        ("original", img),
        ("enhanced_full", ImageEnhance.Contrast(
            img.filter(ImageFilter.UnsharpMask(radius=2, percent=180, threshold=2))
        ).enhance(1.35)),
    ]

    crop_specs = [
        ("number_plaque_tight", (0.315, 0.265, 0.415, 0.315)),
        ("number_plaque_wide", (0.260, 0.240, 0.500, 0.340)),
        ("upper_middle", (0.25, 0.18, 0.75, 0.42)),
        ("door_header", (0.30, 0.22, 0.58, 0.38)),
        ("front_center", (0.20, 0.18, 0.82, 0.62)),
        ("porch_left", (0.00, 0.22, 0.45, 0.72)),
        ("porch_right", (0.45, 0.18, 0.95, 0.72)),
    ]
    for name, (x1, y1, x2, y2) in crop_specs:
        crop = img.crop((int(width * x1), int(height * y1), int(width * x2), int(height * y2)))
        crop = _upscale(crop, 6 if name.startswith("number_plaque") else 3)
        sharp = crop.filter(ImageFilter.UnsharpMask(radius=2, percent=220, threshold=1))
        variants.append((f"{name}_sharp", ImageEnhance.Contrast(sharp).enhance(1.6)))

        gray = ImageOps.grayscale(crop)
        gray = ImageOps.autocontrast(gray)
        gray = ImageEnhance.Sharpness(gray).enhance(2.0)
        variants.append((f"{name}_gray", gray.convert("RGB")))
        if name.startswith("number_plaque"):
            variants.append((f"{name}_high_contrast", _high_contrast_number_plate(crop)))
            variants.append((f"{name}_threshold", _threshold_number_plate(crop)))

    if save_dir:
        save_dir.mkdir(parents=True, exist_ok=True)

    output: list[tuple[str, bytes]] = []
    for name, image in variants:
        data = _jpg_bytes(image)
        output.append((name, data))
        if save_dir:
            (save_dir / f"{name}.jpg").write_bytes(data)
    return output


def _extract_json(text: str) -> dict[str, Any]:
    parsed = parse_llm_json_object(text)
    if parsed:
        return parsed
    numbers = re.findall(r"\b\d{1,8}\b", text or "")
    return {"numbers": numbers, "best_number": numbers[0] if numbers else "", "confidence": 0, "evidence": (text or "")[:300]}


def ask_ollama(image_bytes: bytes, *, model: str, url: str, timeout: int) -> dict[str, Any]:
    payload = {
        "model": model,
        "prompt": PROMPT,
        "images": [base64.b64encode(image_bytes).decode("ascii")],
        "stream": False,
        "format": "json",
        "options": {
            "temperature": 0,
            "num_predict": 220,
        },
    }
    resp = requests.post(f"{url}/api/generate", json=payload, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    parsed = _extract_json(data.get("response", ""))
    parsed["_raw_response"] = data.get("response", "")
    return parsed


def _numbers_from_result(result: dict[str, Any]) -> list[str]:
    found: list[str] = []
    best = str(result.get("best_number") or "").strip()
    if re.fullmatch(r"\d{1,8}", best):
        found.append(best)
    numbers = result.get("numbers") or []
    if isinstance(numbers, list):
        for value in numbers:
            text = str(value or "").strip()
            if re.fullmatch(r"\d{1,8}", text):
                found.append(text)
    return found


def extract_house_numbers(
    image_path: str | Path,
    *,
    model: str = DEFAULT_MODEL,
    url: str = DEFAULT_URL,
    timeout: int = 120,
    max_variants: int | None = None,
    save_variants: bool = False,
    save_dir: str | Path | None = None,
) -> dict[str, Any]:
    path = Path(image_path)
    variant_dir = Path(save_dir) if save_dir else _default_variant_dir(path)
    variants = build_variants(path, save_dir=variant_dir if save_variants else None)
    if max_variants:
        variants = variants[:max(1, max_variants)]

    results: list[dict[str, Any]] = []
    votes: Counter[str] = Counter()
    for name, image_bytes in variants:
        try:
            result = ask_ollama(image_bytes, model=model, url=url, timeout=timeout)
        except Exception as exc:
            results.append({"variant": name, "error": str(exc)})
            continue
        result["variant"] = name
        for number in _numbers_from_result(result):
            votes[number] += 1
        results.append(result)

    best_number = votes.most_common(1)[0][0] if votes else ""
    confidence = min(100, 55 + votes[best_number] * 10) if best_number else 0
    return {
        "image": str(path),
        "model": model,
        "best_number": best_number,
        "numbers": [num for num, _count in votes.most_common()],
        "vote_counts": dict(votes.most_common()),
        "confidence": confidence,
        "variants_checked": len(variants),
        "saved_variants_dir": str(variant_dir) if save_variants else "",
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract house numbers from an image with local Ollama vision.")
    parser.add_argument("image", help="Path to a local image file")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Ollama model, default: {DEFAULT_MODEL}")
    parser.add_argument("--url", default=DEFAULT_URL, help=f"Ollama base URL, default: {DEFAULT_URL}")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--max-variants", type=int, default=None)
    parser.add_argument("--save-variants", action="store_true", help="Save enhanced images and crops under images/ollama_house_number_debug/")
    parser.add_argument("--save-dir", default=None, help="Optional folder for enhanced images/crops")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    args = parser.parse_args()

    result = extract_house_numbers(
        args.image,
        model=args.model,
        url=args.url,
        timeout=args.timeout,
        max_variants=args.max_variants,
        save_variants=args.save_variants,
        save_dir=args.save_dir,
    )
    print(json.dumps(result, indent=2 if args.pretty else None, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

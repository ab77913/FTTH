"""
Agent 5 — multi-region PaddleOCR house-number scan (from reference paddleocr_scan.py).

After each Street View image is captured, runs region crops + enhancement variants
through PaddleOCR and picks the best house number via weighted consensus.
"""

from __future__ import annotations

import logging
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

os.environ.setdefault("FLAGS_use_onednn", "0")
os.environ.setdefault("FLAGS_enable_pir_api", "0")

# Progressive digital zoom on an already-captured crop (no extra Street View API calls).
_LOCAL_CROP_TIGHTNESS = (1.4, 1.0, 0.72, 0.52)
_LOCAL_UPSCALE = (4, 6, 8, 10)
_MAX_LOCAL_ZOOM_STEPS = 10
_FAST_LOCAL_ZOOM_STEPS = 4

HOUSE_REGEX = re.compile(r"\b\d{2,6}[A-Za-z]?\b")
MIN_DETECTION_CONF = 0.22
MATCH_CONF_FLOOR = 0.50


def paddleocr_scan_enabled() -> bool:
    return os.environ.get("FTTH_ENABLE_PADDLEOCR_SCAN", "1").strip().lower() not in {
        "0",
        "false",
        "no",
    }


def clean_ocr_text(text: str) -> str:
    text = text.upper()
    for src, dst in (("O", "0"), ("I", "1"), ("L", "1"), ("S", "5"), ("B", "8"), ("Z", "2")):
        text = text.replace(src, dst)
    return text


def is_noise_house_number(num: str) -> bool:
    """Reject common OCR false positives (watermarks, padding zeros, tiny values)."""
    digits = re.sub(r"\D", "", num or "")
    if not digits:
        return True
    try:
        value = int(digits)
    except ValueError:
        return True
    if value <= 1:
        return True
    # Leading-zero padded noise such as 001, 002, 012.
    if len(digits) >= 3 and value < 100:
        return True
    return False


def valid_house_number(num: str) -> bool:
    digits = re.sub(r"\D", "", num or "")
    if len(digits) < 2 or len(digits) > 5:
        return False
    if is_noise_house_number(digits):
        return False
    try:
        n = int(digits)
    except ValueError:
        return False
    if n <= 0 or 1900 <= n <= 2100:
        return False
    return True


def choose_best_house_number(
    detections: list[tuple[str, float]],
    *,
    expected: str = "",
) -> tuple[str | None, float]:
    """Weighted consensus over (digits, ocr_confidence) pairs."""
    if not detections:
        return None, 0.0

    expected_digits = re.sub(r"\D", "", expected or "")
    filtered = [
        (num, conf)
        for num, conf in detections
        if valid_house_number(num)
    ]
    if expected_digits:
        matches = [(num, conf) for num, conf in filtered if num == expected_digits]
        if matches:
            filtered = matches
    if not filtered:
        return None, 0.0

    votes: dict[str, float] = {}
    frequency: Counter[str] = Counter()
    for num, conf in filtered:
        frequency[num] += 1
        weight = float(conf)
        weight += frequency[num] * 0.8
        if len(num) == 3:
            weight += 1.0
        if len(num) == 4:
            weight += 1.5
        if len(num) == 5:
            weight += 1.2
        votes[num] = votes.get(num, 0.0) + weight

    best_num, best_weight = max(votes.items(), key=lambda item: item[1])
    return best_num, round(best_weight, 4)


def _parse_legacy_ocr(result: Any) -> list[tuple[str, float]]:
    found: list[tuple[str, float]] = []
    if not result or result[0] is None:
        return found
    for line in result[0]:
        try:
            text = str(line[1][0]).strip()
            conf = float(line[1][1])
        except (IndexError, TypeError, ValueError):
            continue
        if conf < MIN_DETECTION_CONF:
            continue
        text = clean_ocr_text(text)
        for match in HOUSE_REGEX.findall(text):
            digits = re.sub(r"\D", "", match)
            if valid_house_number(digits):
                found.append((digits, conf))
    return found


def _parse_modern_ocr(result: Any) -> list[tuple[str, float]]:
    from data_ingestion.utils.agent5_paddle_ocr import _collect_text_confidence

    found: list[tuple[str, float]] = []
    for text, conf in _collect_text_confidence(result):
        if conf < MIN_DETECTION_CONF:
            continue
        text = clean_ocr_text(str(text))
        for match in HOUSE_REGEX.findall(text):
            digits = re.sub(r"\D", "", match)
            if valid_house_number(digits):
                found.append((digits, conf))
    return found


def _parse_ocr_result(result: Any) -> list[tuple[str, float]]:
    if isinstance(result, list):
        return _parse_legacy_ocr(result)
    return _parse_modern_ocr(result)


def _bytes_to_bgr(image_bytes: bytes) -> Any | None:
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        logger.debug("PaddleOCR focused scan OpenCV import failed: %s", exc)
        return None
    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    return img


def _poly_to_bbox(poly: Any, img_w: int, img_h: int) -> tuple[int, int, int, int]:
    if isinstance(poly, dict) and "x" in poly and "w" in poly:
        x1 = int(poly["x"])
        y1 = int(poly["y"])
        x2 = int(poly["x"] + poly["w"])
        y2 = int(poly["y"] + poly["h"])
    else:
        points = poly if isinstance(poly, list) else []
        xs = [int(p["x"]) for p in points if isinstance(p, dict) and "x" in p]
        ys = [int(p["y"]) for p in points if isinstance(p, dict) and "y" in p]
        if not xs or not ys:
            return (0, 0, img_w, img_h)
        x1, y1, x2, y2 = min(xs), min(ys), max(xs), max(ys)
    return (
        max(0, x1),
        max(0, y1),
        min(img_w, max(x2, x1 + 1)),
        min(img_h, max(y2, y1 + 1)),
    )


def _expand_bbox(
    x1: int, y1: int, x2: int, y2: int, img_w: int, img_h: int, pad_ratio: float
) -> tuple[int, int, int, int]:
    bw = max(x2 - x1, 1)
    bh = max(y2 - y1, 1)
    cx = (x1 + x2) / 2
    cy = (y1 + y2) / 2
    half_w = bw * pad_ratio / 2
    half_h = bh * pad_ratio / 2
    return (
        max(0, int(cx - half_w)),
        max(0, int(cy - half_h)),
        min(img_w, int(cx + half_w)),
        min(img_h, int(cy + half_h)),
    )


def _default_focus_box(img_w: int, img_h: int) -> tuple[int, int, int, int]:
    """Facade band where house numbers usually appear on Street View."""
    return (
        int(img_w * 0.18),
        int(img_h * 0.20),
        int(img_w * 0.82),
        int(img_h * 0.78),
    )


def _focus_box_from_gpt(img_w: int, img_h: int, gpt_result: dict | None) -> tuple[int, int, int, int] | None:
    if not gpt_result:
        return None
    direction = (gpt_result.get("house_direction") or "center").lower()
    if direction == "left":
        return (0, int(img_h * 0.12), int(img_w * 0.55), int(img_h * 0.88))
    if direction == "right":
        return (int(img_w * 0.45), int(img_h * 0.12), img_w, int(img_h * 0.88))
    return (int(img_w * 0.22), int(img_h * 0.15), int(img_w * 0.78), int(img_h * 0.88))


def _collect_focus_boxes_from_azure(
    vision: dict | None,
    expected: str,
    img_w: int,
    img_h: int,
) -> list[tuple[int, int, int, int]]:
    if not vision:
        return []
    expected_digits = re.sub(r"\D", "", expected or "")
    boxes: list[tuple[int, int, int, int]] = []

    def _maybe_add(text: str, poly: Any) -> None:
        normalized = clean_ocr_text(str(text or ""))
        digits = re.sub(r"\D", "", normalized)
        if not digits and not normalized:
            return
        is_match = bool(
            expected_digits
            and (
                digits == expected_digits
                or expected_digits in digits
                or digits in expected_digits
            )
        )
        has_digits = bool(re.search(r"\d{2,}", normalized))
        if is_match or (has_digits and valid_house_number(digits)):
            boxes.append(_poly_to_bbox(poly, img_w, img_h))

    for block in vision.get("readResult", {}).get("blocks", []):
        for line in block.get("lines", []):
            line_poly = line.get("boundingPolygon")
            line_text = line.get("text", "")
            if line_poly and expected_digits and expected_digits in re.sub(r"\D", "", line_text):
                boxes.append(_poly_to_bbox(line_poly, img_w, img_h))
            for word in line.get("words", []):
                _maybe_add(word.get("text", ""), word.get("boundingPolygon"))

    # De-duplicate near-identical boxes; prefer smaller / tighter detections first.
    unique: list[tuple[int, int, int, int]] = []
    for box in boxes:
        if box not in unique:
            unique.append(box)
    return unique


def _focus_boxes_for_image(
    img_w: int,
    img_h: int,
    *,
    expected: str,
    azure_vision: dict | None,
    gpt_result: dict | None,
) -> list[tuple[int, int, int, int]]:
    boxes = _collect_focus_boxes_from_azure(azure_vision, expected, img_w, img_h)
    gpt_box = _focus_box_from_gpt(img_w, img_h, gpt_result)
    if gpt_box and gpt_box not in boxes:
        boxes.append(gpt_box)
    default = _default_focus_box(img_w, img_h)
    if default not in boxes:
        boxes.append(default)
    return boxes


def _crop_bgr(img: Any, box: tuple[int, int, int, int]) -> Any:
    x1, y1, x2, y2 = box
    return img[y1:y2, x1:x2]


def _run_paddle_on_bgr(engine: Any, crop: Any, *, fast: bool) -> list[tuple[str, float]]:
    from data_ingestion.utils.agent5_paddle_ocr import (
        _PADDLE_INFER_LOCK,
        mark_paddle_runtime_unavailable,
        paddle_runtime_disabled,
        paddle_runtime_error_is_fatal,
    )

    if paddle_runtime_disabled():
        return []

    detections: list[tuple[str, float]] = []
    for variant in _enhance_variants(crop, fast=fast):
        try:
            with _PADDLE_INFER_LOCK:
                raw = _run_ocr(engine, variant)
            detections.extend(_parse_ocr_result(raw))
        except Exception as exc:
            logger.debug("PaddleOCR focused variant failed: %s", exc)
            if paddle_runtime_error_is_fatal(exc):
                mark_paddle_runtime_unavailable(exc)
                break
    return detections


def scan_focused_local_zoom(
    image_bytes: bytes,
    *,
    expected: str = "",
    azure_vision: dict | None = None,
    gpt_result: dict | None = None,
    fast: bool = False,
) -> tuple[str | None, float, int]:
    """
    Crop to the detected house-number area on an existing capture, then digitally
    zoom/enhance locally and re-run PaddleOCR — no extra Street View API calls.
    """
    if not paddleocr_scan_enabled() or not image_bytes:
        return None, 0.0, 0

    img = _bytes_to_bgr(image_bytes)
    if img is None:
        logger.debug("PaddleOCR focused scan skipped: OpenCV unavailable")
        return None, 0.0, 0

    from data_ingestion.utils.agent5_paddle_ocr import _ocr_engine

    engine = _ocr_engine()
    if engine is None:
        return None, 0.0, 0

    img_h, img_w = img.shape[:2]
    focus_boxes = _focus_boxes_for_image(
        img_w, img_h, expected=expected, azure_vision=azure_vision, gpt_result=gpt_result
    )

    max_steps = _FAST_LOCAL_ZOOM_STEPS if fast else _MAX_LOCAL_ZOOM_STEPS
    all_detections: list[tuple[str, float]] = []
    steps = 0
    for box in focus_boxes:
        for tightness in _LOCAL_CROP_TIGHTNESS:
            padded = _expand_bbox(*box, img_w, img_h, tightness)
            crop = _crop_bgr(img, padded)
            if crop is None or crop.size == 0:
                continue
            for upscale in _LOCAL_UPSCALE:
                try:
                    import cv2
                except ImportError:
                    break
                zoomed = cv2.resize(
                    crop, None, fx=upscale, fy=upscale, interpolation=cv2.INTER_CUBIC
                )
                all_detections.extend(_run_paddle_on_bgr(engine, zoomed, fast=True))
                steps += 1
                number, conf = choose_best_house_number(all_detections, expected=expected)
                if number and scan_matches_expected(number, conf, expected):
                    logger.debug(
                        "PaddleOCR focused match at tightness=%.2f upscale=%dx number=%r",
                        tightness,
                        upscale,
                        number,
                    )
                    return number, conf, len(all_detections)
                if steps >= max_steps:
                    break
            if steps >= max_steps:
                break
        if steps >= max_steps:
            break

    number, conf = choose_best_house_number(all_detections, expected=expected)
    if number and is_noise_house_number(number):
        number, conf = None, 0.0
    logger.debug(
        "PaddleOCR focused scan: steps=%d detections=%d number=%r conf=%.3f",
        steps,
        len(all_detections),
        number,
        conf,
    )
    return number, conf, len(all_detections)


def _get_regions(img: Any, *, fast: bool) -> list[Any]:
    h, w = img.shape[:2]
    regions = [
        img[int(h * 0.20) : int(h * 0.75), int(w * 0.15) : int(w * 0.85)],
        img[int(h * 0.30) : int(h * 0.85), int(w * 0.25) : int(w * 0.75)],
        img,
    ]
    if not fast:
        regions.extend([
            img[int(h * 0.10) : int(h * 0.45), int(w * 0.20) : int(w * 0.80)],
            img[int(h * 0.20) : int(h * 0.75), int(w * 0.00) : int(w * 0.50)],
            img[int(h * 0.20) : int(h * 0.75), int(w * 0.50) : int(w * 1.00)],
        ])
    return regions


def _enhance_variants(img: Any, *, fast: bool) -> list[Any]:
    try:
        import cv2
        import numpy as np
    except ImportError:
        return [img]

    big = cv2.resize(img, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
    variants = [big]

    gray = cv2.cvtColor(big, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=4.5, tileGridSize=(6, 6))
    gray = clahe.apply(gray)
    denoise = cv2.fastNlMeansDenoising(gray, None, 12, 7, 21)
    kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]])
    sharp = cv2.filter2D(denoise, -1, kernel)
    variants.append(cv2.cvtColor(sharp, cv2.COLOR_GRAY2BGR))

    adaptive = cv2.adaptiveThreshold(
        sharp, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 7
    )
    variants.append(cv2.cvtColor(adaptive, cv2.COLOR_GRAY2BGR))

    # Street View house numbers are often mounted at a slight angle or seen from
    # an oblique camera position. Small de-skew probes help Paddle find boxes
    # before the pipeline spends time fetching another remote image.
    for angle in (-7, 7):
        variants.append(_rotate_image(big, angle))
        variants.append(_rotate_image(cv2.cvtColor(sharp, cv2.COLOR_GRAY2BGR), angle))

    if fast:
        return variants

    inv = 255 - adaptive
    variants.append(cv2.cvtColor(inv, cv2.COLOR_GRAY2BGR))
    for threshold in (70, 90, 110, 140, 170):
        _, th = cv2.threshold(sharp, threshold, 255, cv2.THRESH_BINARY)
        variants.append(cv2.cvtColor(th, cv2.COLOR_GRAY2BGR))
        _, th_inv = cv2.threshold(sharp, threshold, 255, cv2.THRESH_BINARY_INV)
        variants.append(cv2.cvtColor(th_inv, cv2.COLOR_GRAY2BGR))
    return variants


def _rotate_image(img: Any, angle_deg: float) -> Any:
    try:
        import cv2
    except ImportError:
        return img
    h, w = img.shape[:2]
    center = (w / 2.0, h / 2.0)
    matrix = cv2.getRotationMatrix2D(center, angle_deg, 1.0)
    return cv2.warpAffine(
        img,
        matrix,
        (w, h),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
    )


def _run_ocr(engine: Any, variant: Any) -> Any:
    if hasattr(engine, "ocr"):
        try:
            return engine.ocr(variant, cls=True)
        except TypeError:
            return engine.ocr(variant)
    if hasattr(engine, "predict"):
        import tempfile

        import cv2

        ok, encoded = cv2.imencode(".png", variant)
        if not ok:
            return None
        tmp_path = ""
        try:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                tmp.write(encoded.tobytes())
                tmp.flush()
                tmp_path = tmp.name
            return engine.predict(tmp_path)
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
    return None


@dataclass
class PaddleScanImageResult:
    view: str
    house_number: str | None
    confidence: float
    detections: int = 0


@dataclass
class PaddleScanAccumulator:
    """Collects scan results across every Street View capture for one address."""

    expected: str = ""
    per_image: list[PaddleScanImageResult] = field(default_factory=list)
    best_number: str | None = None
    best_confidence: float = 0.0
    best_view: str | None = None

    def scan_streetview_image(
        self,
        view: str,
        image_bytes: bytes,
        *,
        fast: bool = False,
        azure_vision: dict | None = None,
        gpt_result: dict | None = None,
        run_paddle_focus: bool = True,
    ) -> PaddleScanImageResult:
        if run_paddle_focus:
            number, conf, det_count = scan_focused_local_zoom(
                image_bytes,
                expected=self.expected,
                azure_vision=azure_vision,
                gpt_result=gpt_result,
                fast=fast,
            )
        else:
            number, conf, det_count = None, 0.0, 0
        engine = "paddle_focus"
        if not number and azure_vision:
            number, conf, det_count = scan_house_number_from_azure_vision(
                azure_vision, expected=self.expected
            )
            engine = "azure"
        result = PaddleScanImageResult(
            view=view if engine == "paddle_focus" else f"{view}_azure",
            house_number=number,
            confidence=conf,
            detections=det_count,
        )
        self.per_image.append(result)
        expected_digits = re.sub(r"\D", "", self.expected or "")
        if number and valid_house_number(number):
            if expected_digits:
                if number == expected_digits and conf >= self.best_confidence:
                    self.best_number = number
                    self.best_confidence = conf
                    self.best_view = result.view
            elif conf >= self.best_confidence:
                self.best_number = number
                self.best_confidence = conf
                self.best_view = result.view
        return result

    def matches_expected(self) -> bool:
        expected_digits = re.sub(r"\D", "", self.expected or "")
        if not expected_digits or not self.best_number:
            return False
        return self.best_number == expected_digits

    def record_confirmed_read(
        self,
        number: str,
        confidence: float,
        *,
        view: str = "",
        scale: float = 5.0,
        min_confidence: float = 0.0,
    ) -> None:
        """Fill the scan column from any engine that confirmed the expected number."""
        text = re.sub(r"\D", "", number or "")
        expected_digits = re.sub(r"\D", "", self.expected or "")
        if not text or text != expected_digits or not valid_house_number(text):
            return
        raw_conf = float(confidence or 0)
        if raw_conf < min_confidence:
            return
        scaled = round(raw_conf * scale, 4) if scale else round(raw_conf, 4)
        if scaled >= self.best_confidence:
            self.best_number = text
            self.best_confidence = scaled
            self.best_view = view or self.best_view or "confirmed"

    def record_gpt_confirmed_read(self, gpt_result: dict | None) -> None:
        """Fill the scan column when GPT confirmed the expected number but OCR did not."""
        if not gpt_result or not gpt_result.get("house_number_visible"):
            return
        self.record_confirmed_read(
            gpt_result.get("house_number_text", "") or "",
            float(gpt_result.get("confidence", 0) or 0),
            view="gpt_confirmed",
            scale=5.0,
            min_confidence=0.70,
        )

    def summary(self) -> dict[str, Any]:
        matched = self.matches_expected()
        return {
            "paddleocr_scan_recognized": (self.best_number or "") if matched else "",
            "paddleocr_scan_confidence": round(self.best_confidence, 2) if matched else 0.0,
            "paddleocr_scan_matched": matched,
            "paddleocr_scan_view": (self.best_view or "") if matched else "",
            "paddleocr_scan_images": len(self.per_image),
        }


def scan_streetview_house_number(
    image_bytes: bytes,
    *,
    fast: bool = False,
    expected: str = "",
    azure_vision: dict | None = None,
    gpt_result: dict | None = None,
) -> tuple[str | None, float, int]:
    """
    Focused PaddleOCR on the house-number region with local digital zoom.
    """
    return scan_focused_local_zoom(
        image_bytes,
        expected=expected,
        azure_vision=azure_vision,
        gpt_result=gpt_result,
        fast=fast,
    )


def scan_house_number_from_azure_vision(
    vision: dict | None,
    *,
    expected: str = "",
) -> tuple[str | None, float, int]:
    """
    Extract house-number candidates from Azure Vision readResult when PaddleOCR
    is unavailable or returned nothing.
    """
    if not vision:
        return None, 0.0, 0

    detections: list[tuple[str, float]] = []
    for block in vision.get("readResult", {}).get("blocks", []):
        for line in block.get("lines", []):
            words = line.get("words") or []
            confidences = [
                float(word.get("confidence", 0.0))
                for word in words
                if word.get("confidence") is not None
            ]
            line_conf = sum(confidences) / len(confidences) if confidences else 0.55
            raw_text = (line.get("text") or "").strip()
            if not raw_text:
                continue
            normalized = clean_ocr_text(raw_text.upper())
            if normalized.lower() in {"GOOGLE", "@ GOOGLE"}:
                continue
            for token in HOUSE_REGEX.findall(normalized):
                digits = re.sub(r"\D", "", token)
                if valid_house_number(digits):
                    detections.append((digits, line_conf))
            digits = re.sub(r"\D", "", normalized)
            if valid_house_number(digits):
                detections.append((digits, line_conf))

    number, conf = choose_best_house_number(detections, expected=expected)
    if number:
        logger.debug(
            "PaddleOCR scan Azure fallback: number=%r conf=%.3f detections=%d expected=%r",
            number,
            conf,
            len(detections),
            expected,
        )
    return number, conf, len(detections)


def scan_matches_expected(
    recognized: str | None,
    confidence: float,
    expected: str,
    *,
    min_confidence: float = MATCH_CONF_FLOOR,
) -> bool:
    expected_digits = re.sub(r"\D", "", expected or "")
    if not expected_digits or not recognized or confidence < min_confidence:
        return False
    if is_noise_house_number(recognized):
        return False
    return recognized == expected_digits


def scan_needs_zoom_refinement(
    recognized: str | None,
    confidence: float,
    expected: str,
) -> bool:
    """True when PaddleOCR has not yet read the expected house number."""
    expected_digits = re.sub(r"\D", "", expected or "")
    if not expected_digits:
        return False
    if scan_matches_expected(recognized, confidence, expected):
        return False
    return True

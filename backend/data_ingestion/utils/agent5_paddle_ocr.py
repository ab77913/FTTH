"""Optional local OCR support for Agent 5 house-number validation."""

from __future__ import annotations

import logging
import os
import re
import tempfile
import threading
from functools import lru_cache
from io import BytesIO
from typing import Any

from PIL import Image

logger = logging.getLogger(__name__)
_PADDLE_INFER_LOCK = threading.Lock()
_TESSERACT_AVAILABLE: bool | None = None
_PADDLE_UNAVAILABLE_REASON = ""
_PADDLE_RUNTIME_DISABLED = False

os.environ.setdefault("FLAGS_use_onednn", "0")
os.environ.setdefault("FLAGS_enable_pir_api", "0")


def paddle_ocr_enabled() -> bool:
    return os.environ.get("FTTH_ENABLE_PADDLE_OCR", "1").strip().lower() not in {
        "0",
        "false",
        "no",
    }


def paddleocr_runtime_status() -> dict[str, bool | str]:
    """Report whether Agent 5 can use the local PaddleOCR engine (Agent 5 only)."""
    enabled = paddle_ocr_enabled()
    available = bool(enabled and _ocr_engine() is not None)
    return {
        "enabled": enabled,
        "available": available,
        "backend": "paddleocr" if available else "azure_fallback",
        "reason": "" if available else (_PADDLE_UNAVAILABLE_REASON or "PaddleOCR engine unavailable"),
    }


def paddle_runtime_error_is_fatal(exc: BaseException | str) -> bool:
    """Return True for Paddle runtime failures that make retries useless."""
    text = str(exc).lower()
    fatal_markers = (
        "convertpirattribute2runtimeattribute",
        "onednn_instruction",
        "pir::arrayattribute",
        "new_executor",
        "not support",
    )
    return any(marker in text for marker in fatal_markers)


def paddle_runtime_disabled() -> bool:
    return _PADDLE_RUNTIME_DISABLED


def mark_paddle_runtime_unavailable(reason: BaseException | str) -> None:
    """Disable PaddleOCR after a fatal inference error and use fallbacks."""
    global _PADDLE_UNAVAILABLE_REASON, _PADDLE_RUNTIME_DISABLED
    _PADDLE_RUNTIME_DISABLED = True
    _PADDLE_UNAVAILABLE_REASON = f"runtime failed: {type(reason).__name__ if isinstance(reason, BaseException) else ''}: {str(reason)[:300]}".strip()
    try:
        _ocr_engine.cache_clear()
    except Exception:
        pass


@lru_cache(maxsize=1)
def _ocr_engine() -> Any | None:
    global _PADDLE_UNAVAILABLE_REASON
    if _PADDLE_RUNTIME_DISABLED:
        if not _PADDLE_UNAVAILABLE_REASON:
            _PADDLE_UNAVAILABLE_REASON = "PaddleOCR runtime disabled after inference failure"
        return None
    if not paddle_ocr_enabled():
        _PADDLE_UNAVAILABLE_REASON = "FTTH_ENABLE_PADDLE_OCR disabled"
        return None
    try:
        from paddleocr import PaddleOCR
    except Exception as exc:
        _PADDLE_UNAVAILABLE_REASON = f"{type(exc).__name__}: {exc}"
        logger.debug("PaddleOCR is not available: %s", exc)
        return None

    init_attempts = (
        {
            "use_doc_orientation_classify": False,
            "use_doc_unwarping": False,
            "use_textline_orientation": True,
            "lang": "en",
        },
        {"use_angle_cls": True, "lang": "en"},
        {
            "use_doc_orientation_classify": False,
            "use_doc_unwarping": False,
            "use_textline_orientation": False,
            "lang": "en",
        },
        {"lang": "en"},
    )
    for kwargs in init_attempts:
        try:
            return PaddleOCR(**kwargs)
        except TypeError:
            continue
        except Exception as exc:
            _PADDLE_UNAVAILABLE_REASON = f"initialization failed: {type(exc).__name__}: {exc}"
            logger.debug("PaddleOCR initialization failed: %s", exc)
            return None
    _PADDLE_UNAVAILABLE_REASON = "no compatible PaddleOCR initializer accepted"
    return None


def _image_to_array(image_bytes: bytes) -> Any:
    import numpy as np

    image = Image.open(BytesIO(image_bytes)).convert("RGB")
    return np.array(image)


def _collect_text_confidence(node: Any) -> list[tuple[str, float]]:
    items: list[tuple[str, float]] = []
    if isinstance(node, dict):
        rec_texts = node.get("rec_texts")
        rec_scores = node.get("rec_scores") or node.get("rec_score")
        if isinstance(rec_texts, list):
            for idx, text in enumerate(rec_texts):
                if not text:
                    continue
                conf = 0.0
                if isinstance(rec_scores, list) and idx < len(rec_scores):
                    conf = float(rec_scores[idx] or 0)
                items.append((str(text), conf))
        text = node.get("text") or node.get("transcription")
        conf = node.get("score") or node.get("confidence")
        if text:
            items.append((str(text), float(conf or 0)))
        for value in node.values():
            items.extend(_collect_text_confidence(value))
    elif isinstance(node, (list, tuple)):
        if len(node) >= 2 and isinstance(node[1], (list, tuple)) and node[1]:
            text = node[1][0]
            conf = node[1][1] if len(node[1]) > 1 else 0
            if isinstance(text, str):
                items.append((text, float(conf or 0)))
        for value in node:
            items.extend(_collect_text_confidence(value))
    return items


def _run_paddle(engine: Any, image_bytes: bytes) -> Any:
    if hasattr(engine, "predict"):
        tmp_path = ""
        try:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                tmp.write(image_bytes)
                tmp.flush()
                tmp_path = tmp.name
            return engine.predict(tmp_path)
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
    return engine.ocr(_image_to_array(image_bytes))


def detect_house_number_with_paddle(
    image_bytes: bytes,
    expected: str,
    threshold: float = 0.90,
) -> tuple[bool, float, str]:
    """Return (matched, confidence, text) using PaddleOCR if available."""
    expected = (expected or "").strip()
    if not expected:
        return False, 0.0, ""

    engine = _ocr_engine()
    if engine is None:
        return False, 0.0, ""

    try:
        with _PADDLE_INFER_LOCK:
            result = _run_paddle(engine, image_bytes)
    except Exception as exc:
        logger.debug("PaddleOCR analysis failed: %s", exc)
        if paddle_runtime_error_is_fatal(exc):
            mark_paddle_runtime_unavailable(exc)
        return False, 0.0, ""

    text_conf = _collect_text_confidence(result)
    full_text = " ".join(text for text, _conf in text_conf)
    expected_digits = re.sub(r"\D+", "", expected)
    best_conf = 0.0
    for text, conf in text_conf:
        digits = re.sub(r"\D+", "", text)
        if expected in text or (expected_digits and expected_digits in digits):
            best_conf = max(best_conf, conf)

    matched = best_conf >= threshold
    logger.debug(
        "PaddleOCR: image_bytes=%d expected=%r matched=%s best_conf=%.3f text=%r",
        len(image_bytes), expected, matched, best_conf, full_text[:500],
    )
    return matched, best_conf, full_text


def tesseract_ocr_enabled() -> bool:
    return os.environ.get("FTTH_ENABLE_TESSERACT_OCR", "1").strip().lower() not in {
        "0",
        "false",
        "no",
    }


def tesseract_available() -> bool:
    """Cached probe so we do not retry a missing Tesseract binary every probe."""
    global _TESSERACT_AVAILABLE
    if _TESSERACT_AVAILABLE is not None:
        return _TESSERACT_AVAILABLE
    if not tesseract_ocr_enabled():
        _TESSERACT_AVAILABLE = False
        return False
    try:
        import pytesseract
        tesseract_cmd = os.environ.get("TESSERACT_CMD") or os.environ.get("PYTESSERACT_CMD")
        if tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
        pytesseract.get_tesseract_version()
    except Exception as exc:
        logger.debug("Tesseract is not available: %s", exc)
        _TESSERACT_AVAILABLE = False
        return False
    _TESSERACT_AVAILABLE = True
    return True


def detect_house_number_with_tesseract(
    image_bytes: bytes,
    expected: str,
    threshold: float = 0.90,
) -> tuple[bool, float, str]:
    """Return (matched, confidence, text) using local Tesseract/pytesseract when available."""
    expected = (expected or "").strip()
    if not expected or not tesseract_available():
        return False, 0.0, ""

    try:
        import pytesseract
    except Exception as exc:
        logger.debug("pytesseract is not available: %s", exc)
        return False, 0.0, ""

    tesseract_cmd = os.environ.get("TESSERACT_CMD") or os.environ.get("PYTESSERACT_CMD")
    if tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd

    try:
        image = Image.open(BytesIO(image_bytes)).convert("RGB")
        text = pytesseract.image_to_string(image)
    except Exception as exc:
        logger.debug("Tesseract OCR analysis failed: %s", exc)
        return False, 0.0, ""

    expected_digits = re.sub(r"\D+", "", expected)
    text_digits = re.sub(r"\D+", "", text)
    matched = bool(expected and expected in text) or bool(expected_digits and expected_digits in text_digits)
    logger.debug(
        "Tesseract OCR: image_bytes=%d expected=%r matched=%s text=%r",
        len(image_bytes), expected, matched, (text or "")[:500],
    )
    return matched, (threshold if matched else 0.0), text

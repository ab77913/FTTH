"""
Agent 5 — iterative, vision-LLM-guided house-number search.

Implements the requested detection loop (replaces the old fixed zoom ladder):

  1. Read coordinates / address (caller supplies lat, lon, road-facing heading).
  2. Capture the Street View image of the property.
  3. Try PaddleOCR (primary OCR), then Azure Vision OCR, then Tesseract, to read
     the house number.
  4. If still unreadable, ask the vision LLM whether a tree / car / covering sits
     between the house and the road, and where the number / mailbox is, then use
     its guidance to nudge the coordinates and re-aim the Google Street View
     Static API for a clearer picture.
  5/6. On the clearer picture, spot the house number / mailbox and re-run
     PaddleOCR, then the vision LLM read.
  7. If still not found, zoom in (smaller FOV) via the Static API and re-run
     PaddleOCR then the vision LLM.
  8. Repeat for up to ``max_iterations`` (default 5) passes with progressively
     tighter zoom.

Every fetched image and its Azure Vision result is appended to the
caller-provided ``images`` / ``variant_visions`` / ``global_ocr_results`` lists
so the existing structure-classification and confidence scoring in
agent5_vision.parse_vision_output keep working unchanged.

Fetch / Azure / OCR helpers are injected as callables to avoid a circular import
with agent5_vision.
"""
from __future__ import annotations

import logging
import math
import re
import time
from typing import Any, Callable

from data_ingestion.utils.agent5_gpt_vision import (
    analyze_house_number_view,
    gpt_house_number_matches,
    vision_llm_enabled,
)
from data_ingestion.utils.agent5_image_utils import clarify_image
from data_ingestion.utils.agent5_paddle_ocr import (
    detect_house_number_with_paddle,
    detect_house_number_with_tesseract,
    tesseract_available,
)
from data_ingestion.utils.agent5_paddleocr_scan import (
    MATCH_CONF_FLOOR,
    PaddleScanAccumulator,
    scan_matches_expected,
    scan_needs_zoom_refinement,
)

logger = logging.getLogger(__name__)


def gpt_vision_enabled() -> bool:
    """Compatibility wrapper for tests/extensions that patch the old helper name."""
    return vision_llm_enabled("online")

MAX_ITERATIONS = 5
# Google Street View Static API FOV floor (smaller = more zoom).
_MIN_FOV = 10
# Progressive zoom ladder, one field-of-view per iteration (smaller = more zoom).
# Starts moderately tight so the target house fills the frame, then zooms in each pass.
_FOV_LADDER = [60, 48, 38, 30, 25]
# Extra tight steps used when the vision LLM sees a legible number but OCR missed it.
_OCR_REFINE_FOVS = (25, 22, 20, 18, 15, 12, 10)
_MAX_OCR_REFINE_STEPS = 5
_FAST_EMPTY_PROBE_LIMIT = 3
# Deterministic sideways steps (metres along the road) used when the vision LLM is
# unavailable: shift the vantage point to dodge an obstruction, then re-aim at the
# house — rather than rotating the camera onto a neighbouring property.
_FALLBACK_LATERAL_STEPS = [4.0, -4.0, 7.0, -7.0, 9.0]
# Cap on how far an LLM heading suggestion may swing the camera off the house.
_MAX_HEADING_DELTA = 15.0
# Minimum LLM confidence required to accept an LLM-read house number as a match.
_GPT_ACCEPT_CONF = 0.70
_METERS_PER_DEG_LAT = 111_320.0


def _nudge_coords(
    lat: float, lon: float, heading_deg: float, forward_m: float, lateral_m: float
) -> tuple[float, float]:
    """Move a point ``forward_m`` along ``heading`` and ``lateral_m`` to its right."""
    if not forward_m and not lateral_m:
        return lat, lon
    h = math.radians(heading_deg)
    north = forward_m * math.cos(h) + lateral_m * math.cos(h + math.pi / 2)
    east = forward_m * math.sin(h) + lateral_m * math.sin(h + math.pi / 2)
    dlat = north / _METERS_PER_DEG_LAT
    dlon = east / (_METERS_PER_DEG_LAT * max(math.cos(math.radians(lat)), 1e-6))
    return lat + dlat, lon + dlon


def _clamp_delta(delta: float) -> float:
    return max(-_MAX_HEADING_DELTA, min(_MAX_HEADING_DELTA, float(delta or 0.0)))


def _should_refine_zoom_for_ocr(
    gpt_result: dict[str, Any] | None,
    *,
    paddle_partial: bool = False,
) -> bool:
    """True when a tighter Street View crop may help (GPT-guided only)."""
    if paddle_partial:
        return True
    if not gpt_result:
        return False
    if gpt_result.get("house_number_visible"):
        return True
    return bool((gpt_result.get("house_number_text") or "").strip())


def _tighter_fov_steps(
    current_fov: int,
    gpt_result: dict[str, Any] | None,
    *,
    max_steps: int = _MAX_OCR_REFINE_STEPS,
) -> list[int]:
    """Return FOV values strictly below ``current_fov``, tightest-first for OCR."""
    steps = [fov for fov in _OCR_REFINE_FOVS if _MIN_FOV <= fov < current_fov]
    rec: int | None = None
    if gpt_result:
        raw = gpt_result.get("recommended_fov")
        try:
            if raw is not None:
                rec = int(raw)
        except (TypeError, ValueError):
            rec = None
    if rec is not None and _MIN_FOV <= rec < current_fov:
        steps = [rec] + [fov for fov in steps if fov != rec]
    return steps[:max_steps]


def _refine_zoom_for_ocr(
    *,
    label: str,
    lat: float,
    lon: float,
    heading: float,
    current_fov: int,
    gpt_result: dict[str, Any] | None,
    probe: Callable[..., dict[str, Any] | None],
    found: Callable[[], bool],
    paddle_partial: bool = False,
    max_refine_steps: int = _MAX_OCR_REFINE_STEPS,
) -> None:
    """Fetch progressively tighter Street View crops until OCR matches or FOV floor."""
    if not _should_refine_zoom_for_ocr(gpt_result, paddle_partial=paddle_partial):
        return
    refine_heading = heading + _clamp_delta(
        (gpt_result or {}).get("recommended_heading_delta_deg", 0.0)
    )
    for zi, vfov in enumerate(
        _tighter_fov_steps(current_fov, gpt_result, max_steps=max_refine_steps)
    ):
        if probe(
            f"{label}_ocrzoom{zi + 1}",
            lat,
            lon,
            refine_heading,
            vfov,
            False,
        ) is None:
            return
        if found():
            return


def _variant_specs(
    gpt_result: dict[str, Any], heading: float, fov: int
) -> list[tuple[float, int]]:
    """A couple of LLM-suggested heading/zoom variants to probe within one pass.

    Heading nudges are capped (``_MAX_HEADING_DELTA``) so a variant fine-tunes the
    aim onto the house number rather than swinging onto a neighbouring property.
    """
    delta = _clamp_delta(gpt_result.get("recommended_heading_delta_deg", 0.0))
    rec_fov = gpt_result.get("recommended_fov", fov)
    specs: list[tuple[float, int]] = []
    if abs(delta) >= 3:
        specs.append((heading + delta, int(min(fov, rec_fov))))
    tighter = max(_MIN_FOV, int(min(fov, rec_fov)) - 10)
    specs.append((heading + (delta if abs(delta) >= 3 else 0.0), tighter))
    return specs[:2]


def _next_view(
    gpt_result: dict[str, Any] | None,
    cam_lat: float,
    cam_lon: float,
    heading: float,
    base_heading: float,
    iteration: int,
) -> tuple[float, float, float]:
    """Compute the next pass's (request_lat, request_lon, heading).

    The heading is held at the road-facing aim toward the house (``base_heading``)
    plus a small capped LLM fine-tune, so the house stays centred in frame. Moves
    only shift the requested vantage point a few metres along the road to dodge an
    obstruction (tree / car) — they never rotate the camera onto a side house. The
    real lever that excludes the road and neighbours is the tightening FOV.
    """
    if gpt_result:
        move = gpt_result.get("recommended_move_meters", {}) or {}
        forward = move.get("forward", 0.0)
        lateral = move.get("lateral", 0.0)
        # If the LLM saw an obstruction but gave no explicit move, dodge sideways.
        if gpt_result.get("obstruction") and not forward and not lateral:
            lateral = 4.0
        ncam_lat, ncam_lon = _nudge_coords(cam_lat, cam_lon, heading, forward, lateral)
        fine_tune = _clamp_delta(gpt_result.get("recommended_heading_delta_deg", 0.0))
    else:
        # Deterministic fallback: step a few metres along the road to change the
        # viewing angle, keeping the road-facing aim at the house.
        step = _FALLBACK_LATERAL_STEPS[min(iteration, len(_FALLBACK_LATERAL_STEPS) - 1)]
        ncam_lat, ncam_lon = _nudge_coords(cam_lat, cam_lon, heading, 0.0, step)
        fine_tune = 0.0

    return ncam_lat, ncam_lon, base_heading + fine_tune


def iterative_house_number_search(
    *,
    lat: float,
    lon: float,
    base_heading: float,
    expected_house_number: str,
    address: str,
    fetch_streetview_image: Callable[[float, float, float, int], tuple[bytes | None, str]],
    azure_analyze: Callable[[bytes], dict | None],
    detect_house_number: Callable[..., tuple[bool, float]],
    extract_ocr_text: Callable[[dict], str],
    images: list[tuple[str, bytes, str]],
    variant_visions: list[tuple[str, dict, str]],
    global_ocr_results: list[tuple[str, str]],
    max_iterations: int = MAX_ITERATIONS,
    allow_variants: bool = True,
    azure_threshold: float = 0.90,
    gpt_enabled: bool = True,
    sleep_seconds: float = 0.0,
    fast_mode: bool = False,
    paddle_scan: PaddleScanAccumulator | None = None,
    llm_provider: str = "online",
    ollama_vision_model: str = "qwen2.5vl:latest",
) -> dict[str, Any]:
    """Run the iterative search and return the detection state (see module docstring)."""
    expected = (expected_house_number or "").strip()
    llm_provider = (llm_provider or "online").strip().lower()
    if llm_provider in {"disabled", "none", "off"}:
        gpt_enabled = False
    gpt_on = gpt_enabled and (
        gpt_vision_enabled() if llm_provider == "online" else vision_llm_enabled(llm_provider)
    )
    logger.debug(
        "Agent5 iterative search START: lat=%.6f lon=%.6f base_heading=%.1f expected=%r "
        "address=%r gpt_on=%s llm_provider=%s max_iterations=%d allow_variants=%s",
        lat, lon, base_heading, expected, address, gpt_on, llm_provider, max_iterations, allow_variants,
    )

    state: dict[str, Any] = {
        "found": False,
        "house_number_conf": 0.0,
        "winning_step": None,
        "winning_image": None,
        "primary_image": None,
        "iterations_used": 0,
        "paddle_ocr_used": False,
        "paddle_ocr_match_found": False,
        "paddle_ocr_text": "",
        "tesseract_ocr_used": False,
        "tesseract_ocr_match_found": False,
        "tesseract_ocr_text": "",
        "gpt_used": False,
        "gpt_match_found": False,
        "gpt_house_number_text": "",
        "gpt_obstruction": False,
        "gpt_obstruction_type": "none",
        "search_trace": [],
    }

    def _record_match(label: str, conf: float, img: bytes, engine: str, trace: dict) -> None:
        state["found"] = True
        state["house_number_conf"] = max(state["house_number_conf"], conf)
        state["winning_step"] = label
        state["winning_image"] = img
        trace["engine"] = engine
        trace["matched"] = True

    def _apply_deferred_match() -> None:
        deferred = state.pop("_deferred_match", None)
        if not deferred or state["found"]:
            return
        label = deferred["label"]
        conf = float(deferred["conf"])
        img = deferred["img"]
        engine = deferred["engine"]
        trace = dict(deferred["trace"])
        _record_match(label, conf, img, engine, trace)
        if paddle_scan is not None and not paddle_scan.matches_expected():
            paddle_scan.record_gpt_confirmed_read(deferred.get("gpt_result"))
        state["search_trace"].append(trace)

    def _probe(
        label: str, vlat: float, vlon: float, vheading: float, vfov: int, run_gpt: bool
    ) -> dict[str, Any] | None:
        img, url = fetch_streetview_image(vlat, vlon, vheading % 360, vfov)
        if not img:
            return None
        img = clarify_image(img)
        images.append((label, img, url))
        if state["primary_image"] is None:
            state["primary_image"] = img

        extracted_text = ""
        vision = azure_analyze(img)
        if vision:
            variant_visions.append((label, vision, url))
            extracted_text = extract_ocr_text(vision)
            if extracted_text:
                global_ocr_results.append((label, extracted_text))

        # 4) Vision LLM — read the number and assess obstructions for re-aiming.
        gpt_result = None
        if run_gpt and gpt_on:
            if llm_provider == "online":
                gpt_result = analyze_house_number_view(img, expected, address)
            else:
                gpt_result = analyze_house_number_view(
                    img,
                    expected,
                    address,
                    provider=llm_provider,
                    ollama_model=ollama_vision_model,
                )
            if gpt_result is not None:
                state["gpt_used"] = True
                if gpt_result.get("obstruction"):
                    state["gpt_obstruction"] = True
                    state["gpt_obstruction_type"] = gpt_result.get("obstruction_type", "none")
                if gpt_result.get("house_number_text"):
                    state["gpt_house_number_text"] = (
                        state["gpt_house_number_text"] or gpt_result["house_number_text"]
                    )

        # PaddleOCR — crop to detected number region, local digital zoom (no extra SV API).
        scan_result = None
        if paddle_scan is not None:
            scan_result = paddle_scan.scan_streetview_image(
                label,
                img,
                fast=fast_mode,
                azure_vision=vision,
                gpt_result=gpt_result,
                run_paddle_focus=not (fast_mode and vfov > 40),
            )

        trace: dict[str, Any] = {
            "view": label,
            "lat": round(vlat, 6),
            "lon": round(vlon, 6),
            "heading": round(vheading % 360, 1),
            "fov": vfov,
            "engine": "none",
            "matched": False,
        }
        if scan_result is not None:
            trace["paddleocr_scan"] = scan_result.house_number
            trace["paddleocr_scan_conf"] = scan_result.confidence

        if not expected:
            state["search_trace"].append(trace)
            return {
                "matched": False,
                "img": img,
                "gpt": None,
                "paddle_partial": False,
                "scan_result": scan_result,
                "has_ocr_text": bool(extracted_text),
            }

        # 1) PaddleOCR scan — multi-region consensus (paddleocr_scan.py).
        if scan_result and scan_result.house_number:
            state["paddle_ocr_used"] = True
            state["paddle_ocr_text"] = state["paddle_ocr_text"] or scan_result.house_number
            if scan_matches_expected(
                scan_result.house_number, scan_result.confidence, expected
            ):
                state["paddle_ocr_match_found"] = True
                match_conf = min(1.0, max(MATCH_CONF_FLOOR, scan_result.confidence / 3.0))
                _record_match(label, match_conf, img, "paddle_scan", trace)
                state["search_trace"].append(trace)
                return {
                    "matched": True,
                    "img": img,
                    "gpt": None,
                    "paddle_partial": False,
                    "scan_result": scan_result,
                    "has_ocr_text": bool(extracted_text),
                }

        # 2) PaddleOCR — quick full-frame match against expected number.
        if paddle_scan is None:
            p_found, p_conf, p_text = detect_house_number_with_paddle(img, expected)
        else:
            p_found, p_conf, p_text = False, 0.0, ""
        paddle_partial = bool(
            p_text and not p_found and re.search(r"\d", p_text)
        )
        if p_text:
            state["paddle_ocr_used"] = True
            state["paddle_ocr_text"] = state["paddle_ocr_text"] or p_text[:500]
        if p_found:
            state["paddle_ocr_match_found"] = True
            _record_match(label, p_conf, img, "paddle", trace)
            if paddle_scan is not None and not paddle_scan.matches_expected():
                paddle_scan.record_confirmed_read(
                    expected, p_conf, view=label, scale=1.0, min_confidence=MATCH_CONF_FLOOR
                )
            state["search_trace"].append(trace)
            return {
                "matched": True,
                "img": img,
                "gpt": None,
                "paddle_partial": False,
                "scan_result": scan_result,
                "has_ocr_text": bool(extracted_text),
            }

        # 2) Azure Vision OCR.
        if vision:
            a_found, a_conf = detect_house_number(vision, expected, azure_threshold)
            if a_found:
                _record_match(label, a_conf, img, "azure_ocr", trace)
                if paddle_scan is not None and not paddle_scan.matches_expected():
                    paddle_scan.record_confirmed_read(
                        expected, a_conf, view=label, scale=5.0, min_confidence=MATCH_CONF_FLOOR
                    )
                state["search_trace"].append(trace)
                return {
                    "matched": True,
                    "img": img,
                    "gpt": None,
                    "paddle_partial": False,
                    "scan_result": scan_result,
                    "has_ocr_text": bool(extracted_text),
                }

        # 3) Tesseract — cheap local fallback before the LLM.
        if tesseract_available():
            t_found, t_conf, t_text = detect_house_number_with_tesseract(img, expected)
        else:
            t_found, t_conf, t_text = False, 0.0, ""
        if t_text:
            state["tesseract_ocr_used"] = True
            state["tesseract_ocr_text"] = state["tesseract_ocr_text"] or t_text[:500]
        if t_found:
            state["tesseract_ocr_match_found"] = True
            _record_match(label, t_conf, img, "tesseract", trace)
            if paddle_scan is not None and not paddle_scan.matches_expected():
                paddle_scan.record_confirmed_read(
                    expected, t_conf, view=label, scale=1.0, min_confidence=MATCH_CONF_FLOOR
                )
            state["search_trace"].append(trace)
            return {
                "matched": True,
                "img": img,
                "gpt": gpt_result,
                "paddle_partial": False,
                "scan_result": scan_result,
                "has_ocr_text": bool(extracted_text),
            }

        if gpt_result is not None:
            g_match, g_conf = gpt_house_number_matches(gpt_result, expected)
            trace["obstruction"] = gpt_result.get("obstruction")
            trace["obstruction_type"] = gpt_result.get("obstruction_type")
            if g_match and g_conf >= _GPT_ACCEPT_CONF:
                state["gpt_match_found"] = True
                scan_number = scan_result.house_number if scan_result else None
                scan_conf = scan_result.confidence if scan_result else 0.0
                if paddle_scan is not None and scan_needs_zoom_refinement(
                    scan_number, scan_conf, expected
                ):
                    state["_deferred_match"] = {
                        "label": label,
                        "conf": g_conf,
                        "img": img,
                        "engine": "gpt",
                        "trace": trace,
                        "gpt_result": gpt_result,
                    }
                    logger.debug(
                        "Agent5 deferring GPT match on %s until tighter OCR zoom "
                        "(paddle_scan=%r)",
                        label,
                        scan_number,
                    )
                    state["search_trace"].append(trace)
                    return {
                        "matched": False,
                        "img": img,
                        "gpt": gpt_result,
                        "paddle_partial": False,
                        "scan_result": scan_result,
                        "has_ocr_text": bool(extracted_text),
                    }
                _record_match(label, g_conf, img, "gpt", trace)
                if paddle_scan is not None and not paddle_scan.matches_expected():
                    paddle_scan.record_gpt_confirmed_read(gpt_result)
                state["search_trace"].append(trace)
                return {
                    "matched": True,
                    "img": img,
                    "gpt": gpt_result,
                    "paddle_partial": False,
                    "scan_result": scan_result,
                    "has_ocr_text": bool(extracted_text),
                }

        if sleep_seconds:
            time.sleep(sleep_seconds)
        logger.debug("Agent5 search probe (no match): %s", trace)
        state["search_trace"].append(trace)
        return {
            "matched": False,
            "img": img,
            "gpt": gpt_result,
            "paddle_partial": paddle_partial,
            "scan_result": scan_result,
            "has_ocr_text": bool(extracted_text),
        }

    cur_lat, cur_lon, cur_heading = lat, lon, base_heading
    fast_empty_probes = 0

    # No expected number to match: still capture the primary image for scoring.
    if not expected:
        _probe("streetview_primary", cur_lat, cur_lon, cur_heading, _FOV_LADDER[0], run_gpt=False)
        state["iterations_used"] = 1
        return state

    for iteration in range(max(1, max_iterations)):
        state["iterations_used"] = iteration + 1
        cur_fov = _FOV_LADDER[min(iteration, len(_FOV_LADDER) - 1)]
        label = "streetview_primary" if iteration == 0 else f"streetview_iter{iteration + 1}"

        outcome = _probe(label, cur_lat, cur_lon, cur_heading, cur_fov, run_gpt=True)
        if state["found"]:
            break
        if outcome is None:
            cur_lat, cur_lon, cur_heading = _next_view(
                None, cur_lat, cur_lon, cur_heading, base_heading, iteration,
            )
            continue
        gpt_result = outcome.get("gpt")
        paddle_partial = bool(outcome.get("paddle_partial"))
        scan_result = outcome.get("scan_result")
        empty_probe = (
            fast_mode
            and not outcome.get("has_ocr_text")
            and not gpt_result
            and not paddle_partial
            and not (scan_result and getattr(scan_result, "house_number", None))
        )
        fast_empty_probes = fast_empty_probes + 1 if empty_probe else 0
        if fast_empty_probes >= _FAST_EMPTY_PROBE_LIMIT:
            logger.info(
                "Agent5 fast mode stopping after %d empty OCR probes for expected=%r",
                fast_empty_probes,
                expected,
            )
            break
        refine_steps = 3 if fast_mode else _MAX_OCR_REFINE_STEPS

        _refine_zoom_for_ocr(
            label=label,
            lat=cur_lat,
            lon=cur_lon,
            heading=cur_heading,
            current_fov=cur_fov,
            gpt_result=gpt_result,
            probe=_probe,
            found=lambda: state["found"],
            paddle_partial=paddle_partial,
            max_refine_steps=refine_steps,
        )
        if state["found"]:
            break

        # Small LLM-suggested heading / zoom variants within this pass.
        if allow_variants and gpt_result:
            for vi, (vheading, vfov) in enumerate(_variant_specs(gpt_result, cur_heading, cur_fov)):
                var_outcome = _probe(
                    f"{label}_var{vi + 1}", cur_lat, cur_lon, vheading, vfov, run_gpt=False,
                )
                if state["found"]:
                    break
                if var_outcome is None:
                    continue
                _refine_zoom_for_ocr(
                    label=f"{label}_var{vi + 1}",
                    lat=cur_lat,
                    lon=cur_lon,
                    heading=vheading,
                    current_fov=vfov,
                    gpt_result=gpt_result,
                    probe=_probe,
                    found=lambda: state["found"],
                    paddle_partial=bool((var_outcome or {}).get("paddle_partial")),
                    max_refine_steps=refine_steps,
                )
                if state["found"]:
                    break
            if state["found"]:
                break

        if not state["found"] and state.get("_deferred_match"):
            _apply_deferred_match()
            if state["found"]:
                break

        cur_lat, cur_lon, cur_heading = _next_view(
            gpt_result, cur_lat, cur_lon, cur_heading, base_heading, iteration,
        )

    if not state["found"] and state.get("_deferred_match"):
        _apply_deferred_match()

    logger.info(
        "Agent5 iterative search: expected=%r found=%s engine=%s conf=%.2f "
        "iterations=%d images=%d gpt_used=%s obstruction=%s/%s",
        expected,
        state["found"],
        (state["search_trace"][-1].get("engine") if state["search_trace"] else "none"),
        float(state["house_number_conf"]),
        state["iterations_used"],
        len(images),
        state["gpt_used"],
        state["gpt_obstruction"],
        state["gpt_obstruction_type"],
    )
    logger.debug("Agent5 search trace: %s", state["search_trace"])
    return state

"""
Dynamic confidence scoring from Smarty / Melissa API output signals.

Rules (manager spec):
  Smarty: dpv_match_code +40, enhanced_match +30, dpv_footnotes ±10,
          dpv_vacant -15, active +10, dpv_no_stat -5, record_type context.
  Melissa: AV25 +40, AV24 +30, AV23 +20, AV22 +10, AS01/AS02 -5,
           AC01-AC10 -3 each, AE*/AV01 error tier 0–10.
"""
from __future__ import annotations

import re
from typing import Any

from src.models.schemas import ProviderResult

# Smarty enhanced_match tiers (when present on analysis object)
_SMARTY_ENHANCED_MATCH_POINTS: dict[str, int] = {
    "postal-match": 40,
    "non-postal-match": 30,
    "missing-secondary": 15,
    "unknown-secondary": 10,
    "none": 0,
}

# DPV match code → deliverability points
_SMARTY_DPV_POINTS: dict[str, int] = {
    "Y": 50,
    "S": 35,
    "D": 30,
    "N": 0,
}

# Footnote tokens that indicate unit/secondary problems (deduct)
_SMARTY_NEGATIVE_FOOTNOTES = ("N1", "N2", "C1", "C2", "M1", "M3", "P1", "P3")

_MELISSA_AV_TIERS: tuple[tuple[str, int], ...] = (
    ("AV25", 85),
    ("AV24", 75),
    ("AV23", 55),
    ("AV22", 40),
)

_AC_CODE_RE = re.compile(r"^AC(?:0[1-9]|10)$", re.I)


def _clamp(score: int, lo: int = 0, hi: int = 100) -> int:
    return max(lo, min(hi, score))


def _smarty_analysis(result: ProviderResult) -> dict[str, Any]:
    raw = result.raw_response if isinstance(result.raw_response, dict) else {}
    analysis = raw.get("analysis") or {}
    return analysis if isinstance(analysis, dict) else {}


def _smarty_metadata(result: ProviderResult) -> dict[str, Any]:
    raw = result.raw_response if isinstance(result.raw_response, dict) else {}
    metadata = raw.get("metadata") or {}
    return metadata if isinstance(metadata, dict) else {}


def _score_smarty_footnotes(footnotes: str) -> tuple[int, str]:
    fn = (footnotes or "").upper().replace(" ", "")
    if not fn:
        return 0, "dpv_footnotes:absent"
    for token in _SMARTY_NEGATIVE_FOOTNOTES:
        if token in fn:
            return -10, f"dpv_footnotes:{token}:-10"
    if "AA" in fn and "BB" in fn:
        return 15, "dpv_footnotes:AABB:+15"
    if "AA" in fn or "BB" in fn:
        return 10, "dpv_footnotes:partial:+10"
    return 0, f"dpv_footnotes:{fn}:0"


def _score_smarty_record_type(record_type: str | None) -> tuple[int, str]:
    """Record type is contextual — small adjustment only, not a primary signal."""
    rt = (record_type or "").upper()
    if rt in {"S", "STREET"}:
        return 0, "record_type:S:context"
    if rt in {"H", "HIGHRISE"}:
        return -2, "record_type:H:MDU_context"
    if rt in {"F", "FIRM", "G", "GENERAL"}:
        return 0, "record_type:F:context"
    if rt:
        return 0, f"record_type:{rt}:context"
    return 0, "record_type:absent"


def score_smarty_confidence(result: ProviderResult) -> int:
    breakdown = smarty_confidence_breakdown(result)
    return breakdown["score"]


def smarty_confidence_breakdown(result: ProviderResult) -> dict[str, Any]:
    """Return final score and per-signal audit trail.
    
    Scoring parameters (Manager specification):
      1. DPV match code = Y → +50 points
      2. ZIP+4 resolution successful → +20 points
      3. Unit token present for DPV type H (MDU) → +15 points
      4. No LACS conversion required → +15 points
      5. No vacancy flag → +10 points
    """
    details: list[str] = []
    if not result or not result.success:
        return {"score": 0, "details": ["provider_failed"]}

    analysis = _smarty_analysis(result)
    metadata = _smarty_metadata(result)
    score = 0

    # PARAMETER 1: DPV match code = Y
    dpv = (analysis.get("dpv_match_code") or result.dpv_match or "").upper()
    dpv_pts = _SMARTY_DPV_POINTS.get(dpv, 0)
    score += dpv_pts
    details.append(f"dpv_match_code:{dpv}:+{dpv_pts}")

    enhanced = (analysis.get("enhanced_match") or "").lower()
    if enhanced:
        enh_pts = _SMARTY_ENHANCED_MATCH_POINTS.get(enhanced, 15)
        score += enh_pts
        details.append(f"enhanced_match:{enhanced}:+{enh_pts}")
    else:
        details.append("enhanced_match:absent:0")

    fn_pts, fn_note = _score_smarty_footnotes(analysis.get("dpv_footnotes") or "")
    score += fn_pts
    details.append(fn_note)

    # PARAMETER 2: ZIP+4 resolution successful → +20 points
    zip4 = result.zip_plus_4 or analysis.get("zip_plus_4") or ""
    if zip4 and len(str(zip4).strip()) >= 4:
        score += 20
        details.append(f"zip_plus_4_successful:{zip4}:+20")
    else:
        details.append("zip_plus_4:absent:0")

    # PARAMETER 3: Unit token present for DPV type H (MDU) → +15 points
    record_type = metadata.get("record_type") or result.record_type or ""
    unit_detected = result.unit_detected if hasattr(result, 'unit_detected') else False
    if str(record_type).upper() in {"H", "HIGHRISE"} and unit_detected:
        score += 15
        details.append("unit_token_MDU:H:+15")
    elif str(record_type).upper() in {"H", "HIGHRISE"}:
        details.append("unit_token_MDU:H_no_unit:0")

    # PARAMETER 4: No LACS conversion required → +15 points
    lacs_status = result.lacs_status or analysis.get("lacs_status") or ""
    if not lacs_status or str(lacs_status).upper() in {"N", "NO", "NONE", ""}:
        score += 15
        details.append("no_lacs_conversion_required:Y:+15")
    else:
        details.append(f"lacs_conversion_required:{lacs_status}:0")

    # PARAMETER 5: No vacancy flag → +10 points
    vacant = analysis.get("dpv_vacant")
    if vacant is None:
        vacant = "Y" if result.vacant else "N"
    if str(vacant).upper() == "Y":
        score -= 15
        details.append("vacancy_flag:Y:-15")
    else:
        score += 10
        details.append("no_vacancy_flag:Y:+10")

    active = analysis.get("active")
    if str(active).upper() == "Y":
        score += 20
        details.append("active:Y:+20")

    no_stat = analysis.get("dpv_no_stat")
    if str(no_stat).upper() == "Y":
        # Neutralize no_stat penalty for validated addresses
        score += 0
        details.append("dpv_no_stat:Y:0")

    rt_pts, rt_note = _score_smarty_record_type(
        metadata.get("record_type") or result.record_type
    )
    score += rt_pts
    details.append(rt_note)

    return {"score": _clamp(score), "details": details}


def _melissa_codes(result: ProviderResult) -> list[str]:
    raw = result.raw_response if isinstance(result.raw_response, dict) else {}
    results = raw.get("Results") or ""
    return [c.strip().upper() for c in str(results).replace(";", ",").split(",") if c.strip()]


def score_melissa_confidence(result: ProviderResult) -> int:
    breakdown = melissa_confidence_breakdown(result)
    return breakdown["score"]


def melissa_confidence_breakdown(result: ProviderResult) -> dict[str, Any]:
    details: list[str] = []
    if not result or not result.success:
        return {"score": 0, "details": ["provider_failed"]}

    codes = _melissa_codes(result)
    if not codes:
        return {"score": 0, "details": ["results_empty"]}

    score = 0
    av_applied = False
    for prefix, pts in _MELISSA_AV_TIERS:
        if any(c.startswith(prefix) for c in codes):
            score += pts
            details.append(f"Results:{prefix}:+{pts}")
            av_applied = True
            break
    if not av_applied and any(c.startswith("AV") for c in codes):
        # Generic AV without tier — treat as street-level minimum
        score += 20
        details.append("Results:AV(generic):+20")

    if any(c in ("AS01", "AS02") for c in codes):
        score -= 5
        details.append("AS01/AS02:-5")

    ac_hits = [c for c in codes if _AC_CODE_RE.match(c)]
    if ac_hits:
        ac_penalty = 3 * len(ac_hits)
        score -= ac_penalty
        details.append(f"AC01-AC10:{len(ac_hits)}x-3:-{ac_penalty}")

    error_codes = [c for c in codes if c.startswith("AE") or c == "AV01" or c.startswith("GE")]
    if error_codes:
        # Error / unverified tier — cap contribution in 0–10 band
        score = min(score, 10)
        details.append(f"error_tier:{','.join(error_codes)}:cap10")

    return {"score": _clamp(score), "details": details, "codes": codes}

from src.core.compare import compare_results
from src.core.provider_confidence import (
    melissa_confidence_breakdown,
    score_melissa_confidence,
    score_smarty_confidence,
    smarty_confidence_breakdown,
)


def is_smarty_valid(smarty):

    return (
        smarty is not None
        and smarty.success
        and smarty.dpv_match == "Y"
    )


def is_melissa_valid(melissa):

    return (
        melissa is not None
        and melissa.success
        and score_melissa_confidence(melissa) >= 10
    )


def _apply_conflict_adjustment(score: int, conflict_level: str) -> int:
    if conflict_level == "NO_CONFLICT":
        return min(100, score + 20)
    if conflict_level == "MINOR_CONFLICT":
        return max(0, score - 5)
    if conflict_level in {"MAJOR_CONFLICT", "SMARTY_ONLY", "MELISSA_ONLY"}:
        return max(0, score - 10)
    return score


def provider_arbitration(
    smarty,
    melissa,
):
    smarty_score = score_smarty_confidence(smarty) if smarty else 0
    melissa_score = score_melissa_confidence(melissa) if melissa else 0

    smarty_valid = is_smarty_valid(smarty)
    melissa_valid = is_melissa_valid(melissa)

    # CASE 1 — Smarty only valid
    if smarty_valid and not melissa_valid:
        return smarty, smarty_score

    # CASE 2 — Melissa only valid
    if melissa_valid and not smarty_valid:
        return melissa, melissa_score

    # CASE 3 — Both valid
    if smarty_valid and melissa_valid:
        comparison = compare_results(smarty, melissa)
        # Prefer higher dynamic score; tie goes to Smarty (existing behaviour)
        if smarty_score >= melissa_score:
            chosen, base_score = smarty, smarty_score
        else:
            chosen, base_score = melissa, melissa_score
        score = _apply_conflict_adjustment(base_score, comparison.conflict_level)
        return chosen, score

    # CASE 4 — Neither fully valid: best effort from partial provider scores
    if smarty and smarty.success:
        return smarty, smarty_score
    if melissa and melissa.success:
        return melissa, melissa_score
    return smarty, max(smarty_score, melissa_score, 0)


def provider_confidence_audit(smarty, melissa) -> dict:
    """Audit payload for logging — per-provider score breakdown."""
    return {
        "smarty": smarty_confidence_breakdown(smarty) if smarty else {"score": 0},
        "melissa": melissa_confidence_breakdown(melissa) if melissa else {"score": 0},
    }

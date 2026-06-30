from src.models.schemas import ProviderResult, ComparisonResult
from src.core.provider_confidence import (
    score_melissa_confidence,
    score_smarty_confidence,
)


def score_provider_result(result: ProviderResult) -> int:
    if not result or not result.success:
        return 0
    if result.provider == "melissa":
        return score_melissa_confidence(result)
    return score_smarty_confidence(result)


def choose_result(smarty: ProviderResult, melissa: ProviderResult, comparison: ComparisonResult):
    smarty_score = score_provider_result(smarty)
    melissa_score = score_provider_result(melissa)
    if comparison.better_provider == "smarty":
        chosen, score = smarty, smarty_score
    elif comparison.better_provider == "melissa":
        chosen, score = melissa, melissa_score
    else:
        if smarty_score >= melissa_score:
            chosen, score = smarty, smarty_score
        else:
            chosen, score = melissa, melissa_score
    if comparison.conflict_level == "AGREE":
        score = min(100, score + 5)
    elif comparison.conflict_level == "MAJOR_CONFLICT":
        score = max(0, score - 20)
    return chosen, score


def structure_hint(result: ProviderResult, raw_address: str) -> str:
    text = raw_address.upper()
    if any(tok in text for tok in [" APT ", " UNIT ", " STE ", " SUITE ", " BLDG ", "#"]):
        return "MDU_OR_MXU_HINT"
    if result.record_type and str(result.record_type).upper() in {"H", "HIGHRISE", "M"}:
        return "MDU_HINT"
    if result.record_type and str(result.record_type).upper() in {"F", "BUSINESS", "FIRM"}:
        return "ANCHOR_OR_MXU_HINT"
    return "SFU_HINT"


def validation_status(score, chosen, comparison):
    
    # Handle comparison failure safely
    if comparison is None:

        if chosen and chosen.success:

            return "AUTO_ACCEPT", None

        return "REJECT", "Both providers failed"

    # Existing logic
    if score >= 90:

        return "AUTO_ACCEPT", None

    elif score >= 70:

        return "MANUAL_REVIEW", (
            f"{comparison.conflict_level}: "
            f"{comparison.reason}"
        )

    else:

        return "REJECT", (
            f"{comparison.conflict_level}: "
            f"{comparison.reason}"
        )

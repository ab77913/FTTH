from src.models.schemas import ComparisonResult


def compare_results(smarty, melissa):

    # Smarty only success
    if smarty.success and not melissa.success:

        return ComparisonResult(
            match=True,
            conflict_level="SMARTY_ONLY",
            reason="Melissa validation failed"
        )

    # Melissa only success
    if melissa.success and not smarty.success:

        return ComparisonResult(
            match=True,
            conflict_level="MELISSA_ONLY",
            reason="Smarty validation failed"
        )

    # Both failed
    if not smarty.success and not melissa.success:

        return ComparisonResult(
            match=False,
            conflict_level="MAJOR_CONFLICT",
            reason="Both providers failed"
        )

    score = 0

    # ZIP+4 comparison
    if (
        smarty.zip_plus_4
        and melissa.zip_plus_4
    ):

        if smarty.zip_plus_4 == melissa.zip_plus_4:
            score += 25

    # Standardized address comparison
    if (
        smarty.standardized_address
        and melissa.standardized_address
    ):

        if (
            smarty.standardized_address.lower()
            ==
            melissa.standardized_address.lower()
        ):
            score += 25

    # Latitude comparison
    if (
        smarty.latitude
        and melissa.latitude
    ):

        if round(smarty.latitude, 3) == round(melissa.latitude, 3):
            score += 25

    # DPV comparison
    if (
        smarty.dpv_match == "Y"
        and melissa.dpv_match == "Y"
    ):

        score += 25

    # Conflict logic
    if score >= 75:

        conflict = "NO_CONFLICT"

    elif score >= 40:

        conflict = "MINOR_CONFLICT"

    else:

        conflict = "MAJOR_CONFLICT"

    return ComparisonResult(
        match=score >= 40,
        conflict_level=conflict,
        reason=f"Comparison score = {score}"
    )
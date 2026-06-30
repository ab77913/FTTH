"""Unit tests for dynamic Smarty / Melissa confidence scoring."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_AGENT_SRC = (
    Path(__file__).resolve().parents[1]
    / "reference"
    / "ftth_address_validation_agent_1"
    / "ftth_address_validation_agent_1"
)
if str(_AGENT_SRC) not in sys.path:
    sys.path.insert(0, str(_AGENT_SRC))

from src.models.schemas import ProviderResult  # noqa: E402
from src.core.provider_confidence import (  # noqa: E402
    melissa_confidence_breakdown,
    smarty_confidence_breakdown,
)
from src.core.provider_arbitration import provider_arbitration  # noqa: E402


_SMARTY_SAMPLE = {
    "analysis": {
        "dpv_match_code": "Y",
        "dpv_footnotes": "AABB",
        "dpv_vacant": "N",
        "dpv_no_stat": "Y",
        "active": "Y",
    },
    "metadata": {"record_type": "S"},
}


class TestSmartyConfidence:
    def test_sample_from_production_log(self):
        result = ProviderResult(
            provider="smarty",
            success=True,
            dpv_match="Y",
            vacant=False,
            record_type="S",
            zip_plus_4="12345",
            raw_response=_SMARTY_SAMPLE,
        )
        breakdown = smarty_confidence_breakdown(result)
        # Y(+50) + AABB(+15) + ZIP+4(+20) + no_lacs(+15) + no_vacancy(+10) + active(+20) = 130 → clamped to 100
        assert breakdown["score"] == 100

    def test_vacant_penalty(self):
        raw = {
            "analysis": {
                "dpv_match_code": "Y",
                "dpv_footnotes": "AABB",
                "dpv_vacant": "Y",
                "active": "Y",
            },
            "metadata": {"record_type": "S"},
        }
        result = ProviderResult(
            provider="smarty", success=True, dpv_match="Y", vacant=True, zip_plus_4="12345", raw_response=raw
        )
        assert smarty_confidence_breakdown(result)["score"] == 100  # 50+15+20+15-15+20 = 105 → clamped to 100


class TestMelissaConfidence:
    def test_av24_premise(self):
        result = ProviderResult(
            provider="melissa",
            success=True,
            raw_response={"Results": "AV24"},
        )
        assert melissa_confidence_breakdown(result)["score"] == 75

    def test_av25_highest_tier(self):
        result = ProviderResult(
            provider="melissa",
            success=True,
            raw_response={"Results": "AV25,AS01,AC01"},
        )
        # 85 - 5 - 3 = 77
        assert melissa_confidence_breakdown(result)["score"] == 77

    def test_error_tier_capped(self):
        result = ProviderResult(
            provider="melissa",
            success=True,
            raw_response={"Results": "AE10,GE08"},
        )
        assert melissa_confidence_breakdown(result)["score"] <= 10


class TestProviderArbitrationDynamic:
    def test_smarty_only_uses_dynamic_score(self):
        smarty = ProviderResult(
            provider="smarty",
            success=True,
            dpv_match="Y",
            vacant=False,
            zip_plus_4="12345",
            raw_response=_SMARTY_SAMPLE,
        )
        melissa = ProviderResult(provider="melissa", success=False, error="fail")
        chosen, score = provider_arbitration(smarty, melissa)
        assert chosen.provider == "smarty"
        assert score == 100

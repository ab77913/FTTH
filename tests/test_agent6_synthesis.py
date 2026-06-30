"""
Tests for Agent 6 — Final FTTH Classification (pure synthesis logic).

The _synthesize() function is a pure Python function with no DB or HTTP
dependencies.  Every classification rule is tested here.

Run:
    pytest tests/test_agent6_synthesis.py -v
"""
from __future__ import annotations

from unittest.mock import MagicMock

# ── helpers to pull private functions without importing the whole agent ────────

from data_ingestion.agents.agent6_finalization import _synthesize, _majority_vote


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_addr():
    addr = MagicMock()
    addr.id = 1
    addr.raw_address = "123 Main St"
    return addr


def _make_a1(status="AUTO_ACCEPT", score=90):
    a1 = MagicMock()
    a1.validation_status = status
    a1.confidence_score = score
    a1.exception_reason = None
    return a1


def _a4(structure="SFH", confidence=80):
    return {"structure_type": structure, "confidence": confidence, "imagery_source": "street_view"}


def _a5(structure="SFH", confidence=75, units_min=1, units_max=1):
    return {
        "structure_type": structure,
        "confidence": confidence,
        "visible_units_min": units_min,
        "visible_units_max": units_max,
        "imagery_source": "street_view",
    }


def _a3(land_use="Single-Family Residential"):
    return {"land_use": land_use, "status": "found", "confidence": 55}


def _a2(status="geocoded", location_type="ROOFTOP"):
    return {"status": status, "location_type": location_type, "confidence": 95}


# ══════════════════════════════════════════════════════════════════════════════
# 1. majority_vote pure function
# ══════════════════════════════════════════════════════════════════════════════

class TestMajorityVote:
    def test_empty_list_returns_unknown(self):
        assert _majority_vote([]) == "Unknown"

    def test_all_unknown_returns_unknown(self):
        assert _majority_vote(["Unknown", "Unknown"]) == "Unknown"

    def test_single_type(self):
        assert _majority_vote(["SFH"]) == "SFH"

    def test_majority_wins(self):
        assert _majority_vote(["SFH", "SFH", "MDU"]) == "SFH"

    def test_ignores_unknown_in_majority(self):
        # "Unknown" is excluded from counting — so SFH wins
        assert _majority_vote(["Unknown", "SFH", "Unknown"]) == "SFH"

    def test_tie_returns_either(self):
        result = _majority_vote(["SFH", "MDU"])
        assert result in ("SFH", "MDU")

    def test_mdu_wins_clear_majority(self):
        assert _majority_vote(["MDU", "MDU", "SFH"]) == "MDU"

    def test_commercial_type(self):
        assert _majority_vote(["Commercial", "Commercial"]) == "Commercial"


# ══════════════════════════════════════════════════════════════════════════════
# 2. Agent-1 REJECT path
# ══════════════════════════════════════════════════════════════════════════════

class TestRejectPath:
    def test_rejected_address_returns_skip(self):
        result = _synthesize(
            addr=_make_addr(),
            a1=_make_a1("REJECT", score=10),
            a2=None, a3=None, a4=None, a5=None,
        )
        assert result["ftth_priority"] == "SKIP"
        assert result["final_structure_type"] == "Unknown"
        assert result["final_confidence"] == 0

    def test_rejected_status_field(self):
        result = _synthesize(
            addr=_make_addr(),
            a1=_make_a1("REJECT", score=5),
            a2=None, a3=None, a4=None, a5=None,
        )
        assert result["status"] == "rejected"
        assert "Agent 1" in result["validation_summary"]

    def test_no_agent1_data_not_rejected(self):
        # Without a1 data it should still produce a result (not reject)
        result = _synthesize(
            addr=_make_addr(),
            a1=None, a2=None, a3=None, a4=None, a5=None,
        )
        assert result["ftth_priority"] in ("HIGH", "MEDIUM", "LOW", "SKIP")
        assert result["status"] == "classified"


# ══════════════════════════════════════════════════════════════════════════════
# 3. SFH classification
# ══════════════════════════════════════════════════════════════════════════════

class TestSFHClassification:
    def test_sfh_auto_accept_is_medium(self):
        result = _synthesize(
            addr=_make_addr(),
            a1=_make_a1("AUTO_ACCEPT", 90),
            a2=_a2(), a3=_a3("Single-Family Residential"),
            a4=_a4("SFH", 85), a5=_a5("SFH", 80),
        )
        assert result["final_structure_type"] == "SFH"
        assert result["ftth_priority"] == "MEDIUM"
        assert result["final_is_mdu"] is False
        assert result["final_unit_count"] == 1

    def test_sfh_manual_review_is_low(self):
        result = _synthesize(
            addr=_make_addr(),
            a1=_make_a1("MANUAL_REVIEW", 55),
            a2=_a2(), a3=_a3("Single-Family Residential"),
            a4=_a4("SFH", 70), a5=_a5("SFH", 65),
        )
        assert result["final_structure_type"] == "SFH"
        assert result["ftth_priority"] == "LOW"

    def test_sfh_confidence_calculation(self):
        result = _synthesize(
            addr=_make_addr(),
            a1=_make_a1("AUTO_ACCEPT", 100),
            a2=_a2(), a3=_a3("Single-Family Residential"),
            a4=_a4("SFH", 100), a5=_a5("SFH", 100),
        )
        # Max confidence: 0.40*100 + 0.40*100 + 0.20*(10 or 20) = 82 or 100
        assert result["final_confidence"] <= 100
        assert result["final_confidence"] >= 0

    def test_sfh_no_imagery_still_classifies(self):
        """SFH classification works even without Agent 4/5 data."""
        result = _synthesize(
            addr=_make_addr(),
            a1=_make_a1("AUTO_ACCEPT", 85),
            a2=_a2(), a3=_a3("Single-Family Residential"),
            a4=None, a5=None,
        )
        assert result["final_structure_type"] in ("SFH", "Unknown")


# ══════════════════════════════════════════════════════════════════════════════
# 4. MDU classification
# ══════════════════════════════════════════════════════════════════════════════

class TestMDUClassification:
    def test_mdu_large_is_high_priority(self):
        result = _synthesize(
            addr=_make_addr(),
            a1=_make_a1("AUTO_ACCEPT", 90),
            a2=_a2(), a3=_a3("Multi-Family Residential"),
            a4=_a4("MDU", 85), a5=_a5("MDU", 80, units_min=10, units_max=12),
        )
        assert result["final_structure_type"] == "MDU_LARGE"
        assert result["ftth_priority"] == "HIGH"
        assert result["final_is_mdu"] is True
        assert result["final_unit_count"] >= 10

    def test_mdu_small_is_medium_priority(self):
        result = _synthesize(
            addr=_make_addr(),
            a1=_make_a1("AUTO_ACCEPT", 80),
            a2=_a2(), a3=_a3("Multi-Family Residential"),
            a4=_a4("MDU", 80), a5=_a5("MDU", 75, units_min=3, units_max=5),
        )
        assert result["final_structure_type"] == "MDU_SMALL"
        assert result["ftth_priority"] == "MEDIUM"

    def test_mdu_exactly_8_units_is_large(self):
        result = _synthesize(
            addr=_make_addr(),
            a1=_make_a1("AUTO_ACCEPT", 85),
            a2=_a2(), a3=_a3("Multi-Family Residential"),
            a4=_a4("MDU", 80), a5=_a5("MDU", 80, units_min=8, units_max=8),
        )
        assert result["final_structure_type"] == "MDU_LARGE"

    def test_mdu_land_use_votes_for_mdu(self):
        """A3 'apartment' land_use should add an MDU vote."""
        result = _synthesize(
            addr=_make_addr(),
            a1=_make_a1("AUTO_ACCEPT", 85),
            a2=_a2(), a3=_a3("apartment complex"),
            a4=_a4("MDU", 80), a5=_a5("MDU", 80, units_min=6, units_max=8),
        )
        assert result["final_is_mdu"] is True

    def test_mdu_unit_count_is_max_of_min_max(self):
        result = _synthesize(
            addr=_make_addr(),
            a1=_make_a1("AUTO_ACCEPT", 85),
            a2=_a2(), a3=_a3("Multi-Family Residential"),
            a4=_a4("MDU", 80), a5=_a5("MDU", 80, units_min=4, units_max=6),
        )
        assert result["final_unit_count"] == 6  # max(4, 6) = 6

    def test_detailed_agent5_apartment_type_maps_to_mdu(self):
        a5 = _a5("Unknown", 92, units_min=9, units_max=12)
        a5["structure_type_detail"] = "low_rise_apt"
        result = _synthesize(
            addr=_make_addr(),
            a1=_make_a1("AUTO_ACCEPT", 90),
            a2=_a2(), a3=_a3("Multi-Family Residential"),
            a4=_a4("Unknown", 0), a5=a5,
        )
        assert result["final_structure_type"] == "MDU_LARGE"
        assert result["final_is_mdu"] is True

    def test_high_conf_agent5_house_number_boosts_final_confidence(self):
        a5 = _a5("SFH", 40)
        a5["structure_type_detail"] = "detached_house"
        a5["high_conf_house_number"] = True
        a5["house_number_conf"] = 95.0
        result = _synthesize(
            addr=_make_addr(),
            a1=_make_a1("AUTO_ACCEPT", 70),
            a2=_a2(), a3=_a3("Single-Family Residential"),
            a4=_a4("Unknown", 0), a5=a5,
        )
        assert result["final_confidence"] >= 90
        assert result["a5_high_conf_house_number"] is True


# ══════════════════════════════════════════════════════════════════════════════
# 5. Commercial classification
# ══════════════════════════════════════════════════════════════════════════════

class TestCommercialClassification:
    def test_commercial_is_low_priority(self):
        result = _synthesize(
            addr=_make_addr(),
            a1=_make_a1("AUTO_ACCEPT", 80),
            a2=_a2(), a3=_a3("Commercial/Industrial"),
            a4=_a4("Commercial", 80), a5=_a5("Commercial", 75),
        )
        assert result["final_structure_type"] == "Commercial"
        assert result["ftth_priority"] == "LOW"

    def test_commercial_land_use_vote(self):
        result = _synthesize(
            addr=_make_addr(),
            a1=_make_a1("AUTO_ACCEPT", 75),
            a2=_a2(), a3=_a3("commercial warehouse"),
            a4=_a4("Commercial", 75), a5=_a5("Commercial", 70),
        )
        assert result["ftth_priority"] == "LOW"


# ══════════════════════════════════════════════════════════════════════════════
# 6. Vote weighting & conflict resolution
# ══════════════════════════════════════════════════════════════════════════════

class TestVoteWeighting:
    def test_conflicting_votes_uses_majority(self):
        """2 SFH votes vs 1 MDU vote → SFH wins."""
        result = _synthesize(
            addr=_make_addr(),
            a1=_make_a1("AUTO_ACCEPT", 80),
            a2=_a2(), a3=_a3("Single-Family Residential"),
            a4=_a4("SFH", 80), a5=_a5("MDU", 70, units_min=2, units_max=3),
        )
        # A3=SFH + A4=SFH vs A5=MDU → SFH majority
        assert result["final_structure_type"] == "SFH"

    def test_unanimous_agreement_higher_confidence(self):
        unanimous = _synthesize(
            addr=_make_addr(),
            a1=_make_a1("AUTO_ACCEPT", 80),
            a2=_a2(), a3=_a3("Multi-Family Residential"),
            a4=_a4("MDU", 80), a5=_a5("MDU", 80, units_min=5, units_max=5),
        )
        conflicting = _synthesize(
            addr=_make_addr(),
            a1=_make_a1("AUTO_ACCEPT", 80),
            a2=_a2(), a3=_a3("Multi-Family Residential"),
            a4=_a4("MDU", 80), a5=_a5("SFH", 80),  # A5 disagrees
        )
        # Unanimous agreement should give higher final confidence
        assert unanimous["final_confidence"] >= conflicting["final_confidence"]

    def test_land_use_as_tiebreaker(self):
        """When A4 and A5 disagree, A3 land_use provides additional vote."""
        result = _synthesize(
            addr=_make_addr(),
            a1=_make_a1("AUTO_ACCEPT", 80),
            a2=_a2(), a3=_a3("apartment building"),  # adds MDU vote
            a4=_a4("MDU", 80),                        # MDU vote
            a5=_a5("SFH", 75),                        # SFH vote
        )
        # 2 MDU votes (A4 + A3) vs 1 SFH vote (A5) → MDU wins
        assert result["final_is_mdu"] is True


# ══════════════════════════════════════════════════════════════════════════════
# 7. A2 geocoding influence
# ══════════════════════════════════════════════════════════════════════════════

class TestA2Influence:
    def test_a2_geocoded_flag_in_result(self):
        result = _synthesize(
            addr=_make_addr(),
            a1=_make_a1("AUTO_ACCEPT", 85),
            a2={"status": "geocoded", "location_type": "ROOFTOP", "confidence": 95},
            a3=_a3(), a4=_a4(), a5=_a5(),
        )
        assert result["a2_geocoded"] is True
        assert result["a2_location_type"] == "ROOFTOP"

    def test_a2_failed_does_not_block_classification(self):
        result = _synthesize(
            addr=_make_addr(),
            a1=_make_a1("AUTO_ACCEPT", 85),
            a2={"status": "failed", "confidence": 0},
            a3=_a3(), a4=_a4(), a5=_a5(),
        )
        assert result["status"] == "classified"
        assert result["a2_geocoded"] is False

    def test_missing_a2_still_classifies(self):
        result = _synthesize(
            addr=_make_addr(),
            a1=_make_a1("AUTO_ACCEPT", 85),
            a2=None, a3=_a3(), a4=_a4(), a5=_a5(),
        )
        assert result["status"] == "classified"


# ══════════════════════════════════════════════════════════════════════════════
# 8. Validation summary text
# ══════════════════════════════════════════════════════════════════════════════

class TestValidationSummary:
    def test_summary_contains_agent1_status(self):
        result = _synthesize(
            addr=_make_addr(),
            a1=_make_a1("AUTO_ACCEPT", 90),
            a2=_a2(), a3=_a3(), a4=_a4(), a5=_a5(),
        )
        assert "AUTO_ACCEPT" in result["validation_summary"]
        assert "90" in result["validation_summary"]

    def test_summary_contains_structure_type(self):
        result = _synthesize(
            addr=_make_addr(),
            a1=_make_a1("AUTO_ACCEPT", 85),
            a2=_a2(), a3=_a3("Multi-Family Residential"),
            a4=_a4("MDU", 80), a5=_a5("MDU", 80, units_min=5, units_max=5),
        )
        assert "MDU" in result["validation_summary"]

    def test_mdu_summary_includes_unit_count(self):
        result = _synthesize(
            addr=_make_addr(),
            a1=_make_a1("AUTO_ACCEPT", 85),
            a2=_a2(), a3=_a3("Multi-Family Residential"),
            a4=_a4("MDU", 80), a5=_a5("MDU", 80, units_min=8, units_max=10),
        )
        assert "units" in result["validation_summary"].lower()

    def test_imagery_source_in_summary(self):
        result = _synthesize(
            addr=_make_addr(),
            a1=_make_a1("AUTO_ACCEPT", 85),
            a2=_a2(), a3=_a3(),
            a4=_a4("SFH", 80), a5=_a5("SFH", 80),
        )
        assert "street_view" in result["validation_summary"]


# ══════════════════════════════════════════════════════════════════════════════
# 9. Output field completeness
# ══════════════════════════════════════════════════════════════════════════════

class TestOutputFieldCompleteness:
    REQUIRED_FIELDS = [
        "status", "final_structure_type", "final_is_mdu", "final_unit_count",
        "final_confidence", "ftth_priority", "validation_summary",
        "a1_status", "a1_score",
    ]

    def test_all_required_fields_present_classified(self):
        result = _synthesize(
            addr=_make_addr(),
            a1=_make_a1("AUTO_ACCEPT", 85),
            a2=_a2(), a3=_a3(), a4=_a4(), a5=_a5(),
        )
        for field in self.REQUIRED_FIELDS:
            assert field in result, f"Missing field: {field}"

    def test_all_required_fields_present_rejected(self):
        result = _synthesize(
            addr=_make_addr(),
            a1=_make_a1("REJECT", 0),
            a2=None, a3=None, a4=None, a5=None,
        )
        for field in ("status", "final_structure_type", "final_confidence",
                      "ftth_priority", "validation_summary"):
            assert field in result

    def test_confidence_always_0_to_100(self):
        for status, score in [("AUTO_ACCEPT", 100), ("MANUAL_REVIEW", 50), ("AUTO_ACCEPT", 0)]:
            result = _synthesize(
                addr=_make_addr(),
                a1=_make_a1(status, score),
                a2=_a2(), a3=_a3(), a4=_a4("SFH", 100), a5=_a5("SFH", 100),
            )
            conf = result["final_confidence"]
            assert 0 <= conf <= 100, f"confidence={conf} out of range for status={status}"

    def test_ftth_priority_always_valid_value(self):
        valid = {"HIGH", "MEDIUM", "LOW", "SKIP"}
        for status in ("AUTO_ACCEPT", "MANUAL_REVIEW", "REJECT"):
            result = _synthesize(
                addr=_make_addr(),
                a1=_make_a1(status, 80),
                a2=_a2(), a3=_a3(), a4=_a4(), a5=_a5(),
            )
            assert result["ftth_priority"] in valid

    def test_vote_pool_in_classified_result(self):
        result = _synthesize(
            addr=_make_addr(),
            a1=_make_a1("AUTO_ACCEPT", 85),
            a2=_a2(), a3=_a3("Single-Family Residential"),
            a4=_a4("SFH"), a5=_a5("SFH"),
        )
        assert "vote_pool" in result
        assert isinstance(result["vote_pool"], list)

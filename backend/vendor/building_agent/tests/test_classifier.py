"""Tests for StructureHintClassifier rule-based logic."""

from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from src.classifier import StructureHintClassifier


CONFIG = {
    "thresholds": {
        "sfu_max_area_m2": 650,
        "mdu_min_area_m2": 300,
        "anchor_min_area_m2": 5000,
        "elongation_mdu_threshold": 3.0,
    }
}


@pytest.fixture()
def clf() -> StructureHintClassifier:
    return StructureHintClassifier(CONFIG)


def _features(area: float = 200.0, elongation: float = 1.2) -> dict:
    return {"footprint_area_m2": area, "elongation_ratio": elongation}


def test_anchor_by_owner(clf: StructureHintClassifier) -> None:
    """GOV owner type forces ANCHOR at 95 confidence."""
    result = clf.classify(_features(), assessor={"owner_type": "GOV"})
    assert result["structure_hint"] == "ANCHOR"
    assert result["hint_confidence"] == 95


def test_anchor_by_area(clf: StructureHintClassifier) -> None:
    """Area > anchor_min_area_m2 forces ANCHOR."""
    result = clf.classify(_features(area=8000.0))
    assert result["structure_hint"] == "ANCHOR"


def test_mdu_by_elongation(clf: StructureHintClassifier) -> None:
    """elongation_ratio > 3.0 with moderate area should yield MDU_SMALL."""
    result = clf.classify(_features(area=500.0, elongation=4.0))
    assert result["structure_hint"] == "MDU_SMALL"


def test_sfu_small(clf: StructureHintClassifier) -> None:
    """Small area and low elongation should yield SFU."""
    result = clf.classify(_features(area=120.0, elongation=1.2))
    assert result["structure_hint"] == "SFU"


def test_mdu_large_moderate_elongation(clf: StructureHintClassifier) -> None:
    """Large footprint with moderate elongation should not fall through to SFU."""
    result = clf.classify(_features(area=800.0, elongation=2.5))
    assert result["structure_hint"] == "MDU_LARGE"


def test_mdu_strip_moderate_elongation(clf: StructureHintClassifier) -> None:
    """Medium elongated strip (apartment wing) should be MDU_SMALL, not SFU."""
    result = clf.classify(_features(area=450.0, elongation=2.6))
    assert result["structure_hint"] == "MDU_SMALL"


def test_sfu_ranch_regression(clf: StructureHintClassifier) -> None:
    """Typical ranch home stays SFU."""
    result = clf.classify(_features(area=280.0, elongation=2.1))
    assert result["structure_hint"] == "SFU"


def test_unresolved(clf: StructureHintClassifier) -> None:
    """Zero area and zero elongation should yield UNRESOLVED."""
    result = clf.classify({"footprint_area_m2": 0.0, "elongation_ratio": 0.0})
    assert result["structure_hint"] == "UNRESOLVED"
    assert result["hint_confidence"] == 0


def test_signals_populated(clf: StructureHintClassifier) -> None:
    """hint_signals should be non-empty for any resolved classification."""
    for features, assessor in [
        (_features(area=120.0, elongation=1.2), None),
        (_features(area=8000.0), None),
        (_features(area=500.0, elongation=4.0), None),
        (_features(), {"owner_type": "EDU"}),
    ]:
        result = clf.classify(features, assessor)
        assert len(result["hint_signals"]) > 0, f"No signals for {result['structure_hint']}"


def test_batch_classify(clf: StructureHintClassifier) -> None:
    """classify_batch on 5 records returns 5 results each with structure_hint."""
    records = [
        {"footprint_area_m2": 120.0, "elongation_ratio": 1.2},
        {"footprint_area_m2": 500.0, "elongation_ratio": 4.0},
        {"footprint_area_m2": 8000.0, "elongation_ratio": 1.5},
        {"footprint_area_m2": 0.0, "elongation_ratio": 0.0},
        {"footprint_area_m2": 400.0, "elongation_ratio": 2.0, "assessor": {"owner_type": "MED"}},
    ]
    results = clf.classify_batch(records)
    assert len(results) == 5
    for r in results:
        assert "structure_hint" in r
        assert r["structure_hint"] in {"SFU", "MDU_SMALL", "MDU_LARGE", "MXU", "ANCHOR", "UNRESOLVED"}

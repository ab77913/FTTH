"""Tests for vacant-lot classification in Agent 4."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from data_ingestion.agents import agent4_building


def _addr(**kwargs):
    defaults = {
        "id": 1,
        "raw_address": "1 Example St",
        "validated_raw_address": None,
        "raw_metadata": {},
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class TestAttomVacantLandUse:
    def test_vacant_land_maps_to_vacant(self):
        assert agent4_building._attom_classify_land_use("VACANT LAND") == "Vacant"

    def test_vacant_lot_maps_to_vacant(self):
        assert agent4_building._attom_classify_land_use("VACANT LOT RESIDENTIAL") == "Vacant"

    def test_residential_without_vacant_still_sfh(self):
        assert agent4_building._attom_classify_land_use("SINGLE FAMILY RESIDENTIAL") == "SFH"


class TestMicrosoftVacantLotRules:
    def test_no_footprint_without_evidence_stays_unresolved(self):
        enriched = {
            "building_matched": False,
            "structure_hint": "UNRESOLVED",
            "hint_confidence": 0,
            "class_source": "NONE",
        }
        payload = agent4_building._to_agent4_payload(enriched, _addr(), None)
        assert payload["structure_type"] == "UNRESOLVED"
        assert payload["structure_type"] != "Vacant"
        assert "no_building_footprint_matched" in payload["hint_signals"]

    def test_no_footprint_with_validated_match_becomes_sfh(self):
        enriched = {
            "building_matched": False,
            "structure_hint": "UNRESOLVED",
            "hint_confidence": 0,
            "class_source": "NONE",
        }
        addr = _addr(raw_metadata={
            "address_validation": {
                "match_status": "MATCH",
                "confidence_score": 100,
                "location_type": "ROOFTOP",
            },
        })
        payload = agent4_building._to_agent4_payload(enriched, addr, None)
        assert payload["structure_type"] == "SFH"
        assert payload["class_source"] == "VALIDATED_PIN_FALLBACK"
        assert "validated_pin_building_fallback" in payload["hint_signals"]

    def test_distant_large_footprint_becomes_vacant(self):
        enriched = {
            "building_matched": True,
            "structure_hint": "SFU",
            "hint_confidence": 90,
            "class_source": "ML",
            "footprint_area_m2": 430,
            "footprint_match_distance_m": 40.51,
            "hint_signals": [],
        }
        addr = _addr(raw_metadata={
            "address_validation": {
                "match_status": "MATCH",
                "confidence_score": 100,
            },
        })
        payload = agent4_building._to_agent4_payload(enriched, addr, None)
        assert payload["structure_type"] == "Vacant"
        assert "distant_footprint_rejected" in payload["hint_signals"]

    def test_distant_small_footprint_with_validated_match_becomes_sfh(self):
        """MS offset ~31m on a house-sized footprint should not be marked vacant."""
        enriched = {
            "building_matched": True,
            "structure_hint": "SFU",
            "hint_confidence": 90,
            "class_source": "ML",
            "footprint_area_m2": 186,
            "footprint_match_distance_m": 31.11,
            "hint_signals": ["geometry_small_compact"],
        }
        addr = _addr(raw_metadata={
            "address_validation": {
                "match_status": "MATCH",
                "confidence_score": 100,
                "location_type": "ROOFTOP",
            },
        })
        payload = agent4_building._to_agent4_payload(enriched, addr, None)
        assert payload["structure_type"] == "SFH"
        assert payload["structure_type"] != "Vacant"
        assert "distant_footprint_unreliable" in payload["hint_signals"]
        assert "validated_pin_building_fallback" in payload["hint_signals"]

    def test_parcel_vacant_land_use_without_address_evidence_is_vacant(self):
        enriched = {
            "building_matched": False,
            "structure_hint": "UNRESOLVED",
            "hint_confidence": 0,
            "class_source": "NONE",
        }
        payload = agent4_building._to_agent4_payload(
            enriched,
            _addr(),
            None,
            a3={"land_use": "VACANT LAND"},
        )
        assert payload["structure_type"] == "Vacant"
        assert "parcel_vacant_land_use" in payload["hint_signals"]

    def test_agent1_vacant_flag_overrides_sfh(self):
        a1 = MagicMock()
        a1.smarty_vacant = True
        a1.melissa_vacant = False
        enriched = {
            "building_matched": True,
            "structure_hint": "SFU",
            "hint_confidence": 90,
            "class_source": "RULE",
            "footprint_area_m2": 180,
            "footprint_match_distance_m": 5,
        }
        payload = agent4_building._to_agent4_payload(enriched, _addr(), a1)
        assert payload["structure_type"] == "Vacant"
        assert "address_validation_vacant_flag" in payload["hint_signals"]

    def test_matched_unresolved_with_building_not_marked_vacant(self):
        enriched = {
            "building_matched": True,
            "structure_hint": "UNRESOLVED",
            "hint_confidence": 0,
            "class_source": "HUMAN",
            "footprint_area_m2": 210,
            "footprint_match_distance_m": 22,
            "hint_signals": [],
        }
        payload = agent4_building._to_agent4_payload(enriched, _addr(), None)
        assert payload["structure_type"] == "SFH"
        assert payload["structure_type"] != "Vacant"
        assert payload["building_matched"] is True
        assert payload["unit_count"] == 1

    def test_mdu_footprint_fallback_sets_unit_count(self):
        enriched = {
            "building_matched": True,
            "structure_hint": "UNRESOLVED",
            "hint_confidence": 0,
            "class_source": "HUMAN",
            "footprint_area_m2": 543.73,
            "footprint_match_distance_m": 12,
            "hint_signals": [],
        }
        payload = agent4_building._to_agent4_payload(enriched, _addr(), None)
        assert payload["structure_type"] == "MDU"
        assert payload["structure_hint"] == "MDU_SMALL"
        assert payload["unit_count"] == 3

    def test_mdu_small_from_ms_agent_gets_unit_count(self):
        enriched = {
            "building_matched": True,
            "structure_hint": "MDU_SMALL",
            "hint_confidence": 90,
            "class_source": "RULE",
            "footprint_area_m2": 520.0,
            "footprint_match_distance_m": 8,
            "hint_signals": ["area_or_shape"],
        }
        payload = agent4_building._to_agent4_payload(enriched, _addr(), None)
        assert payload["structure_type"] == "MDU"
        assert payload["unit_count"] == 3

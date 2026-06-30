from data_ingestion.agents.pipeline_runner import _flow_rule_status, _rule_status_from_merge_status


def test_flow_rule_status_verifies_first_agent_match_above_threshold():
    status, color, reason = _flow_rule_status(
        {
            "routing_history": [
                {
                    "source_agent": "agent0_reverse_geocoder",
                    "status": "MATCH",
                    "confidence": 92,
                    "threshold": 90,
                }
            ],
            "final_resolution": {
                "source_agent": "agent0_reverse_geocoder",
                "status": "MATCH",
                "confidence": 92,
                "threshold": 90,
            },
        },
        92,
    )

    assert status == "verified"
    assert color == "green"
    assert "MATCH above threshold" in reason


def test_flow_rule_status_invalidates_final_mismatch_even_with_high_confidence():
    status, color, reason = _flow_rule_status(
        {
            "routing_history": [
                {
                    "source_agent": "agent0_reverse_geocoder",
                    "status": "MISMATCH",
                    "confidence": 95,
                    "threshold": 90,
                    "action": "continue",
                }
            ],
            "final_resolution": {
                "source_agent": "agent5_streetview",
                "status": "MISMATCH",
                "confidence": 94,
                "threshold": 90,
            },
        },
        94,
    )

    assert status == "invalid"
    assert color == "red"
    assert "MISMATCH" in reason


def test_flow_rule_status_does_not_verify_first_agent_below_threshold():
    status, color, reason = _flow_rule_status(
        {
            "routing_history": [
                {
                    "source_agent": "agent0_reverse_geocoder",
                    "status": "MATCH",
                    "confidence": 65,
                    "threshold": 90,
                }
            ],
        },
        65,
    )

    assert status == "invalid"
    assert color == "red"
    assert "No selected flow agent" in reason


def test_rule_status_contract_maps_merge_status_to_explicit_output_values():
    assert _rule_status_from_merge_status("verified") == "valid"
    assert _rule_status_from_merge_status("invalid") == "invalid"
    assert _rule_status_from_merge_status("duplicate") == "duplicate"
    assert _rule_status_from_merge_status("new") == "new"


def test_flow_rule_status_keeps_sticky_duplicate_white():
    status, color, reason = _flow_rule_status(
        {
            "record_status": "DUPLICATE",
            "address_validation": {"match_status": "MATCH"},
            "final_resolution": {"status": "AUTO_ACCEPT", "confidence": 100},
        },
        100,
    )

    assert status == "duplicate"
    assert color == "white"
    assert "duplicate" in reason.lower()

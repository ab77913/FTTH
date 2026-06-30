"""
Unit tests for the pipeline runner and A2A bus integration.

Tests:
  - Stage progress calculation (_stage_progress, _overall_pct)
  - Address classification logic (_classify_addresses) — mocked DB
  - Full run_full_pipeline with all agents mocked
  - A2A bus events are published at the right moments
  - Pipeline is resilient when bus is unavailable
  - Progress callback is invoked correctly

No live DB or RabbitMQ required.

Run:
    pytest tests/test_pipeline_unit.py -v
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import pytest

from data_ingestion.agents.pipeline_runner import (
    _stage_progress,
    _overall_pct,
    _STAGE_WEIGHTS,
    _STAGE_NAMES,
    _agent1_match_status,
    _apply_resolution_gate,
    _ids_with_real_address,
    _status_is_match,
    run_full_pipeline,
)
from data_ingestion.agents.agent6_finalization import _synthesize
from data_ingestion.utils.pipeline_progress import (
    apply_stage_progress,
    mark_current_agent_failed,
)


def _sample_agents() -> list[dict]:
    return [
        {"status": "pending", "progress": 0, "records_total": 7, "records_processed": 0},
        {"status": "pending", "progress": 0, "records_total": 7, "records_processed": 0},
        {"status": "pending", "progress": 0, "records_total": 7, "records_processed": 0},
        {"status": "pending", "progress": 0, "records_total": 7, "records_processed": 0},
        {"status": "pending", "progress": 0, "records_total": 7, "records_processed": 0},
        {"status": "pending", "progress": 0, "records_total": 7, "records_processed": 0},
        {"status": "pending", "progress": 0, "records_total": 7, "records_processed": 0},
        {"status": "pending", "progress": 0, "records_total": 7, "records_processed": 0},
    ]


# ══════════════════════════════════════════════════════════════════════════════
# 1. Stage progress math
# ══════════════════════════════════════════════════════════════════════════════

class TestStageProgress:
    def test_stage_weights_sum_to_one(self):
        assert abs(sum(_STAGE_WEIGHTS) - 1.0) < 1e-6

    def test_ten_stage_weights(self):
        assert len(_STAGE_WEIGHTS) == 10

    def test_ten_stage_names(self):
        assert len(_STAGE_NAMES) == 10
        assert _STAGE_NAMES[-1] == "agent7_neighborhood_discovery"

    def test_overall_pct_stage_0_start(self):
        pct = _overall_pct(0, 0, 1)
        assert pct == 0

    def test_overall_pct_stage_0_end(self):
        pct = _overall_pct(0, 1, 1)
        assert pct == int(_STAGE_WEIGHTS[0] * 100)

    def test_overall_pct_all_stages_below_100(self):
        for i in range(len(_STAGE_WEIGHTS)):
            pct = _overall_pct(i, 1, 1)
            assert 0 <= pct <= 99, f"stage {i} pct={pct} out of range"

    def test_stage_progress_calls_callback(self):
        calls = []
        def cb(done, total, stage, s_done, s_total):
            calls.append((done, total, stage))

        _stage_progress(cb, 2, 1, 10)
        assert len(calls) == 1
        assert calls[0][1] == 100
        assert calls[0][2] == "agent2_geocoding"

    def test_stage_progress_none_callback_is_noop(self):
        _stage_progress(None, 0, 0, 1)  # must not raise

    def test_overall_pct_mid_stage(self):
        pct_half = _overall_pct(3, 5, 10)
        pct_end  = _overall_pct(3, 10, 10)
        assert pct_half < pct_end

    def test_stage_names_put_building_before_streetview_and_final(self):
        assert _STAGE_NAMES.index("agent4_building") < _STAGE_NAMES.index("agent5_0_offline_ocr")
        assert _STAGE_NAMES.index("agent5_0_offline_ocr") < _STAGE_NAMES.index("agent5_streetview")
        assert _STAGE_NAMES.index("agent4_building") < _STAGE_NAMES.index("agent5_streetview")
        assert _STAGE_NAMES.index("agent4_building") < _STAGE_NAMES.index("agent6_final")


class TestAgentQualityRules:
    @staticmethod
    def _address_row(address_id, status=None, metadata_status=None):
        metadata = {}
        if metadata_status is not None:
            metadata["address_validation"] = {"match_status": metadata_status}
        return SimpleNamespace(
            id=address_id,
            coord_address_match_status=status,
            raw_metadata=metadata,
            validated_raw_address=None,
            raw_address=f"{address_id} Main St",
        )

    def test_agent1_match_status_prefers_database_column(self):
        row = self._address_row(1, status="MISMATCH", metadata_status="MATCH")
        assert _agent1_match_status(row) == "MISMATCH"

    def test_agent1_match_status_falls_back_to_metadata(self):
        row = self._address_row(1, metadata_status="match")
        assert _agent1_match_status(row) == "MATCH"

    def test_real_address_gate_excludes_existing_matches_from_smarty(self):
        rows = [
            self._address_row(1, status="MATCH"),
            self._address_row(2, status="MISMATCH"),
            self._address_row(3, metadata_status="MATCH"),
            self._address_row(4),
        ]
        session = MagicMock()
        session.scalars.return_value.all.return_value = rows

        with patch(
            "data_ingestion.agents.pipeline_runner.get_session_factory",
            return_value=lambda: session,
        ):
            assert _ids_with_real_address("job-id", [1, 2, 3, 4]) == [2, 4]

        session.close.assert_called_once()

    def test_agent2_validated_match_populates_final_resolution(self):
        address = SimpleNamespace(
            id=1,
            raw_address="4910 NW 152ND LN",
            latitude=29.370049,
            longitude=-82.2061220000017,
            raw_metadata={},
        )
        agent2_result = SimpleNamespace(
            address_id=1,
            data={
                "status": "validated_match",
                "confidence": 100,
                "formatted_address": "4910 NW 152nd Ln, Reddick, FL 32686, USA",
                "latitude": 29.370039,
                "longitude": -82.2061066,
                "source": "GOOGLE_FORWARD",
            },
        )
        session = MagicMock()
        session.scalars.side_effect = [
            MagicMock(all=MagicMock(return_value=[address])),
            MagicMock(all=MagicMock(return_value=[agent2_result])),
        ]

        with patch(
            "data_ingestion.agents.pipeline_runner.get_session_factory",
            return_value=lambda: session,
        ):
            with patch("sqlalchemy.orm.attributes.flag_modified"):
                continued, counts = _apply_resolution_gate(
                    "00000000-0000-0000-0000-000000000001",
                    [1],
                    "agent2_geocoding",
                    70,
                )

        assert continued == []
        assert counts == {"total": 1, "accepted": 1, "continued": 0}
        final = address.raw_metadata["final_resolution"]
        assert final["address"] == agent2_result.data["formatted_address"]
        assert final["latitude"] == agent2_result.data["latitude"]
        assert final["longitude"] == agent2_result.data["longitude"]
        assert final["confidence"] == 100
        assert final["provider"] == "GOOGLE_FORWARD"
        assert final["source_agent"] == "agent2_geocoding"

    def test_agent5_analyzed_status_can_pass_confidence_gate(self):
        assert _status_is_match("analyzed") is True

    def test_agent6_agreement_score_uses_full_weight(self):
        addr = SimpleNamespace(raw_metadata={})
        a1 = SimpleNamespace(validation_status="AUTO_ACCEPT", confidence_score=90, exception_reason="")
        result = _synthesize(
            addr=addr,
            a1=a1,
            a2={"status": "geocoded"},
            a3={"land_use": "single family residential"},
            a4={"structure_type": "SFH", "confidence": 90},
            a5={"structure_type": "SFH", "confidence": 90},
        )
        assert result["final_structure_type"] == "SFH"
        assert result["final_confidence"] >= 90

    def test_overall_pct_zero_total_does_not_raise(self):
        pct = _overall_pct(0, 0, 0)
        assert pct == 0


class TestPipelineAgentProgress:
    def test_stage_to_agent_idx_matches_dashboard_agent_order(self):
        """Regression: swapped A4/A5 indices made OCR (A5) show as Agent 4 running."""
        dashboard_order = [
            "agent0_house_discovery",
            "agent2_geocoding",
            "agent1_address_validator",
            "agent3_parcel",
            "agent4_building",
            "agent5_0_offline_ocr",
            "agent5_streetview",
            "agent6_final",
        ]
        from data_ingestion.utils.pipeline_progress import STAGE_TO_AGENT_IDX

        for idx, agent_id in enumerate(dashboard_order):
            assert STAGE_TO_AGENT_IDX[agent_id] == idx

    def test_agent5_stage_progress_updates_streetview_slot_not_building(self):
        agents = _sample_agents()
        idx = apply_stage_progress(
            agents,
            stage_name="agent5_streetview",
            stage_done=3,
            stage_total=10,
            total_records=10,
        )
        assert idx == 6
        assert agents[4]["status"] == "completed"
        assert agents[5]["status"] == "completed"
        assert agents[6]["status"] == "running"
        assert agents[6]["records_processed"] == 3

    def test_agent5_stage_progress_marks_agent4_completed_first(self):
        agents = _sample_agents()
        apply_stage_progress(
            agents,
            stage_name="agent4_building",
            stage_done=8,
            stage_total=8,
            total_records=8,
        )
        idx = apply_stage_progress(
            agents,
            stage_name="agent5_streetview",
            stage_done=2,
            stage_total=10,
            total_records=10,
        )
        assert idx == 6
        assert agents[4]["status"] == "completed"
        assert agents[5]["status"] == "completed"
        assert agents[6]["status"] == "running"

    def test_agent4_stage_progress_runs_before_streetview_slot(self):
        agents = _sample_agents()
        idx = apply_stage_progress(
            agents,
            stage_name="agent4_building",
            stage_done=2,
            stage_total=8,
            total_records=8,
        )
        assert idx == 4
        assert agents[4]["status"] == "running"
        assert agents[5]["status"] == "pending"

    def test_apply_stage_progress_marks_previous_agents_completed(self):
        agents = _sample_agents()
        agents[0]["status"] = "running"

        idx = apply_stage_progress(
            agents,
            stage_name="agent1_address_validator",
            stage_done=2,
            stage_total=7,
            total_records=7,
        )

        assert idx == 2
        assert agents[0]["status"] == "completed"
        assert agents[1]["status"] == "completed"
        assert agents[2]["status"] == "running"
        assert agents[2]["records_processed"] == 2

    def test_mark_current_agent_failed_leaves_completed_agents_green(self):
        agents = _sample_agents()
        agents[0]["status"] = "completed"
        agents[1]["status"] = "completed"
        agents[2]["status"] = "running"

        mark_current_agent_failed(agents, 2, "boom")

        assert agents[0]["status"] == "completed"
        assert agents[1]["status"] == "completed"
        assert agents[2]["status"] == "failed"
        assert agents[2]["errors"] == ["boom"]


# ══════════════════════════════════════════════════════════════════════════════
# 2. Full pipeline with all stages mocked
# ══════════════════════════════════════════════════════════════════════════════

def _mock_classify(job_id):
    """Returns (coord_only=[], addr_ids=[1,2,3], all_ids=[1,2,3])."""
    return [], [1, 2, 3], [1, 2, 3]


def _mock_classify_coord_only(job_id):
    """Returns coord-only addresses."""
    return [1, 2], [], [1, 2]


DUMMY_SUMMARY = {"total": 3, "processed": 3}


@pytest.fixture(autouse=True)
def _mock_resolution_gate_and_coord_validation():
    """Pipeline unit tests mock agent calls; keep routing gates/coord validation DB-free."""
    def _gate(_job_id, candidate_ids, _agent_name, _threshold):
        return list(candidate_ids or []), {
            "total": len(candidate_ids or []),
            "accepted": 0,
            "continued": len(candidate_ids or []),
        }

    with patch("data_ingestion.agents.pipeline_runner.session_scope", side_effect=RuntimeError("offline unit test")):
        with patch("data_ingestion.agents.pipeline_runner.run_agent0_for_job", return_value={"skipped": True}):
            with patch("data_ingestion.agents.pipeline_runner._refresh_rule_classification_after_processing", return_value={}):
                with patch("data_ingestion.agents.pipeline_runner._write_new_bundle_from_agent_results", return_value=None):
                    with patch("data_ingestion.agents.pipeline_runner._apply_resolution_gate", side_effect=_gate):
                        with patch("data_ingestion.agents.pipeline_runner._delete_unconfirmed_agent0_discoveries", return_value={"checked": 0, "deleted": 0, "deleted_ids": []}):
                            with patch("data_ingestion.agents.pipeline_runner._ids_with_real_address", side_effect=lambda _job_id, ids: list(ids or [])):
                                with patch("data_ingestion.agents.pipeline_runner._agent0_discovered_ids", return_value=[]):
                                    yield


def _mock_geocoding_summary(*, reverse=None, forward=None):
    reverse = reverse if reverse is not None else {"skipped": True}
    forward = forward if forward is not None else {"processed": 3}
    return {
        "reverse": reverse,
        "reverse_geocoder": reverse,
        "coord_address_validation": {},
        "forward": forward,
        "agent2_geocoding": forward,
    }


class TestFullPipeline:
    def _run_with_mocks(self, classify_fn=None, bus_connected=False):
        """Helper: patch all agent functions + DB + bus, return results."""
        classify = classify_fn or _mock_classify

        mock_bus = MagicMock()
        mock_bus.connected = bus_connected

        with patch("data_ingestion.agents.pipeline_runner._classify_addresses", side_effect=classify):
            with patch("data_ingestion.agents.pipeline_runner.run_agent1_for_job", return_value={"processed": 3}):
                with patch(
                    "data_ingestion.agents.pipeline_runner.run_geocoding_for_job",
                    return_value=_mock_geocoding_summary(),
                ):
                    with patch("data_ingestion.agents.pipeline_runner.run_agent3_for_job", return_value={"processed": 3}):
                        with patch("data_ingestion.agents.pipeline_runner.run_agent4_for_job", return_value={"processed": 3}):
                            with patch("data_ingestion.agents.pipeline_runner.run_agent5_for_job", return_value={"processed": 3}):
                                with patch("data_ingestion.agents.pipeline_runner.run_agent6_for_job", return_value={"high": 1}):
                                    with patch("data_ingestion.agents.pipeline_runner._get_agent_bus", return_value=mock_bus):
                                        results = run_full_pipeline("job_test")
        return results, mock_bus

    def test_returns_dict_with_all_stage_keys(self):
        results, _ = self._run_with_mocks()
        assert "agent0_house_discovery" in results
        assert "classify" in results
        assert "agent1_address_validator" in results
        assert "agent2_geocoding" in results
        assert "agent3_parcel" in results
        assert "agent4_building" in results
        assert "agent5_streetview" in results
        assert "agent6_final" in results

    def test_classify_summary_in_results(self):
        results, _ = self._run_with_mocks()
        classify = results["classify"]
        assert "total" in classify
        assert classify["total"] == 3

    def test_agent1_skipped_when_no_address_ids(self):
        """When all addresses are coord-only, Agent 1 is skipped."""
        with patch("data_ingestion.agents.pipeline_runner._ids_with_real_address", return_value=[]):
            results, _ = self._run_with_mocks(classify_fn=_mock_classify_coord_only)
        assert results.get("agent1_address_validator") == {"skipped": True}

    def test_agent0_internal_validation_receives_inserted_ids(self):
        validation_calls = []

        def validate(_job_id, inserted_ids, *, agent0_options):
            validation_calls.append((list(inserted_ids), dict(agent0_options)))
            return {"checked": len(inserted_ids), "deleted": 1, "deleted_ids": [202]}

        opts = {
            "agent0": {
                "enabled": True,
                "validate_discovered": True,
                "validate_with_smarty": True,
                "validate_with_regrid": False,
                "validation_threshold": 90,
            },
            "agent1": {"enabled": False},
            "agent2": {"enabled": False},
            "agent3": {"enabled": False},
            "agent4": {"enabled": False},
            "agent5": {"enabled": False},
            "agent6": {"enabled": False},
        }

        with (
            patch("data_ingestion.agents.pipeline_runner.run_agent0_for_job", return_value={"inserted_address_ids": [201, 202]}),
            patch("data_ingestion.agents.pipeline_runner._validate_agent0_discoveries", side_effect=validate),
            patch("data_ingestion.agents.pipeline_runner._classify_addresses", return_value=([], [201], [201])),
            patch("data_ingestion.agents.pipeline_runner._get_agent_bus", return_value=None),
        ):
            results = run_full_pipeline("job_agent0_validate", pipeline_options=opts)

        assert validation_calls[0][0] == [201, 202]
        assert validation_calls[0][1]["validate_discovered"] is True
        assert validation_calls[0][1]["validate_with_smarty"] is True
        assert validation_calls[0][1]["validation_threshold"] == 90
        assert results["agent0_internal_validation"]["deleted_ids"] == [202]

    def test_agent0_rows_are_excluded_from_normal_agent1_only(self):
        a1_calls = []
        a3_calls = []

        def track_a1(_job_id, address_ids=None, **_kwargs):
            a1_calls.append(list(address_ids or []))
            return {"processed": len(address_ids or [])}

        def track_a3(_job_id, address_ids=None, **_kwargs):
            a3_calls.append(list(address_ids or []))
            return {"processed": len(address_ids or [])}

        opts = {
            "agent0": {"enabled": False, "exclude_discovered_from_agent1": True},
            "agent2": {"enabled": False},
            "agent1": {"enabled": True, "smarty": True},
            "agent3": {"enabled": True, "regrid": True},
            "agent4": {"enabled": False},
            "agent5": {"enabled": False},
            "agent6": {"enabled": False},
        }

        with (
            patch("data_ingestion.agents.pipeline_runner._classify_addresses", return_value=([], [101, 202], [101, 202])),
            patch("data_ingestion.agents.pipeline_runner._ids_with_real_address", side_effect=lambda _job_id, ids: list(ids or [])),
            patch("data_ingestion.agents.pipeline_runner._agent0_discovered_ids", return_value=[202]),
            patch("data_ingestion.agents.pipeline_runner.run_agent1_for_job", side_effect=track_a1),
            patch("data_ingestion.agents.pipeline_runner.run_agent3_for_job", side_effect=track_a3),
            patch("data_ingestion.agents.pipeline_runner._get_agent_bus", return_value=None),
        ):
            results = run_full_pipeline("job_agent0_skip_a1", pipeline_options=opts)

        assert a1_calls == [[101]]
        assert [sorted(ids) for ids in a3_calls] == [[101, 202]]
        assert results["agent0_excluded_from_agent1"]["excluded_ids"] == [202]

    def test_agent5_runs_when_only_agent5_enabled(self):
        """Flow Builder may enable Agent 5 alone; prior agents disabled must not drain active_ids."""
        a5_calls: list[list[int] | None] = []

        def track_a5(job_id, address_ids=None, **kwargs):
            a5_calls.append(address_ids)
            return {"analyzed": len(address_ids or [])}

        opts = {
            "agent2": {"enabled": False},
            "agent1": {"enabled": False},
            "agent3": {"enabled": False},
            "agent4": {"enabled": False},
            "agent5": {"enabled": True, "street_view": True, "satellite_fallback": True, "azure_vision": True},
            "agent6": {"enabled": False},
        }

        with patch("data_ingestion.agents.pipeline_runner._classify_addresses", return_value=([], [1, 2, 3], [1, 2, 3])):
            with patch("data_ingestion.agents.pipeline_runner._ids_with_real_address", return_value=[1, 2, 3]):
                with patch("data_ingestion.agents.pipeline_runner.run_agent5_for_job", side_effect=track_a5):
                    with patch("data_ingestion.agents.pipeline_runner._get_agent_bus", return_value=None):
                        results = run_full_pipeline("job_a5_only", pipeline_options=opts)

        assert a5_calls == [[1, 2, 3]]
        assert results["agent5_streetview"]["analyzed"] == 3
        assert results.get("agent5_streetview", {}).get("skipped") is not True

    def test_agent5_runs_even_when_prior_agents_accept_all_rows(self):
        """Selecting Agent 5 in Flow Builder should populate A5 columns for accepted rows."""
        a5_calls: list[list[int] | None] = []

        def accept_all_prior(_job_id, candidate_ids, agent_name, _threshold):
            ids = list(candidate_ids or [])
            if agent_name in {"agent2_geocoding", "agent1_address_validator", "agent3_parcel"}:
                return [], {"total": len(ids), "accepted": len(ids), "continued": 0}
            return ids, {"total": len(ids), "accepted": 0, "continued": len(ids)}

        def track_a5(_job_id, address_ids=None, **_kwargs):
            a5_calls.append(list(address_ids or []))
            return {"analyzed": len(address_ids or [])}

        opts = {
            "agent0": {"enabled": False},
            "agent2": {"enabled": True},
            "agent1": {"enabled": True},
            "agent3": {"enabled": False},
            "agent4": {"enabled": False},
            "agent5": {"enabled": True, "street_view": True},
            "agent6": {"enabled": False},
        }

        with (
            patch("data_ingestion.agents.pipeline_runner._classify_addresses", return_value=([], [101, 102], [101, 102])),
            patch("data_ingestion.agents.pipeline_runner._ids_with_real_address", side_effect=lambda _job_id, ids: list(ids or [])),
            patch("data_ingestion.agents.pipeline_runner._apply_resolution_gate", side_effect=accept_all_prior),
            patch("data_ingestion.agents.pipeline_runner.run_geocoding_for_job", return_value=_mock_geocoding_summary()),
            patch("data_ingestion.agents.pipeline_runner.run_agent1_for_job", return_value={}),
            patch("data_ingestion.agents.pipeline_runner.run_agent5_for_job", side_effect=track_a5),
            patch("data_ingestion.agents.pipeline_runner.run_agent4_for_job", return_value={}),
            patch("data_ingestion.agents.pipeline_runner.run_agent6_for_job", return_value={}),
            patch("data_ingestion.agents.pipeline_runner._get_agent_bus", return_value=None),
        ):
            results = run_full_pipeline("job_a5_after_accept", pipeline_options=opts)

        assert a5_calls == [[101, 102]]
        assert results["agent5_streetview"]["analyzed"] == 2
        assert results["agent5_streetview"]["input_rows"] == 2
        assert results["agent5_streetview"]["low_confidence_rows"] == 0

    def test_agent50_runs_only_low_confidence_prior_rows(self):
        a50_calls: list[list[int] | None] = []
        a50_configs: list[object] = []

        def track_a50(_job_id, address_ids=None, **kwargs):
            a50_calls.append(list(address_ids or []))
            a50_configs.append(kwargs.get("config"))
            return {"analyzed": len(address_ids or [])}

        opts = {
            "agent0": {"enabled": False},
            "agent2": {"enabled": False},
            "agent1": {"enabled": False},
            "agent3": {"enabled": False},
            "agent4": {"enabled": False},
            "agent5_0": {
                "enabled": True,
                "street_view": True,
                "paddle_ocr": True,
                "ollama_guidance": False,
                "max_workers": 2,
                "max_iterations": 3,
                "confidence_gate": 90,
            },
            "agent5": {"enabled": False},
            "agent6": {"enabled": False},
        }

        with (
            patch("data_ingestion.agents.pipeline_runner._classify_addresses", return_value=([], [101, 102, 103], [101, 102, 103])),
            patch("data_ingestion.agents.pipeline_runner._ids_with_agent123_confidence_below", return_value=([102, 103], {"total": 3, "eligible": 2, "ignored_high_confidence": 1, "threshold": 90})),
            patch("data_ingestion.agents.pipeline_runner.run_agent50_for_job", side_effect=track_a50),
            patch("data_ingestion.agents.pipeline_runner._get_agent_bus", return_value=None),
        ):
            results = run_full_pipeline("job_a50_low_confidence", pipeline_options=opts)

        assert a50_calls == [[102, 103]]
        assert results["agent5_0_offline_ocr"]["analyzed"] == 2
        assert results["agent5_0_offline_ocr"]["input_rows"] == 2
        assert results["agent5_0_confidence_gate"]["eligible"] == 2
        assert a50_configs[0].ocr_engine == "vision_primary"
        assert a50_configs[0].use_ollama is True

    def test_unified_geocoding_runs_first_before_agent1(self):
        """Agent 2 runs before address validation."""
        call_order: list[str] = []

        def track_geo(job_id, **kwargs):
            call_order.append("agent2")
            return _mock_geocoding_summary(reverse={"geocoded": 2})

        def track_a1(*args, **kwargs):
            call_order.append("agent1")
            return {}

        with patch("data_ingestion.agents.pipeline_runner._classify_addresses",
                   return_value=([1, 2], [], [1, 2])):
            with patch("data_ingestion.agents.pipeline_runner.run_geocoding_for_job", side_effect=track_geo):
                with patch("data_ingestion.agents.pipeline_runner.run_agent1_for_job", side_effect=track_a1):
                    with patch("data_ingestion.agents.pipeline_runner.run_agent3_for_job", return_value={}):
                        with patch("data_ingestion.agents.pipeline_runner.run_agent4_for_job", return_value={}):
                            with patch("data_ingestion.agents.pipeline_runner.run_agent5_for_job", return_value={}):
                                with patch("data_ingestion.agents.pipeline_runner.run_agent6_for_job", return_value={}):
                                    with patch("data_ingestion.agents.pipeline_runner._get_agent_bus", return_value=None):
                                        results = run_full_pipeline("job_coord")

        assert call_order == ["agent2", "agent1"]
        assert results["reverse_geocoder"] == {"geocoded": 2}

    def test_reverse_geocoder_skipped_when_no_coord_only(self):
        results, _ = self._run_with_mocks()
        assert results.get("reverse_geocoder") == {"skipped": True}

    def test_progress_callback_invoked_at_100_at_end(self):
        cb_calls = []
        def cb(done, total, *args, **kwargs):
            cb_calls.append((done, total))

        with patch("data_ingestion.agents.pipeline_runner._classify_addresses",
                   return_value=([], [1], [1])):
            with patch("data_ingestion.agents.pipeline_runner.run_agent1_for_job", return_value={}):
                with patch(
                    "data_ingestion.agents.pipeline_runner.run_geocoding_for_job",
                    return_value=_mock_geocoding_summary(),
                ):
                    with patch("data_ingestion.agents.pipeline_runner.run_agent3_for_job", return_value={}):
                        with patch("data_ingestion.agents.pipeline_runner.run_agent4_for_job", return_value={}):
                            with patch("data_ingestion.agents.pipeline_runner.run_agent5_for_job", return_value={}):
                                with patch("data_ingestion.agents.pipeline_runner.run_agent6_for_job", return_value={}):
                                    with patch("data_ingestion.agents.pipeline_runner._get_agent_bus", return_value=None):
                                        run_full_pipeline("job_cb", progress_callback=cb)

        # The final progress_callback(100, 100) must be in calls
        assert (100, 100) in cb_calls


# ══════════════════════════════════════════════════════════════════════════════
# 3. A2A bus integration
# ══════════════════════════════════════════════════════════════════════════════

    def test_agent5_high_confidence_rows_do_not_run_agent6(self):
        """Agent 6 should receive only rows that continue after the Agent 5 gate."""
        gate_calls = []

        def gate(_job_id, candidate_ids, agent_name, _threshold):
            ids = list(candidate_ids or [])
            gate_calls.append((agent_name, ids))
            if agent_name == "agent5_streetview":
                return [102], {"total": 2, "accepted": 1, "continued": 1}
            if agent_name == "agent6_final":
                return [], {"total": len(ids), "accepted": len(ids), "continued": 0}
            return ids, {"total": len(ids), "accepted": 0, "continued": len(ids)}

        with (
            patch("data_ingestion.agents.pipeline_runner._classify_addresses", return_value=([], [101, 102], [101, 102])),
            patch("data_ingestion.agents.pipeline_runner._ids_with_real_address", side_effect=lambda _job_id, ids: list(ids or [])),
            patch("data_ingestion.agents.pipeline_runner._ids_where_agent5_executed", side_effect=lambda _job_id, ids: list(ids or [])),
            patch("data_ingestion.agents.pipeline_runner._apply_resolution_gate", side_effect=gate),
            patch("data_ingestion.agents.pipeline_runner.run_agent1_for_job", return_value={}),
            patch(
                "data_ingestion.agents.pipeline_runner.run_geocoding_for_job",
                return_value=_mock_geocoding_summary(),
            ),
            patch("data_ingestion.agents.pipeline_runner.run_agent3_for_job", return_value={}),
            patch("data_ingestion.agents.pipeline_runner.run_agent4_for_job", return_value={}),
            patch("data_ingestion.agents.pipeline_runner.run_agent5_for_job", return_value={}) as a5,
            patch("data_ingestion.agents.pipeline_runner.run_agent6_for_job", return_value={}) as a6,
            patch("data_ingestion.agents.pipeline_runner._get_agent_bus", return_value=None),
        ):
            run_full_pipeline("job_a5_gate")

        a5.assert_called_once()
        assert a5.call_args.kwargs["address_ids"] == [101, 102]
        a6.assert_called_once()
        assert a6.call_args.kwargs["address_ids"] == [102]
        assert ("agent5_streetview", [101, 102]) in gate_calls
        assert ("agent6_final", [102]) in gate_calls

    def test_unconfirmed_agent0_discoveries_are_removed_before_later_agents(self):
        """Agent 0-created rows that remain below threshold after A1/A2/A3 are deleted."""
        cleanup_calls: list[list[int]] = []

        def cleanup(_job_id, candidate_ids, **_kwargs):
            cleanup_calls.append(list(candidate_ids or []))
            return {"checked": len(candidate_ids or []), "deleted": 1, "deleted_ids": [202]}

        with (
            patch("data_ingestion.agents.pipeline_runner._classify_addresses", return_value=([], [101, 202], [101, 202])),
            patch("data_ingestion.agents.pipeline_runner._ids_with_real_address", side_effect=lambda _job_id, ids: list(ids or [])),
            patch("data_ingestion.agents.pipeline_runner._ids_where_agent5_executed", side_effect=lambda _job_id, ids: list(ids or [])),
            patch("data_ingestion.agents.pipeline_runner._delete_unconfirmed_agent0_discoveries", side_effect=cleanup),
            patch("data_ingestion.agents.pipeline_runner.run_agent1_for_job", return_value={}),
            patch("data_ingestion.agents.pipeline_runner.run_geocoding_for_job", return_value=_mock_geocoding_summary()),
            patch("data_ingestion.agents.pipeline_runner.run_agent3_for_job", return_value={}),
            patch("data_ingestion.agents.pipeline_runner.run_agent5_for_job", return_value={}) as a5,
            patch("data_ingestion.agents.pipeline_runner.run_agent4_for_job", return_value={}) as a4,
            patch("data_ingestion.agents.pipeline_runner.run_agent6_for_job", return_value={}),
            patch("data_ingestion.agents.pipeline_runner._get_agent_bus", return_value=None),
        ):
            results = run_full_pipeline("job_agent0_cleanup")

        assert cleanup_calls == [[101, 202]]
        assert results["agent0_cleanup"]["deleted_ids"] == [202]
        a5.assert_called_once()
        assert a5.call_args.kwargs["address_ids"] == [101]
        a4.assert_called_once()
        assert a4.call_args.kwargs["address_ids"] == [101]

    def test_agent4_runs_before_agent6_so_final_has_building_evidence(self):
        call_order: list[str] = []

        def keep_all(_job_id, candidate_ids, agent_name, _threshold):
            return list(candidate_ids or []), {
                "total": len(candidate_ids or []),
                "accepted": 0,
                "continued": len(candidate_ids or []),
            }

        with (
            patch("data_ingestion.agents.pipeline_runner._classify_addresses", return_value=([], [101], [101])),
            patch("data_ingestion.agents.pipeline_runner._ids_with_real_address", side_effect=lambda _job_id, ids: list(ids or [])),
            patch("data_ingestion.agents.pipeline_runner._ids_where_agent5_executed", side_effect=lambda _job_id, ids: list(ids or [])),
            patch("data_ingestion.agents.pipeline_runner._apply_resolution_gate", side_effect=keep_all),
            patch("data_ingestion.agents.pipeline_runner.run_agent1_for_job", return_value={}),
            patch("data_ingestion.agents.pipeline_runner.run_geocoding_for_job", return_value=_mock_geocoding_summary()),
            patch("data_ingestion.agents.pipeline_runner.run_agent3_for_job", return_value={}),
            patch("data_ingestion.agents.pipeline_runner.run_agent5_for_job", side_effect=lambda *a, **k: call_order.append("agent5") or {}),
            patch("data_ingestion.agents.pipeline_runner.run_agent4_for_job", side_effect=lambda *a, **k: call_order.append("agent4") or {}),
            patch("data_ingestion.agents.pipeline_runner.run_agent6_for_job", side_effect=lambda *a, **k: call_order.append("agent6") or {}),
            patch("data_ingestion.agents.pipeline_runner._get_agent_bus", return_value=None),
        ):
            run_full_pipeline("job_order", pipeline_options={"agent0": {"enabled": False}})

        assert call_order == ["agent4", "agent5", "agent6"]


class TestPipelineA2ABusIntegration:
    def _run_with_connected_bus(self):
        mock_bus = MagicMock()
        mock_bus.connected = True

        with patch("data_ingestion.agents.pipeline_runner._classify_addresses",
                   return_value=([], [1, 2], [1, 2])):
            with patch("data_ingestion.agents.pipeline_runner.run_agent1_for_job", return_value={"processed": 2}):
                with patch(
                    "data_ingestion.agents.pipeline_runner.run_geocoding_for_job",
                    return_value=_mock_geocoding_summary(forward={"geocoded": 2}),
                ):
                    with patch("data_ingestion.agents.pipeline_runner.run_agent3_for_job", return_value={"found": 1}):
                        with patch("data_ingestion.agents.pipeline_runner.run_agent4_for_job", return_value={}):
                            with patch("data_ingestion.agents.pipeline_runner.run_agent5_for_job", return_value={}):
                                with patch("data_ingestion.agents.pipeline_runner.run_agent6_for_job", return_value={"high": 1}):
                                    with patch("data_ingestion.agents.pipeline_runner._ids_where_agent5_executed", side_effect=lambda _job_id, ids: list(ids or [])):
                                        with patch("data_ingestion.agents.pipeline_runner._get_agent_bus", return_value=mock_bus):
                                            run_full_pipeline("job_bus")
        return mock_bus

    def test_pipeline_start_notified(self):
        bus = self._run_with_connected_bus()
        bus.notify_pipeline_start.assert_called_once_with("job_bus", 2)

    def test_pipeline_complete_notified(self):
        bus = self._run_with_connected_bus()
        bus.notify_pipeline_complete.assert_called_once()

    def test_bus_disconnected_on_finish(self):
        bus = self._run_with_connected_bus()
        bus.disconnect.assert_called_once()

    def test_agent_start_events_published(self):
        bus = self._run_with_connected_bus()
        agent_start_calls = [c[0][1] for c in bus.notify_agent_start.call_args_list]
        # At minimum: agent1_address_validator, agent2_geocoding, ..., agent6_final
        assert "agent1_address_validator" in agent_start_calls
        assert "agent2_geocoding" in agent_start_calls
        assert "agent6_final" in agent_start_calls

    def test_agent_complete_events_published(self):
        bus = self._run_with_connected_bus()
        agent_complete_calls = [c[0][1] for c in bus.notify_agent_complete.call_args_list]
        assert "agent1_address_validator" in agent_complete_calls
        assert "agent6_final" in agent_complete_calls

    def test_broadcast_progress_published_for_each_agent(self):
        bus = self._run_with_connected_bus()
        # broadcast_progress is called after agents 1-5; agent6 goes straight to
        # notify_pipeline_complete without a separate broadcast_progress call
        assert bus.broadcast_progress.call_count >= 5

    def test_pipeline_works_without_bus(self):
        """When bus=None, pipeline runs identically with no messaging."""
        with patch("data_ingestion.agents.pipeline_runner._classify_addresses",
                   return_value=([], [1], [1])):
            with patch("data_ingestion.agents.pipeline_runner.run_agent1_for_job", return_value={}):
                with patch(
                    "data_ingestion.agents.pipeline_runner.run_geocoding_for_job",
                    return_value=_mock_geocoding_summary(),
                ):
                    with patch("data_ingestion.agents.pipeline_runner.run_agent3_for_job", return_value={}):
                        with patch("data_ingestion.agents.pipeline_runner.run_agent4_for_job", return_value={}):
                            with patch("data_ingestion.agents.pipeline_runner.run_agent5_for_job", return_value={}):
                                with patch("data_ingestion.agents.pipeline_runner.run_agent6_for_job", return_value={}):
                                    with patch("data_ingestion.agents.pipeline_runner._get_agent_bus", return_value=None):
                                        results = run_full_pipeline("job_no_bus")

        assert "agent6_final" in results  # pipeline completed normally

    def test_bus_unavailable_does_not_break_pipeline(self):
        """_get_agent_bus returning None must not raise in any pipeline stage."""
        with patch("data_ingestion.agents.pipeline_runner._classify_addresses",
                   return_value=([10], [20], [10, 20])):
            with patch("data_ingestion.agents.pipeline_runner.run_agent1_for_job", return_value={}):
                with patch(
                    "data_ingestion.agents.pipeline_runner.run_geocoding_for_job",
                    return_value=_mock_geocoding_summary(reverse={"geocoded": 1}),
                ):
                    with patch("data_ingestion.agents.pipeline_runner.run_agent3_for_job", return_value={}):
                        with patch("data_ingestion.agents.pipeline_runner.run_agent4_for_job", return_value={}):
                            with patch("data_ingestion.agents.pipeline_runner.run_agent5_for_job", return_value={}):
                                with patch("data_ingestion.agents.pipeline_runner.run_agent6_for_job", return_value={}):
                                    with patch("data_ingestion.agents.pipeline_runner._get_agent_bus", return_value=None):
                                        results = run_full_pipeline("job_mixed")

        assert "reverse_geocoder" in results
        assert "agent1_address_validator" in results


# ══════════════════════════════════════════════════════════════════════════════
# 4. _get_agent_bus helper
# ══════════════════════════════════════════════════════════════════════════════

from data_ingestion.agents.pipeline_runner import _get_agent_bus


class TestGetAgentBus:
    # AgentBus is imported lazily inside _get_agent_bus(); patch the source module.
    _PATCH_TARGET = "data_ingestion.messaging.agent_bus.AgentBus"

    def test_returns_none_when_rabbitmq_unavailable(self):
        with patch(self._PATCH_TARGET) as mock_cls:
            mock_bus = MagicMock()
            mock_bus.connected = False
            mock_bus.connect.return_value = mock_bus
            mock_cls.return_value = mock_bus
            result = _get_agent_bus()
        assert result is None

    def test_returns_bus_when_connected(self):
        with patch(self._PATCH_TARGET) as mock_cls:
            mock_bus = MagicMock()
            mock_bus.connected = True
            mock_bus.connect.return_value = mock_bus
            mock_cls.return_value = mock_bus
            result = _get_agent_bus()
        assert result is not None

    def test_returns_none_on_import_error(self):
        with patch("data_ingestion.messaging.agent_bus.AgentBus",
                   side_effect=ImportError("pika not installed")):
            result = _get_agent_bus()
        assert result is None

    def test_returns_none_on_connection_exception(self):
        with patch(self._PATCH_TARGET) as mock_cls:
            mock_bus = MagicMock()
            mock_bus.connect.side_effect = Exception("broker unreachable")
            mock_cls.return_value = mock_bus
            result = _get_agent_bus()
        assert result is None

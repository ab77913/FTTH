"""Map pipeline stage names to dashboard agent slots and update agent status."""

from __future__ import annotations

import time
from typing import Any

# Dashboard agent order (see AGENT_DEFINITIONS in api_server.py / worker tasks).
STAGE_TO_AGENT_IDX: dict[str, int] = {
    "agent0_house_discovery": 0,
    "agent2_geocoding": 1,
    "agent1_address_validator": 2,
    "agent3_parcel": 3,
    # Matches AGENT_DEFINITIONS order and pipeline execution order.
    "agent4_building": 4,
    "agent5_0_offline_ocr": 5,
    "agent5_streetview": 6,
    "agent6_final": 7,
    "agent7_neighborhood_discovery": 8,
}

RESULT_STAGE_TO_AGENT_IDX: dict[str, int] = {
    **STAGE_TO_AGENT_IDX,
    "reverse_geocoder": 1,
}

# Pipeline execution order (matches dashboard agent order for A4 → A5).
_PIPELINE_STAGE_SEQUENCE: tuple[str, ...] = (
    "agent0_house_discovery",
    "agent2_geocoding",
    "agent1_address_validator",
    "agent3_parcel",
    "agent4_building",
    "agent5_0_offline_ocr",
    "agent5_streetview",
    "agent6_final",
)

_AGENT_IDX_TO_STAGE: dict[int, str] = {idx: stage for stage, idx in STAGE_TO_AGENT_IDX.items()}


def _pipeline_stage_rank(stage_name: str | None) -> int | None:
    if not stage_name:
        return None
    try:
        return _PIPELINE_STAGE_SEQUENCE.index(stage_name)
    except ValueError:
        return None


def _stamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def apply_stage_progress(
    agents: list[dict[str, Any]],
    *,
    stage_name: str | None,
    stage_done: int,
    stage_total: int,
    total_records: int,
) -> int | None:
    """
    Update agent rows for a pipeline stage tick.

    Completion uses pipeline execution order so earlier stages are marked done
    when a later stage starts.
    """
    if not stage_name or stage_name not in STAGE_TO_AGENT_IDX:
        return None

    current_idx = STAGE_TO_AGENT_IDX[stage_name]
    current_rank = _pipeline_stage_rank(stage_name)
    if current_idx >= len(agents) or current_rank is None:
        return current_idx

    now = _stamp()
    for idx, agent in enumerate(agents):
        agent_stage = _AGENT_IDX_TO_STAGE.get(idx)
        agent_rank = _pipeline_stage_rank(agent_stage)
        if (
            agent_rank is not None
            and agent_rank < current_rank
            and agent.get("status") != "completed"
        ):
            agent["status"] = "completed"
            agent["progress"] = 100
            agent["records_processed"] = agent.get("records_total") or total_records
            agent["completed_at"] = agent.get("completed_at") or now
            agent["errors"] = []
        elif idx == current_idx:
            agent["status"] = "running"
            agent["records_processed"] = stage_done
            agent["records_total"] = max(stage_total, 1)
            agent["progress"] = int((stage_done / max(stage_total, 1)) * 100)
            agent["started_at"] = agent.get("started_at") or now

    return current_idx


def mark_current_agent_failed(
    agents: list[dict[str, Any]],
    current_idx: int,
    error: str,
) -> None:
    """Mark only the active agent slot as failed; leave completed agents unchanged."""
    if not (0 <= current_idx < len(agents)):
        return
    agent = agents[current_idx]
    if agent.get("status") != "completed":
        agent["status"] = "failed"
        agent["errors"] = [error]
        agent["completed_at"] = agent.get("completed_at") or _stamp()


def finalize_agents_from_results(
    agents: list[dict[str, Any]],
    pipeline_results: dict[str, Any],
    *,
    total_records: int,
) -> None:
    """Apply per-stage summaries after a successful pipeline run."""
    now = _stamp()
    for stage_name, stage_summary in pipeline_results.items():
        idx = RESULT_STAGE_TO_AGENT_IDX.get(stage_name)
        if idx is None or idx >= len(agents) or not isinstance(stage_summary, dict):
            continue
        agent = agents[idx]
        agent["status"] = "completed"
        agent["progress"] = 100
        agent["completed_at"] = now
        agent["summary"] = stage_summary
        agent["errors"] = []
        total_proc = stage_summary.get("total", 0)
        agent["records_processed"] = total_proc or agent.get("records_total") or total_records
        agent["records_total"] = total_proc or agent.get("records_total") or total_records

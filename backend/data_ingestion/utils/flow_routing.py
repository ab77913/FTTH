"""Flow routing and confidence-based filtering logic."""

from __future__ import annotations

import logging
from uuid import UUID

from data_ingestion.database.db import session_scope
from data_ingestion.database.models import AgentResult
from data_ingestion.utils.pipeline_options import normalize_flow_agent_name

logger = logging.getLogger(__name__)

# Legacy alias kept for saved flow templates; agents always read Address rows from DB.
_DATABASE_SOURCE_ALIASES = frozenset({"database", "raw_input", "db"})


def normalize_data_source(data_source: str | None) -> str:
    """Map legacy ``raw_input`` to ``database`` (stored Address rows, not upload files)."""
    value = (data_source or "database").strip().lower()
    if value in _DATABASE_SOURCE_ALIASES:
        return "database"
    return value


def apply_confidence_filter(
    job_id: UUID | str,
    agent_name: str,
    address_ids: list[int],
    threshold_1: int | None,
    threshold_2: int | None,
) -> tuple[list[int], list[int]]:
    """
    Split addresses by confidence thresholds.
    
    Returns:
        (passed_ids, failed_ids_for_next_agent)
        
    Logic:
        - If confidence >= threshold_1 → passed
        - If threshold_2 exists and threshold_1 > confidence >= threshold_2 → passed (intermediate)
        - If confidence < threshold_2 or (no threshold_2 and confidence < threshold_1) → failed
    """
    if not threshold_1 or not address_ids:
        # All pass if no threshold set
        return address_ids, []
    
    with session_scope() as session:
        results = session.query(AgentResult).filter(
            AgentResult.address_id.in_(address_ids),
            AgentResult.agent_name == agent_name,
        ).all()
        
        passed, failed = [], []
        
        for res in results:
            conf = res.confidence_score or 0
            
            if conf >= threshold_1:
                # Above primary threshold - passed
                passed.append(res.address_id)
            elif threshold_2 and conf >= threshold_2:
                # Above secondary threshold - intermediate pass
                passed.append(res.address_id)
            else:
                # Below all thresholds - route to next agent
                failed.append(res.address_id)
        
        logger.debug(
            f"Confidence filter for {agent_name} (t1={threshold_1}, t2={threshold_2}): "
            f"{len(passed)} passed, {len(failed)} failed"
        )
        
        return passed, failed


def get_input_ids_for_agent(
    job_id: UUID | str,
    agent_name: str,
    data_source: str,
    address_ids: list[int],
    prior_agent_outputs: dict[str, list[int]],
) -> list[int]:
    """
    Determine which address IDs to process for a given agent based on data source.
    
    Args:
        job_id: Job UUID
        agent_name: Name of agent to run
        data_source: Where to get input ("database", "agent{N}_output", "all_agents";
            legacy "raw_input" is treated as "database")
        address_ids: All available address IDs
        prior_agent_outputs: Dict mapping agent_name → passed_ids from prior execution
        
    Returns:
        List of address IDs to process
    """
    source = normalize_data_source(data_source)

    if source == "database":
        # All stored Address rows for this job (never re-reads CSV/KMZ files).
        return address_ids
    
    elif source == "all_agents":
        # Agent 6 (finalization) uses all agents' outputs
        # This is handled specially in the pipeline runner
        return address_ids
    
    else:
        # Extract from prior agent output (e.g., "agent2_output" → prior_agent_outputs["agent2"])
        prior_agent = source.replace("_output", "")
        ids = prior_agent_outputs.get(prior_agent, [])
        
        if not ids:
            logger.warning(
                f"No output from {prior_agent} for {agent_name} "
                f"(data_source={data_source}); using empty list"
            )
        
        return ids


def build_flow_routing_map(
    flow_config: dict,
) -> dict[str, dict]:
    """
    Build a routing map from flow config for quick lookup.
    
    Returns:
        {
            "agent2_geocoding": {
                "enabled": True,
                "data_source": "database",
                "threshold_1": 60,
                "threshold_2": 40,
                "order": 1
            },
            ...
        }
    """
    routing_map = {}
    
    for agent_cfg in flow_config.get("agents", []):
        agent_name = normalize_flow_agent_name(agent_cfg.get("agent_name"))
        if not agent_name:
            continue
        
        thresholds = agent_cfg.get("confidence_thresholds", {})
        routing_map[agent_name] = {
            "enabled": agent_cfg.get("enabled", True),
            "data_source": normalize_data_source(agent_cfg.get("data_source", "database")),
            "threshold_1": thresholds.get("threshold_1"),
            "threshold_2": thresholds.get("threshold_2"),
            "order": agent_cfg.get("order", 999),
        }
    
    return routing_map


def get_agents_in_execution_order(flow_config: dict) -> list[str]:
    """
    Get list of enabled agent names sorted by execution order.
    
    Returns:
        ["agent2_geocoding", "agent1_address_validator", ...]
    """
    agents = [
        normalize_flow_agent_name(a.get("agent_name"))
        for a in flow_config.get("agents", [])
        if a.get("enabled", True) and a.get("agent_name")
    ]
    
    # Sort by order field
    agents_with_order = [
        (a, next((ag.get("order", 999) for ag in flow_config.get("agents", []) if normalize_flow_agent_name(ag.get("agent_name")) == a), 999))
        for a in agents
    ]
    
    agents_with_order.sort(key=lambda x: x[1])
    
    return [a for a, _ in agents_with_order]

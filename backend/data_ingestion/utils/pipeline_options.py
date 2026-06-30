"""
Per-agent pipeline options selected in the UI and passed through the orchestrator.
"""
from __future__ import annotations

from typing import Any

from data_ingestion.utils.geocode_options import (
    AGENT2_OPTION_KEYS,
    AGENT2_OPTION_LABELS,
    DEFAULT_AGENT2_OPTIONS,
    normalize_agent2_options,
)

DEFAULT_AGENT0_OPTIONS: dict[str, Any] = {
    "enabled": True,
    "reverse_geocode": True,
    "grid_step": 0.00025,
    "max_candidates_per_polygon": 0,
    "max_candidates_per_job": 0,
    "dedup_distance_m": 20,
    "multi_unit_suffixing": True,
    "max_units_per_base_address": 4,
    "validate_discovered": False,
    "validate_with_smarty": True,
    "validate_with_regrid": False,
    "validation_threshold": 90,
    "exclude_discovered_from_agent1": True,
}

DEFAULT_AGENT1_OPTIONS: dict[str, bool] = {
    "enabled": True,
    "smarty": True,
    "melissa": False,
    "use_cache": True,
}

DEFAULT_AGENT3_OPTIONS: dict[str, bool] = {
    "enabled": True,
    "regrid": True,
    "tigerline": True,
    "nominatim": True,
}

DEFAULT_AGENT5_OPTIONS: dict[str, Any] = {
    "enabled": True,
    "analysis_mode": "hybrid",
    "street_view": True,
    "satellite_fallback": True,
    "azure_vision": True,
    "gpt_vision": True,
    "llm_provider": "online",
    "ollama_vision_model": "qwen2.5vl:latest",
    "fast_mode": True,
}

DEFAULT_AGENT50_OPTIONS: dict[str, Any] = {
    "enabled": False,
    "street_view": True,
    "paddle_ocr": True,
    "ocr_engine": "vision_primary",
    "ollama_guidance": True,
    "max_workers": 4,
    "max_iterations": 6,
    "confidence_gate": 90,
}

DEFAULT_AGENT4_OPTIONS: dict[str, Any] = {
    "enabled": True,
    "building_footprint": True,
    "building_provider": "microsoft",
}

DEFAULT_AGENT2_DISTANCE_OPTIONS: dict[str, Any] = {
    "coord_match_threshold_m": 100,
    "coord_mismatch_warn_m": 500,
}

DEFAULT_AGENT6_OPTIONS: dict[str, bool] = {
    "enabled": True,
    "ftth_synthesis": True,
}

DEFAULT_AGENT7_OPTIONS: dict[str, Any] = {
    "enabled": False,
    "search_distance_m": 60,
    "samples_per_address": 4,
    "max_candidates_per_job": 500,
    "concurrency": 8,
    "dedup_distance_m": 25,
    "validation_provider": "reverse_rooftop",
    "validation_threshold": 90,
    "use_validation_cache": False,
    "microsoft_building_enrichment": True,
}

DEFAULT_PIPELINE_OPTIONS: dict[str, dict[str, Any]] = {
    "agent0": dict(DEFAULT_AGENT0_OPTIONS),
    "agent2": {"enabled": True, **DEFAULT_AGENT2_OPTIONS, **DEFAULT_AGENT2_DISTANCE_OPTIONS},
    "agent1": dict(DEFAULT_AGENT1_OPTIONS),
    "agent3": dict(DEFAULT_AGENT3_OPTIONS),
    "agent4": dict(DEFAULT_AGENT4_OPTIONS),
    "agent5_0": dict(DEFAULT_AGENT50_OPTIONS),
    "agent5": dict(DEFAULT_AGENT5_OPTIONS),
    "agent6": dict(DEFAULT_AGENT6_OPTIONS),
    "agent7": dict(DEFAULT_AGENT7_OPTIONS),
}

FLOW_AGENT_TO_PIPELINE_ID: dict[str, str] = {
    "agent0_house_discovery": "agent0",
    "agent2_geocoding": "agent2",
    "agent1_geocoding": "agent2",
    "agent1_reverse_geocoding": "agent2",
    "geocoding": "agent2",
    "reverse_geocoder": "agent2",
    "agent1_address_validator": "agent1",
    "address_validator": "agent1",
    "agent2_address_validation": "agent1",
    "agent2_address_validator": "agent1",
    "address_validation": "agent1",
    "smarty": "agent1",
    "melissa": "agent1",
    "agent3_parcel": "agent3",
    "agent4_building": "agent4",
    "agent5_0_offline_ocr": "agent5_0",
    "agent50_offline_ocr": "agent5_0",
    "agent5_offline_ocr": "agent5_0",
    "agent5_streetview": "agent5",
    "agent6_finalization": "agent6",
    "agent6_final": "agent6",
    "agent7_neighborhood_discovery": "agent7",
}

FLOW_AGENT_CANONICAL_NAME: dict[str, str] = {
    "agent0_house_discovery": "agent0_house_discovery",
    "agent2_geocoding": "agent2_geocoding",
    "agent1_geocoding": "agent2_geocoding",
    "agent1_reverse_geocoding": "agent2_geocoding",
    "geocoding": "agent2_geocoding",
    "reverse_geocoder": "agent2_geocoding",
    "agent1_address_validator": "agent1_address_validator",
    "address_validator": "agent1_address_validator",
    "agent2_address_validation": "agent1_address_validator",
    "agent2_address_validator": "agent1_address_validator",
    "address_validation": "agent1_address_validator",
    "smarty": "agent1_address_validator",
    "melissa": "agent1_address_validator",
    "agent3_parcel": "agent3_parcel",
    "agent4_building": "agent4_building",
    "agent5_0_offline_ocr": "agent5_0_offline_ocr",
    "agent50_offline_ocr": "agent5_0_offline_ocr",
    "agent5_offline_ocr": "agent5_0_offline_ocr",
    "agent5_streetview": "agent5_streetview",
    "agent6_finalization": "agent6_finalization",
    "agent6_final": "agent6_final",
    "agent7_neighborhood_discovery": "agent7_neighborhood_discovery",
}


def normalize_flow_agent_name(agent_name: Any) -> str:
    """Return the canonical pipeline stage name for saved flow aliases."""
    return FLOW_AGENT_CANONICAL_NAME.get(str(agent_name or "").strip(), str(agent_name or "").strip())

PIPELINE_OPTION_LABELS: dict[str, dict[str, str]] = {
    "agent0": {
        "enabled": "Run Agent 0",
        "reverse_geocode": "Reverse geocode discovered candidates",
        "grid_step": "Grid step",
        "max_candidates_per_polygon": "Max candidates per polygon",
        "max_candidates_per_job": "Max candidates per job",
        "dedup_distance_m": "Dedup distance meters",
        "multi_unit_suffixing": "Generate multi-unit suffix addresses",
        "max_units_per_base_address": "Max generated units per base address",
        "validate_discovered": "Validate discovered addresses inside Agent 0",
        "validate_with_smarty": "Use Smarty for Agent 0 validation",
        "validate_with_regrid": "Use Regrid for Agent 0 validation",
        "validation_threshold": "Agent 0 validation threshold",
        "exclude_discovered_from_agent1": "Do not send Agent 0 records to normal Agent 2 validation",
    },
    "agent2": {
        "enabled": "Run Agent 1",
        **AGENT2_OPTION_LABELS,
        "coord_match_threshold_m": "Coordinate match threshold meters",
        "coord_mismatch_warn_m": "Coordinate warning threshold meters",
    },
    "agent1": {
        "enabled": "Run Agent 2",
        "smarty": "Smarty Streets validation",
        "melissa": "Melissa validation (dual-provider arbitration)",
        "use_cache": "Use address validation cache",
    },
    "agent3": {
        "enabled": "Run Agent 3",
        "regrid": "Regrid parcel API",
        "tigerline": "TIGER / census county lookup",
        "nominatim": "Nominatim county fallback",
    },
    "agent5": {
        "enabled": "Run Agent 5",
        "analysis_mode": "Agent 5 mode",
        "street_view": "Google Street View imagery",
        "satellite_fallback": "Satellite / ESRI aerial imagery",
        "azure_vision": "Azure Vision OCR & classification",
        "gpt_vision": "Vision LLM guidance",
        "llm_provider": "Vision LLM provider",
        "ollama_vision_model": "Offline Ollama vision model",
        "fast_mode": "Fast mode (parallel rows, no API sleep; full scoring path preserved)",
    },
    "agent5_0": {
        "enabled": "Run Agent 5-0",
        "street_view": "Google Street View imagery",
        "paddle_ocr": "PaddleOCR validation",
        "ocr_engine": "OCR engine",
        "ollama_guidance": "Offline Ollama guidance",
        "max_workers": "Parallel workers",
        "max_iterations": "Max image search iterations",
        "confidence_gate": "Only run when Agent 1/2/3 confidence is below this percent",
    },
    "agent4": {
        "enabled": "Run Agent 4",
        "building_footprint": "Building footprint enrichment",
        "building_provider": "Building footprint provider",
    },
    "agent6": {
        "enabled": "Run Agent 6",
        "ftth_synthesis": "Final FTTH priority synthesis",
    },
    "agent7": {
        "enabled": "Run Agent 7",
        "search_distance_m": "Neighborhood search distance meters",
        "samples_per_address": "Samples per Final address",
        "max_candidates_per_job": "Maximum Agent 7 candidates per job",
        "concurrency": "Parallel reverse-geocoding requests",
        "dedup_distance_m": "Final-address coordinate deduplication meters",
        "validation_provider": "Agent 7 validation provider (smarty, regrid, reverse_rooftop)",
        "validation_threshold": "Agent 7 validation threshold",
        "use_validation_cache": "Use cache for Agent 7 Smarty validation",
        "microsoft_building_enrichment": "Enrich new records with Microsoft building footprints",
    },
}

PIPELINE_AGENT_META: list[dict[str, str]] = [
    {"id": "agent0", "name": "Agent 0: House Discovery", "description": "Discover households inside KML/KMZ polygons"},
    {"id": "agent2", "name": "Agent 1: Geocoding", "description": "Reverse + forward geocoding"},
    {"id": "agent1", "name": "Agent 2: Address Validation", "description": "Smarty + Melissa"},
    {"id": "agent3", "name": "Agent 3: Parcel & Land Use", "description": "Regrid / TIGER / Nominatim"},
    {"id": "agent4", "name": "Agent 4: Building", "description": "Footprint classification"},
    {"id": "agent5_0", "name": "Agent 5-0: Offline OCR", "description": "Ollama vision OCR + PaddleOCR fallback for low-confidence rows"},
    {"id": "agent5", "name": "Agent 5: Street View", "description": "Imagery analysis"},
    {"id": "agent6", "name": "Agent 6: FTTH Final", "description": "Final suitability score"},
    {"id": "agent7", "name": "Agent 7: Neighborhood Discovery", "description": "Find polygon addresses missing from Final columns"},
]


def _coerce_option_value(default: Any, value: Any) -> Any:
    if isinstance(default, bool):
        if isinstance(value, str):
            return value.strip().lower() not in {"0", "false", "no", "off"}
        return bool(value)
    if isinstance(default, int) and not isinstance(default, bool):
        try:
            return max(0, int(value)) if default == 0 else max(1, int(value))
        except (TypeError, ValueError):
            return default
    if isinstance(default, float):
        try:
            return max(0.00001, float(value))
        except (TypeError, ValueError):
            return default
    return value


def _merge_agent_defaults(agent_id: str, raw: dict[str, Any] | None) -> dict[str, Any]:
    defaults = DEFAULT_PIPELINE_OPTIONS.get(agent_id, {})
    merged = dict(defaults)
    if raw:
        for key, value in raw.items():
            if key in merged:
                merged[key] = _coerce_option_value(defaults[key], value)
    return merged


def normalize_pipeline_options(raw: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Merge UI/API payload with defaults."""
    opts = {agent_id: dict(DEFAULT_PIPELINE_OPTIONS[agent_id]) for agent_id in DEFAULT_PIPELINE_OPTIONS}
    if not raw:
        base = normalize_agent2_options(None)
        base["enabled"] = True
        opts["agent2"] = base
        return opts

    if "agent2_options" in raw and isinstance(raw["agent2_options"], dict):
        section = raw["agent2_options"]
    elif "agent2" in raw and isinstance(raw["agent2"], dict):
        section = raw["agent2"]
    else:
        section = None

    if section is not None:
        enabled = _coerce_option_value(True, section.get("enabled", True))
        opts["agent2"] = normalize_agent2_options(section)
        for key, default in DEFAULT_AGENT2_DISTANCE_OPTIONS.items():
            if key in section:
                opts["agent2"][key] = _coerce_option_value(default, section.get(key))
            else:
                opts["agent2"][key] = default
        opts["agent2"]["enabled"] = enabled
    else:
        opts["agent2"] = normalize_agent2_options(None)
        opts["agent2"].update(DEFAULT_AGENT2_DISTANCE_OPTIONS)
        opts["agent2"]["enabled"] = True

    for agent_id in ("agent0", "agent1", "agent3", "agent4", "agent5_0", "agent5", "agent6", "agent7"):
        section = raw.get(agent_id)
        if isinstance(section, dict):
            opts[agent_id] = _merge_agent_defaults(agent_id, section)

    return opts


def apply_flow_config_to_pipeline_options(
    options: dict[str, dict[str, Any]] | None,
    flow_config: dict[str, Any] | None,
) -> dict[str, dict[str, bool]]:
    """Overlay Flow Builder agent enablement and sub-options onto pipeline options."""
    opts = normalize_pipeline_options(options)
    if not isinstance(flow_config, dict):
        return opts

    agents = flow_config.get("agents")
    if not isinstance(agents, list):
        return opts

    for agent_cfg in agents:
        if not isinstance(agent_cfg, dict):
            continue
        agent_name = normalize_flow_agent_name(agent_cfg.get("agent_name"))
        agent_id = FLOW_AGENT_TO_PIPELINE_ID.get(str(agent_name))
        if not agent_id or agent_id not in opts:
            continue

        enabled = _coerce_option_value(True, agent_cfg.get("enabled", True))
        opts[agent_id]["enabled"] = enabled

        # Flow builder stores agent sub-checkboxes in agent_cfg.pipeline_options.
        raw_pipeline_opts = agent_cfg.get("pipeline_options")
        if isinstance(raw_pipeline_opts, dict):
            defaults = DEFAULT_PIPELINE_OPTIONS.get(agent_id, {})
            for key, value in raw_pipeline_opts.items():
                if key in defaults:
                    opts[agent_id][key] = _coerce_option_value(defaults[key], value)

        # Keep enabled in sync if present in nested options.
        opts[agent_id]["enabled"] = enabled

    if opts.get("agent1", {}).get("enabled") and not (opts["agent1"].get("smarty") or opts["agent1"].get("melissa")):
        opts["agent1"]["smarty"] = True
    if opts.get("agent2", {}).get("enabled") and not any(opts["agent2"].get(key) for key in AGENT2_OPTION_KEYS):
        opts["agent2"]["google_geocoding"] = True

    return opts


def agent_enabled(options: dict[str, dict[str, Any]] | None, agent_id: str) -> bool:
    section = (options or {}).get(agent_id) or DEFAULT_PIPELINE_OPTIONS.get(agent_id, {})
    return _coerce_option_value(True, section.get("enabled", True))


def agent1_provider_mode(agent1_opts: dict[str, Any] | None) -> str:
    opts = _merge_agent_defaults("agent1", agent1_opts)
    if opts.get("smarty") and opts.get("melissa"):
        return "dual_provider"
    if opts.get("smarty"):
        return "smarty_only"
    return "none"


def any_agent_enabled(options: dict[str, dict[str, Any]] | None) -> bool:
    opts = normalize_pipeline_options(options)
    return any(
        agent_enabled(opts, agent_id)
        for agent_id in DEFAULT_PIPELINE_OPTIONS
    )

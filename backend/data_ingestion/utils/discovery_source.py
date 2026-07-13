"""Helpers for describing how newly discovered addresses were geocoded."""
from __future__ import annotations

from typing import Any

_PROVIDER_LABELS = {
    "google_reverse_geocode": "Google Reverse",
    "google": "Google",
    "google_geocoding": "Google",
    "attom": "ATTOM",
    "smarty": "Smarty",
    "regrid": "Regrid",
    "osm": "OSM",
    "microsoft": "Microsoft",
    "reverse_geocoder": "Reverse Geocoder",
    "reverse_rooftop": "Google Reverse",
}

_AGENT_LABELS = {
    "agent0": "Agent 0",
    "agent0_house_discovery": "Agent 0",
    "house_discovery": "Agent 0",
    "agent7": "Agent 7",
    "agent7_neighborhood_discovery": "Agent 7",
    "neighborhood_discovery": "Agent 7",
}


def discovery_agent_label(agent: Any) -> str:
    key = str(agent or "").strip().lower()
    if not key:
        return ""
    return _AGENT_LABELS.get(key, str(agent or "").strip())


def discovery_provider_label(provider: Any) -> str:
    key = str(provider or "").strip().lower().replace(" ", "_")
    if not key:
        return ""
    if key in _PROVIDER_LABELS:
        return _PROVIDER_LABELS[key]
    return str(provider or "").strip().replace("_", " ").title()


def format_new_address_discovery_summary(
    *,
    agent: Any = "",
    provider: Any = "",
    location_type: Any = "",
) -> str:
    """Return a compact label like 'Agent 7 via Google Reverse (ROOFTOP)'."""
    agent_label = discovery_agent_label(agent)
    provider_label = discovery_provider_label(provider)
    loc = str(location_type or "").strip().upper()

    if agent_label and provider_label and loc:
        return f"{agent_label} via {provider_label} ({loc})"
    if agent_label and provider_label:
        return f"{agent_label} via {provider_label}"
    if agent_label and loc:
        return f"{agent_label} ({loc})"
    if provider_label and loc:
        return f"{provider_label} ({loc})"
    if agent_label:
        return agent_label
    if provider_label:
        return provider_label
    if loc:
        return loc
    return ""


def _geocode_like_payload(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _first_agent7_detail(agent7_data: dict[str, Any] | None) -> dict[str, Any]:
    agent7_data = agent7_data or {}
    details = agent7_data.get("new_address_details") or []
    if details and isinstance(details[0], dict):
        return details[0]
    return {}


def resolve_new_address_discovery(
    meta: dict[str, Any] | None = None,
    *,
    agent0_data: dict[str, Any] | None = None,
    agent7_data: dict[str, Any] | None = None,
) -> tuple[str, str, str]:
    """Return (agent, provider, location_type) for a discovered/new address row."""
    meta = meta or {}
    agent0_data = agent0_data or {}
    agent7_data = agent7_data or {}

    agent = str(meta.get("new_address_discovery_agent") or "").strip()
    provider = str(meta.get("new_address_discovery_provider") or "").strip()
    location_type = str(meta.get("new_address_discovery_location_type") or "").strip().upper()

    if meta.get("agent7_discovered") is True:
        agent = agent or "agent7"
        detail = _first_agent7_detail(agent7_data)
        final = meta.get("final_resolution") if isinstance(meta.get("final_resolution"), dict) else {}
        provider = (
            provider
            or detail.get("provider")
            or meta.get("final_provider")
            or final.get("provider")
            or ""
        )
        location_type = (
            location_type
            or detail.get("location_type")
            or meta.get("final_location_type")
            or meta.get("agent7_reverse_geocode_location_type")
            or final.get("location_type")
            or ""
        ).upper()
    elif meta.get("agent0_discovered") is True:
        agent = agent or "agent0"
        geocode = _geocode_like_payload(agent0_data.get("geocode"))
        provider = provider or agent0_data.get("source") or geocode.get("provider") or ""
        location_type = (
            location_type
            or agent0_data.get("location_type")
            or geocode.get("location_type")
            or ""
        ).upper()
    elif str(meta.get("merge_status") or "").strip().lower() == "new":
        if meta.get("agent0_source") == "house_discovery":
            agent = agent or "agent0"
        elif str(meta.get("source_format") or "").strip().lower() == "agent7":
            agent = agent or "agent7"

    return agent, provider, location_type


def build_new_address_discovery_metadata(
    *,
    agent: str,
    provider: str = "",
    location_type: str = "",
    base_reason: str = "",
) -> dict[str, str]:
    """Return metadata fields to persist on a newly discovered address row."""
    agent_key = str(agent or "").strip().lower()
    provider_key = str(provider or "").strip()
    location_key = str(location_type or "").strip().upper()
    summary = format_new_address_discovery_summary(
        agent=agent_key,
        provider=provider_key,
        location_type=location_key,
    )
    reason = base_reason
    if summary:
        reason = f"{base_reason} ({summary})" if base_reason else summary
    return {
        "new_address_discovery_agent": agent_key,
        "new_address_discovery_provider": provider_key,
        "new_address_discovery_location_type": location_key,
        "new_address_discovery_source": summary,
        "merge_reason": reason,
    }


def new_address_discovery_ui_fields(
    meta: dict[str, Any] | None = None,
    *,
    agent0_data: dict[str, Any] | None = None,
    agent7_data: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Return table/export fields describing how a new address was discovered."""
    meta = meta or {}
    is_new = (
        meta.get("agent0_discovered") is True
        or meta.get("agent7_discovered") is True
        or str(meta.get("merge_status") or "").strip().lower() == "new"
        or str(meta.get("rule_status") or "").strip().lower() == "new"
    )
    if not is_new:
        return {
            "new_address_discovery_agent": "",
            "new_address_discovery_provider": "",
            "new_address_discovery_location_type": "",
            "new_address_discovery_source": "",
        }

    agent, provider, location_type = resolve_new_address_discovery(
        meta,
        agent0_data=agent0_data,
        agent7_data=agent7_data,
    )
    summary = (
        str(meta.get("new_address_discovery_source") or "").strip()
        or format_new_address_discovery_summary(
            agent=agent,
            provider=provider,
            location_type=location_type,
        )
    )
    return {
        "new_address_discovery_agent": discovery_agent_label(agent),
        "new_address_discovery_provider": discovery_provider_label(provider),
        "new_address_discovery_location_type": location_type,
        "new_address_discovery_source": summary,
    }

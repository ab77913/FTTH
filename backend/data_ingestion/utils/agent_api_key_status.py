"""Derive per-agent API key health labels for UI/export columns."""
from __future__ import annotations

import os
from typing import Any


def _optional_str(value: Any) -> str:
    return str(value or "").strip()


def _env_has_key(*names: str) -> bool:
    for name in names:
        if _optional_str(os.environ.get(name)):
            return True
    return False


def _google_maps_key_configured() -> bool:
    return _env_has_key("GOOGLE_MAPS_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GEOCODING_API_KEY")


def _status_from_provider_block(block: dict[str, Any] | None) -> str:
    if not isinstance(block, dict) or not block:
        return ""
    if block.get("ok") is True:
        return "Working"
    api_status = _optional_str(block.get("api_status")).upper()
    error = _optional_str(block.get("error_message") or block.get("google_error_message")).upper()
    if api_status in {"REQUEST_DENIED", "INVALID_REQUEST", "OVER_QUERY_LIMIT"}:
        return "Not Working"
    if error.startswith("REQUEST DENIED") or "API KEY" in error:
        return "Not Working"
    if block.get("ok") is False and (api_status or error):
        return "Not Working"
    return ""


def _status_from_google_api_response(meta: dict[str, Any] | None) -> str:
    if not isinstance(meta, dict) or not meta:
        return ""
    status = _optional_str(meta.get("status")).upper()
    if status == "OK":
        return "Working"
    if status in {"NO_GOOGLE_KEY", "MISSING_KEY", ""}:
        return "Missing Key" if not _google_maps_key_configured() else "Not Working"
    if status in {"REQUEST_DENIED", "INVALID_REQUEST", "OVER_QUERY_LIMIT", "ERROR"}:
        return "Not Working"
    if status in {"ZERO_RESULTS", "NOT_FOUND"}:
        return "Working"
    return ""


def _streetview_fields_from_meta(meta: dict[str, Any] | None) -> dict[str, str]:
    meta = meta if isinstance(meta, dict) else {}
    sv = meta.get("streetview_metadata")
    if not isinstance(sv, dict):
        sv = meta.get("streetview_api") if isinstance(meta.get("streetview_api"), dict) else {}
    if not sv and isinstance(meta.get("google street view addresses"), dict):
        gsv = meta["google street view addresses"]
        sv = {
            "status": gsv.get("api_status") or gsv.get("status"),
            "pano_id": gsv.get("pano_id"),
            "date": gsv.get("date"),
        }
    status = _optional_str(sv.get("status"))
    return {
        "streetview_api_status": status,
        "streetview_pano_id": _optional_str(sv.get("pano_id")),
        "streetview_date": _optional_str(sv.get("date")),
        "api_key_status": _status_from_google_api_response(sv),
    }


def _streetview_fields_from_agent(result: dict[str, Any] | None) -> dict[str, str]:
    result = result if isinstance(result, dict) else {}
    sv = result.get("streetview_metadata")
    if not isinstance(sv, dict):
        sv = {}
    fields = {
        "streetview_api_status": _optional_str(sv.get("status")),
        "streetview_pano_id": _optional_str(sv.get("pano_id")),
        "streetview_date": _optional_str(sv.get("date")),
        "api_key_status": _status_from_google_api_response(sv),
    }
    if not fields["api_key_status"] and result.get("status") == "no_imagery":
        reason = _optional_str(result.get("reason")).upper()
        if "NO_GOOGLE_KEY" in reason or not _google_maps_key_configured():
            fields["api_key_status"] = "Missing Key"
        else:
            fields["api_key_status"] = "Not Working"
    if not fields["api_key_status"] and result.get("images_fetched"):
        fields["api_key_status"] = "Working"
    return fields


def persist_streetview_api_summary(meta: dict[str, Any], sv_meta: dict[str, Any] | None) -> dict[str, Any]:
    """Store compact Street View Metadata API payload under raw_metadata."""
    meta = dict(meta or {})
    if not isinstance(sv_meta, dict) or not sv_meta:
        return meta
    block = {
        "status": _optional_str(sv_meta.get("status")),
        "pano_id": _optional_str(sv_meta.get("pano_id")),
        "date": _optional_str(sv_meta.get("date")),
        "copyright": _optional_str(sv_meta.get("copyright")),
        "error_message": _optional_str(sv_meta.get("error_message")),
        "location": sv_meta.get("location") if isinstance(sv_meta.get("location"), dict) else {},
        "api_key_status": _status_from_google_api_response(sv_meta),
    }
    meta["streetview_api"] = {k: v for k, v in block.items() if v not in ("", {}, None)}
    return meta


def agent_api_key_ui_fields(
    meta: dict[str, Any] | None,
    *,
    a0: dict[str, Any] | None = None,
    a1: Any = None,
    a2: dict[str, Any] | None = None,
    a3: dict[str, Any] | None = None,
    a4: dict[str, Any] | None = None,
    a50: dict[str, Any] | None = None,
    a5: dict[str, Any] | None = None,
    a6: dict[str, Any] | None = None,
    a7: dict[str, Any] | None = None,
) -> dict[str, Any]:
    meta = meta if isinstance(meta, dict) else {}
    a0, a2, a3, a4, a50, a5, a6, a7 = a0 or {}, a2 or {}, a3 or {}, a4 or {}, a50 or {}, a5 or {}, a6 or {}, a7 or {}

    reverse_block = meta.get("reverse_geocoding") if isinstance(meta.get("reverse_geocoding"), dict) else {}
    google_block = meta.get("google_geocoding") if isinstance(meta.get("google_geocoding"), dict) else {}
    regrid_block = meta.get("regrid") if isinstance(meta.get("regrid"), dict) else {}

    # Agent 0 — Google geocoding + optional ATTOM discovery
    agent0_status = ""
    if reverse_block or google_block or a0:
        agent0_status = _status_from_provider_block(reverse_block) or _status_from_provider_block(google_block)
    if not agent0_status and a0.get("status") == "skipped" and not _google_maps_key_configured():
        agent0_status = "Missing Key"
    if not agent0_status and _google_maps_key_configured() and (reverse_block or google_block):
        agent0_status = "Working"
    if not agent0_status:
        agent0_status = "Missing Key" if not _google_maps_key_configured() else ""

    # Agent 1 — Smarty / Melissa address validation
    agent1_status = ""
    if a1 is not None:
        exc = _optional_str(getattr(a1, "exception_reason", "") or "")
        if exc and "api" in exc.lower():
            agent1_status = "Not Working"
        elif getattr(a1, "validation_status", None):
            has_smarty = _env_has_key("SMARTY_AUTH_ID") and _env_has_key("SMARTY_AUTH_TOKEN")
            has_melissa = _env_has_key("MELISSA_LICENSE_KEY")
            if has_smarty or has_melissa:
                agent1_status = "Working"
            else:
                agent1_status = "Missing Key"
    elif _env_has_key("SMARTY_AUTH_ID", "SMARTY_AUTH_TOKEN") or _env_has_key("MELISSA_LICENSE_KEY"):
        agent1_status = "Missing Key"

    # Agent 2 (UI) — forward/reverse geocoding
    agent2_status = _status_from_provider_block(google_block) or _status_from_provider_block(reverse_block)
    google_status = _optional_str(a2.get("google_status")).upper()
    if google_status in {"REQUEST_DENIED", "INVALID_REQUEST", "OVER_QUERY_LIMIT"}:
        agent2_status = "Not Working"
    if not agent2_status and a2.get("status") in {"geocoded", "reverse_geocoded", "success"}:
        agent2_status = "Working"
    if not agent2_status and not _google_maps_key_configured():
        agent2_status = "Missing Key"
    if not agent2_status and _google_maps_key_configured():
        agent2_status = "Working" if (google_block or reverse_block or a2) else ""

    # Agent 3 — parcel / Regrid
    agent3_status = _status_from_provider_block(regrid_block)
    if not agent3_status and a3.get("status") in {"found", "ok", "success"}:
        agent3_status = "Working"
    if not agent3_status and a3.get("status") in {"error", "failed", "not_found"}:
        agent3_status = "Not Working"

    # Agent 4 — Microsoft footprints (no key) or ATTOM
    provider = _optional_str(
        a4.get("building_provider") or a4.get("footprint_source") or a4.get("source_agent")
    ).lower()
    if "attom" in provider:
        agent4_status = "Working" if _env_has_key("ATTOM_API_KEY") and a4.get("structure_type") not in {None, "", "UNRESOLVED"} else ""
        if not agent4_status:
            agent4_status = "Missing Key" if not _env_has_key("ATTOM_API_KEY") else "Not Working"
    elif a4.get("structure_type") and a4.get("structure_type") != "UNRESOLVED":
        agent4_status = "Working"
    elif a4:
        agent4_status = "Working" if a4.get("status") not in {"failed", "error"} else "Not Working"
    else:
        agent4_status = ""

    sv50 = _streetview_fields_from_agent(a50)
    if not sv50["api_key_status"]:
        sv50.update({k: v for k, v in _streetview_fields_from_meta(meta).items() if v and not sv50.get(k)})

    sv5 = _streetview_fields_from_agent(a5)
    if not sv5["api_key_status"]:
        sv5_meta = _streetview_fields_from_meta(meta)
        for key, val in sv5_meta.items():
            if val and not sv5.get(key):
                sv5[key] = val

    agent50_status = sv50["api_key_status"]
    if not agent50_status and not _google_maps_key_configured():
        agent50_status = "Missing Key"

    agent5_status = sv5["api_key_status"]
    if not agent5_status:
        if _env_has_key("AZURE_VISION_KEY", "AZURE_VISION_ENDPOINT") or _google_maps_key_configured():
            if a5.get("status") in {"analyzed", "success"} or a5.get("confidence"):
                agent5_status = "Working"
            elif not _google_maps_key_configured() and not _env_has_key("AZURE_VISION_KEY"):
                agent5_status = "Missing Key"
        else:
            agent5_status = "Missing Key"

    agent6_status = "N/A"
    if a6.get("status") in {"failed", "error"}:
        agent6_status = "Not Working"

    agent7_status = ""
    if a7.get("new_address_count") is not None or a7.get("status"):
        agent7_status = "Working" if _google_maps_key_configured() else "Missing Key"

    return {
        "agent0_api_key_status": agent0_status,
        "agent1_api_key_status": agent1_status,
        "agent2_api_key_status": agent2_status,
        "agent3_api_key_status": agent3_status,
        "agent4_api_key_status": agent4_status,
        "agent50_api_key_status": agent50_status,
        "agent5_api_key_status": agent5_status,
        "agent6_api_key_status": agent6_status,
        "agent7_api_key_status": agent7_status,
        "agent50_streetview_api_status": sv50["streetview_api_status"],
        "agent50_streetview_pano_id": sv50["streetview_pano_id"],
        "agent50_streetview_date": sv50["streetview_date"],
        "agent5_streetview_api_status": sv5["streetview_api_status"],
        "agent5_streetview_pano_id": sv5["streetview_pano_id"],
        "agent5_streetview_date": sv5["streetview_date"],
    }

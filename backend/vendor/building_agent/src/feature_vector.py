from __future__ import annotations

from typing import Any


NUMERIC_MEDIANS = {
    "area_m2": 220.0,
    "elongation_ratio": 1.4,
    "distance_m": 7.5,
    "unit_count": 1.0,
    "year_built": 1980.0,
}

DPV_TYPE_ENCODING = {
    "unknown": 0,
    "single": 1,
    "multi": 2,
    "commercial": 3,
    "mixed": 4,
}

OWNER_TYPE_ENCODING = {
    "unknown": 0,
    "individual": 1,
    "llc": 2,
    "corporate": 3,
    "government": 4,
    "nonprofit": 5,
}

OSM_BUILDING_FLAGS = {
    "unknown": 0,
    "yes": 1,
    "house": 1,
    "detached": 1,
    "residential": 2,
    "apartments": 3,
    "commercial": 4,
    "retail": 4,
    "office": 4,
    "industrial": 5,
    "school": 6,
    "hospital": 7,
    "government": 8,
}


def _numeric(value: Any, name: str) -> float:
    if value is None or value == "":
        return float(NUMERIC_MEDIANS[name])
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(NUMERIC_MEDIANS[name])


def _category(value: Any) -> str:
    if value is None or value == "":
        return "unknown"
    return str(value).strip().lower() or "unknown"


def build_features(agent_output: dict[str, Any], external_data: dict[str, Any]) -> dict[str, float]:
    dpv_type = _category(external_data.get("dpv_type"))
    owner_type = _category(external_data.get("owner_type"))
    osm_building_tag = _category(external_data.get("osm_building_tag"))

    return {
        "area_m2": _numeric(agent_output.get("area_m2"), "area_m2"),
        "elongation_ratio": _numeric(agent_output.get("elongation_ratio"), "elongation_ratio"),
        "distance_m": _numeric(agent_output.get("distance_m"), "distance_m"),
        "unit_count": _numeric(external_data.get("unit_count"), "unit_count"),
        "dpv_type": float(DPV_TYPE_ENCODING.get(dpv_type, DPV_TYPE_ENCODING["unknown"])),
        "owner_type": float(OWNER_TYPE_ENCODING.get(owner_type, OWNER_TYPE_ENCODING["unknown"])),
        "year_built": _numeric(external_data.get("year_built"), "year_built"),
        "building_type_flag": float(OSM_BUILDING_FLAGS.get(osm_building_tag, OSM_BUILDING_FLAGS["unknown"])),
    }

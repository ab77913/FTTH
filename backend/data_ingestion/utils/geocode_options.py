"""
Agent 2 geocoding mode options — selected in the UI and passed through the pipeline.
"""
from __future__ import annotations

from typing import Any

AGENT2_OPTION_KEYS = (
    "reverse_geocoder",
    "coord_validation",
    "google_geocoding",
    "osm_geocoding",
    "street_interpolation",
)

DEFAULT_AGENT2_OPTIONS: dict[str, bool] = {
    "reverse_geocoder": True,
    "coord_validation": True,
    "google_geocoding": True,
    "osm_geocoding": True,
    "street_interpolation": True,
}

AGENT2_OPTION_LABELS: dict[str, str] = {
    "reverse_geocoder": "Reverse geocoder (coords → address)",
    "coord_validation": "Address vs coordinate validation",
    "google_geocoding": "Google forward geocoding",
    "osm_geocoding": "OSM / Nominatim forward geocoding",
    "street_interpolation": "Street centerline interpolation",
}


def normalize_agent2_options(raw: dict[str, Any] | None) -> dict[str, bool]:
    """Merge *raw* with defaults; unknown keys are ignored."""
    opts = dict(DEFAULT_AGENT2_OPTIONS)
    if not raw:
        return opts
    for key in AGENT2_OPTION_KEYS:
        if key in raw:
            opts[key] = bool(raw[key])
    return opts


def any_forward_enabled(options: dict[str, bool] | None) -> bool:
    opts = normalize_agent2_options(options)
    return opts["google_geocoding"] or opts["osm_geocoding"] or opts["street_interpolation"]


def any_agent2_enabled(options: dict[str, bool] | None) -> bool:
    opts = normalize_agent2_options(options)
    return any(opts[k] for k in AGENT2_OPTION_KEYS)

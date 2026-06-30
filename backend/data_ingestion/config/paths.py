"""Repository and package path helpers (stable after backend/ layout)."""

from pathlib import Path

# This file lives at backend/data_ingestion/config/paths.py
PACKAGE_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = Path(__file__).resolve().parents[3]

# Third-party agent code vendored under backend/vendor/
VENDOR_ROOT = BACKEND_ROOT / "vendor"
ADDRESS_VALIDATION_VENDOR = VENDOR_ROOT / "address_validation_agent"
BUILDING_AGENT_VENDOR = VENDOR_ROOT / "building_agent"
STREET_VIEW_VENDOR = VENDOR_ROOT / "street_view_analysis"

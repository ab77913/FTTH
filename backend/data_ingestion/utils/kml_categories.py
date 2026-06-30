from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from data_ingestion.schemas import CanonicalAddressRecord

HOUSEHOLD_CATEGORIES = frozenset({"household", "households"})
ADDRESS_CATEGORIES = frozenset({"address", "addresses", "premise", "premises"})
SERVICEABLE_CATEGORIES = HOUSEHOLD_CATEGORIES | ADDRESS_CATEGORIES
KMZ_SOURCE_FORMATS = frozenset({"kml", "kmz"})


def _normalize_category(value: str | None) -> str:
    return (value or "").strip().lower()


def is_household_kml_record(
    *,
    category: str | None = None,
    folder_path: str | None = None,
) -> bool:
    """True when a KML/KMZ placemark belongs to a HOUSEHOLD folder."""
    labels: list[str] = []
    norm_category = _normalize_category(category)
    if norm_category:
        labels.append(norm_category)
    if folder_path:
        labels.extend(
            _normalize_category(part)
            for part in folder_path.split("/")
            if part.strip()
        )
    return any(label in SERVICEABLE_CATEGORIES for label in labels)


def is_transient_kmz_record(record: "CanonicalAddressRecord") -> bool:
    """KMZ rows merged into CSV are not stored separately."""
    meta = record.raw_metadata or {}
    if meta.get("file_role") != "geospatial" and str(meta.get("source_format") or "").lower() not in KMZ_SOURCE_FORMATS:
        suffix = Path(record.source_file or "").suffix.lower()
        if suffix not in {".kml", ".kmz"}:
            return False
    return bool(meta.get("merge_absorbed_into_csv"))


def records_for_storage(records: list["CanonicalAddressRecord"]) -> list["CanonicalAddressRecord"]:
    """Persist tabular rows and KMZ rows that did not merge into CSV."""
    return [record for record in records if not is_transient_kmz_record(record)]

import re
from pathlib import Path
from typing import List, Optional
import pandas as pd
from src.models.schemas import RawAddressRecord

ADDRESS_RE = re.compile(r"\b\d{1,6}\s+[A-Za-z0-9][A-Za-z0-9 .#\-/]+\b")
STATE_RE = re.compile(r"^[A-Z]{2}$")
ZIP_RE = re.compile(r"\b\d{5}(?:-\d{4})?\b")

ADDRESS_CANDIDATE_COLUMNS = [
    "raw_address", "address", "Address", "ADDRESS", "street_address", "full_address",
    "secondary_number", "street_suffix", "street", "ns1:name2", "name", "Name"
]


def _clean(value) -> Optional[str]:
    if value is None or pd.isna(value):
        return None
    value = str(value).strip()
    if not value or value.lower() in {"nan", "none", "null"}:
        return None
    return value


def _looks_like_address(value: str) -> bool:
    if not value:
        return False
    if "{" in value and "}" in value:
        return False
    if value.startswith("A") and value[1:].isdigit():
        return False
    return bool(ADDRESS_RE.search(value))


def _find_address(row: pd.Series) -> str:
    for col in ADDRESS_CANDIDATE_COLUMNS:
        if col in row.index:
            value = _clean(row.get(col))
            if value and _looks_like_address(value):
                return value.upper()
    for value in row.values:
        value = _clean(value)
        if value and _looks_like_address(value):
            return value.upper()
    return ""


def _find_state(row: pd.Series) -> Optional[str]:
    for col in ["state", "STATE", "st", "csa_status"]:
        value = _clean(row.get(col)) if col in row.index else None
        if value and STATE_RE.match(value.upper()):
            return value.upper()
    for col in ["data_source", "network_node", "noc_plan_desc"]:
        value = _clean(row.get(col)) if col in row.index else None
        if value:
            m = re.search(r"\b([A-Z]{2})\d{3,}|\b([A-Z]{2})[A-Z0-9]{4,}", value.upper())
            if m:
                return (m.group(1) or m.group(2)).upper()
    return None


def _find_zip(row: pd.Series) -> Optional[str]:
    for col in ["zip", "ZIP", "zip_code", "postal_code"]:
        value = _clean(row.get(col)) if col in row.index else None
        if value:
            m = ZIP_RE.search(value)
            if m:
                return m.group(0)[:5]
    return None


def _find_city(row: pd.Series) -> Optional[str]:
    for col in ["city", "CITY", "municipality"]:
        value = _clean(row.get(col)) if col in row.index else None
        if value and not value.isdigit() and len(value) > 2:
            return value.upper()
    return None


def extract_csv(path: str | Path) -> List[RawAddressRecord]:
    path = Path(path)
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    records: List[RawAddressRecord] = []
    for idx, row in df.iterrows():
        raw_address = _find_address(row)
        if not raw_address:
            continue
        records.append(
            RawAddressRecord(
                source_file=path.name,
                source_type="csv",
                row_id=str(idx),
                address_id=_clean(row.get("address_id")) or _clean(row.get("Address ID")),
                raw_address=raw_address,
                city=_find_city(row),
                state=_find_state(row),
                zip_code=_find_zip(row),
                latitude=float(row["lat"]) if "lat" in row and _clean(row.get("lat")) else None,
                longitude=float(row["lon"]) if "lon" in row and _clean(row.get("lon")) else None,
                network_node=_clean(row.get("network_node")),
                terminal_id=_clean(row.get("terminal_id")),
                qualified_desc=_clean(row.get("qualified_desc")),
                extra={k: _clean(v) for k, v in row.to_dict().items() if _clean(v) is not None},
            )
        )
    return records

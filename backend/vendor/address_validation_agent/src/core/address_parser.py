import re
from typing import Optional, Tuple
from src.models.schemas import RawAddressRecord, CanonicalAddress

SUFFIX_MAP = {
    "STREET": "ST", "ST": "ST", "ROAD": "RD", "RD": "RD", "DRIVE": "DR", "DR": "DR",
    "AVENUE": "AVE", "AVE": "AVE", "COURT": "CT", "CT": "CT", "LANE": "LN", "LN": "LN",
    "WAY": "WAY", "CIRCLE": "CIR", "CIR": "CIR", "BOULEVARD": "BLVD", "BLVD": "BLVD",
    "PARKWAY": "PKWY", "PKWY": "PKWY", "PLACE": "PL", "PL": "PL"
}
UNIT_TOKENS = {"APT", "APARTMENT", "UNIT", "STE", "SUITE", "BLDG", "BUILDING", "#"}


def clean_text(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    value = re.sub(r"[,]+", " ", str(value).upper())
    value = re.sub(r"\s+", " ", value).strip()
    return value or None


def parse_street(raw_address: str) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str], Optional[str]]:
    raw = clean_text(raw_address) or ""
    parts = raw.split()
    if not parts:
        return None, None, None, None, None
    house = parts[0] if parts[0].isdigit() else None
    rest = parts[1:] if house else parts
    unit_type = None
    unit_number = None
    for i, token in enumerate(rest):
        if token in UNIT_TOKENS:
            unit_type = "APT" if token in {"APARTMENT"} else token
            unit_number = rest[i + 1] if i + 1 < len(rest) else None
            rest = rest[:i]
            break
    suffix = None
    if rest and rest[-1] in SUFFIX_MAP:
        suffix = SUFFIX_MAP[rest[-1]]
        street_name = " ".join(rest[:-1]) or None
    else:
        street_name = " ".join(rest) or None
    return house, street_name, suffix, unit_type, unit_number


def canonicalize(record: RawAddressRecord) -> CanonicalAddress:
    house, street, suffix, unit_type, unit_number = parse_street(record.raw_address)
    pieces = []
    if house: pieces.append(house)
    if street: pieces.append(street)
    if suffix: pieces.append(suffix)
    if unit_type: pieces.append(unit_type)
    if unit_number: pieces.append(unit_number)
    if record.city: pieces.append(record.city.upper())
    if record.state: pieces.append(record.state.upper())
    if record.zip_code: pieces.append(str(record.zip_code)[:5])
    return CanonicalAddress(
        source_file=record.source_file,
        row_id=record.row_id,
        address_id=record.address_id,
        raw_address=record.raw_address,
        house_number=house,
        street_name=street,
        street_suffix=suffix,
        unit_type=unit_type,
        unit_number=unit_number,
        city=record.city.upper() if record.city else None,
        state=record.state.upper() if record.state else None,
        zip_code=str(record.zip_code)[:5] if record.zip_code else None,
        country=(record.country or "US").upper() if record.country else "US",
        latitude=record.latitude,
        longitude=record.longitude,
        network_node=record.network_node,
        terminal_id=record.terminal_id,
        normalized_full_address=" ".join(pieces),
    )

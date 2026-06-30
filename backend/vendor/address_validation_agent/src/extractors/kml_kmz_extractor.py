import html
import re
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from xml.etree import ElementTree as ET
from src.models.schemas import RawAddressRecord

# Require letter as first char after house number — filters junk like "5 1/8", "3 B-12"
ADDRESS_RE = re.compile(r"\b\d{1,6}\s+[A-Za-z][A-Za-z0-9 .#\-/]+\b")
COORD_RE = re.compile(r"(-?\d+\.\d+),(-?\d+\.\d+)(?:,[-?\d.]*)?")
HTML_TAG_RE = re.compile(r"<[^>]+>")


def _read_kml_text(path: Path) -> str:
    if path.suffix.lower() == ".kmz":
        with zipfile.ZipFile(path, "r") as z:
            kml_files = [n for n in z.namelist() if n.lower().endswith(".kml")]
            if not kml_files:
                return ""
            return z.read(kml_files[0]).decode("utf-8", errors="ignore")
    return path.read_text(encoding="utf-8", errors="ignore")


def _strip_ns(tag: str) -> str:
    return tag.split("}")[-1]


def _text(elem, child_name: str) -> Optional[str]:
    for child in list(elem):
        if _strip_ns(child.tag) == child_name:
            return (child.text or "").strip()
    return None


def _coords(elem) -> Tuple[Optional[float], Optional[float]]:
    all_text = " ".join([t.strip() for t in elem.itertext() if t and t.strip()])
    m = COORD_RE.search(all_text)
    if not m:
        return None, None
    lon, lat = float(m.group(1)), float(m.group(2))
    return lat, lon


def _strip_html(text: str) -> str:
    """Remove HTML tags and decode entities, collapsing whitespace."""
    if not text:
        return text
    text = HTML_TAG_RE.sub(" ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _parse_extended_data(pm) -> Dict[str, str]:
    """
    Extract <Data name="..."><value>text</value></Data> entries from
    the Placemark's ExtendedData element.
    Keys are normalised to lowercase with spaces replaced by underscores.
    """
    fields: Dict[str, str] = {}
    for elem in pm.iter():
        if _strip_ns(elem.tag) == "Data":
            attr = elem.get("name", "")
            for child in elem:
                if _strip_ns(child.tag) == "value" and child.text:
                    val = child.text.strip()
                    if val:
                        fields[attr.lower().replace(" ", "_")] = val
    return fields


def _parse_description_html(desc_html: str) -> Dict[str, str]:
    """
    Parse 'Key: Value' pairs from KML CDATA description blocks.
    Splits on <br> tags, then strips remaining HTML.
    """
    fields: Dict[str, str] = {}
    lines = re.split(r"<br\s*/?>", desc_html, flags=re.IGNORECASE)
    for line in lines:
        plain = _strip_html(line).strip()
        if ":" in plain:
            key, _, val = plain.partition(":")
            key = key.strip().lower().replace(" ", "_")
            val = val.strip()
            if key and val and len(val) < 300:
                fields[key] = val
    return fields


def _find_address(text: str) -> str:
    """Return the first valid address match from plain text, or empty string."""
    if not text:
        return ""
    m = ADDRESS_RE.search(text)
    return m.group(0).upper() if m else ""


def _parse_kml_root(kml_text: str) -> ET.Element:
    """Parse KML XML, falling back to namespace-stripped parsing on failure."""
    try:
        return ET.fromstring(kml_text.encode("utf-8"))
    except ET.ParseError:
        # Some KML files use namespace-prefixed tags (gx:, atom:, etc.) without
        # declaring all prefixes in scope — strip them before retrying.
        stripped = re.sub(r"<(/?)[A-Za-z][\w-]*:([\w-]+)", r"<\1\2", kml_text)
        stripped = re.sub(r"\s+[A-Za-z][\w-]*:[A-Za-z][\w-]*=", " ", stripped)
        return ET.fromstring(stripped.encode("utf-8"))


def extract_kml_kmz(path: str | Path) -> List[RawAddressRecord]:
    path = Path(path)
    kml = _read_kml_text(path)
    if not kml:
        return []
    root = _parse_kml_root(kml)
    records: List[RawAddressRecord] = []
    placemarks = [e for e in root.iter() if _strip_ns(e.tag) == "Placemark"]

    for idx, pm in enumerate(placemarks):
        name = _text(pm, "name")
        desc = _text(pm, "description")
        lat, lon = _coords(pm)

        # Structured field extraction — ExtendedData wins over description HTML
        extended = _parse_extended_data(pm)
        desc_fields = _parse_description_html(desc) if desc else {}
        fields: Dict[str, str] = {**desc_fields, **extended}

        city     = (fields.get("city") or "").strip().upper() or None
        state    = (fields.get("state") or "").strip().upper() or None
        raw_zip  = fields.get("zip") or fields.get("zip_code") or fields.get("postal_code") or ""
        zip_code = raw_zip.strip()[:5] if raw_zip.strip() else None
        address_id = fields.get("address_id")

        # Address extraction priority:
        #   1. <name> — usually the clean address label in FTTH KML files
        #   2. ExtendedData "address" field
        #   3. Stripped description text — last resort
        raw_address = ""

        if name:
            raw_address = _find_address(name)

        if not raw_address:
            ext_addr = fields.get("address", "")
            if ext_addr:
                raw_address = _find_address(ext_addr) or ext_addr.upper().strip()

        if not raw_address and desc:
            raw_address = _find_address(_strip_html(desc))

        if not raw_address:
            continue

        records.append(
            RawAddressRecord(
                source_file=path.name,
                source_type=path.suffix.lower().replace(".", ""),
                row_id=str(idx),
                address_id=address_id,
                raw_address=raw_address,
                city=city,
                state=state,
                zip_code=zip_code,
                latitude=lat,
                longitude=lon,
                extra={"placemark_name": name, "description": desc, **fields},
            )
        )
    return records

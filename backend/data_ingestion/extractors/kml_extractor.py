from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from data_ingestion.extractors.base import BaseExtractor
from data_ingestion.schemas import RawExtractedRecord
from data_ingestion.utils.address_match import looks_like_street_address
from data_ingestion.utils.strings import clean_value, normalize_header, parse_kml_coordinate


KML_NS = {"kml": "http://www.opengis.net/kml/2.2"}
NETWORK_LINK_TIMEOUT_SECONDS = 20
NETWORK_LINK_MAX_DEPTH = 2
KML_UPLOAD_POINT_CATEGORIES = frozenset({"household", "households", "address", "addresses"})


def _is_upload_address_category(category: str | None, folder_path: str | None) -> bool:
    labels = [category or ""]
    labels.extend(part for part in str(folder_path or "").split("/") if part)
    return any(normalize_header(label) in KML_UPLOAD_POINT_CATEGORIES for label in labels)


def _copy_first_field(raw: dict[str, Any], target: dict[str, Any], output_key: str, *aliases: str) -> None:
    value = _lookup_placemark_field(raw, *aliases)
    if value:
        target[output_key] = value

def _strip_namespace(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _first_text(element: ET.Element, xpath: str) -> str | None:
    found = element.find(xpath, KML_NS)
    if found is None or found.text is None:
        return None
    return clean_value(found.text)


def _extract_extended_data(placemark: ET.Element) -> dict[str, Any]:
    data: dict[str, Any] = {}

    for data_el in placemark.findall(".//kml:ExtendedData/kml:Data", KML_NS):
        name = data_el.attrib.get("name")
        value = _first_text(data_el, "kml:value")
        if name and value is not None:
            data[name] = value

    for simple_data in placemark.findall(".//kml:ExtendedData//kml:SimpleData", KML_NS):
        name = simple_data.attrib.get("name")
        value = clean_value(simple_data.text)
        if name and value is not None:
            data[name] = value

    return data


def _parse_description_fields(description: str | None) -> dict[str, str]:
    """Parse HTML description into key-value pairs if it contains structured data."""
    if not description:
        return {}
    # Split on <br>, <br/>, <br /> etc and also newlines
    parts = re.split(r'<br\s*/?>|\n', description, flags=re.IGNORECASE)
    fields: dict[str, str] = {}
    for part in parts:
        # Strip HTML tags
        text = re.sub(r'<[^>]+>', '', part).strip()
        if not text:
            continue
        # Try to split as key: value or key = value
        if ':' in text:
            key, _, val = text.partition(':')
            key = key.strip()
            val = val.strip()
            if key and val and len(key) < 40:
                fields[key] = val
    return fields


def _find_coordinates(placemark: ET.Element) -> str | None:
    coordinates = placemark.find(".//kml:Point/kml:coordinates", KML_NS)
    if coordinates is not None and coordinates.text:
        return clean_value(coordinates.text)

    # fallback: first coordinate set from any geometry type
    coordinates = placemark.find(".//kml:coordinates", KML_NS)
    if coordinates is not None and coordinates.text:
        return clean_value(coordinates.text)

    return None


def _network_link_hrefs(root: ET.Element) -> list[str]:
    hrefs: list[str] = []
    for href_el in root.findall(".//kml:NetworkLink/kml:Link/kml:href", KML_NS):
        href = clean_value(href_el.text)
        if href:
            hrefs.append(str(href))
    for href_el in root.findall(".//kml:NetworkLink/kml:Url/kml:href", KML_NS):
        href = clean_value(href_el.text)
        if href:
            hrefs.append(str(href))
    return hrefs


def _fetch_network_link(href: str) -> bytes:
    parsed = urlparse(href)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"Unsupported NetworkLink scheme: {parsed.scheme or '(none)'}")
    request = Request(href, headers={"User-Agent": "FTTH-PRODUCTION-KML/1.0"})
    with urlopen(request, timeout=NETWORK_LINK_TIMEOUT_SECONDS) as response:
        return response.read()


def _lookup_placemark_field(raw: dict[str, Any], *names: str) -> str | None:
    normalized = {normalize_header(key): clean_value(value) for key, value in raw.items()}
    for name in names:
        value = normalized.get(normalize_header(name))
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _resolve_placemark_street_address(
    name: str | None,
    raw_data: dict[str, Any],
    description: str | None,
) -> str | None:
    """Best street-line address from ExtendedData, name, or description."""
    for candidate in (
        _lookup_placemark_field(raw_data, "Address", "address", "Street Address", "street address"),
        clean_value(name),
        _lookup_placemark_field(raw_data, "Street", "street"),
    ):
        if candidate and looks_like_street_address(str(candidate)):
            return str(candidate).strip()
    if description:
        for line in re.split(r"<br\s*/?>", description, flags=re.IGNORECASE):
            plain = re.sub(r"<[^>]+>", " ", line or "")
            plain = re.sub(r"\s+", " ", plain).strip()
            if ":" in plain:
                _, _, value = plain.partition(":")
                plain = value.strip()
            if looks_like_street_address(plain):
                return plain
    return None


class KMLExtractor(BaseExtractor):
    """Extract Placemarks from KML files."""

    def extract(self, file_path: str | Path) -> list[RawExtractedRecord]:
        path = Path(file_path)
        return self.extract_from_bytes(path.read_bytes(), source_file=path.name)

    def extract_from_bytes(
        self,
        content: bytes,
        *,
        source_file: str,
        _network_depth: int = 0,
    ) -> list[RawExtractedRecord]:
        if content[:2] == b"PK":
            records: list[RawExtractedRecord] = []
            with ZipFile(io.BytesIO(content)) as kmz:
                for kml_name in [name for name in kmz.namelist() if name.lower().endswith(".kml")]:
                    records.extend(
                        self.extract_from_bytes(
                            kmz.read(kml_name),
                            source_file=f"{source_file}:{kml_name}",
                            _network_depth=_network_depth,
                        )
                    )
            return records

        root = ET.fromstring(content)
        records: list[RawExtractedRecord] = []
        ns = "http://www.opengis.net/kml/2.2"

        def _walk(element: ET.Element, folder_stack: list[str]) -> None:
            for child in element:
                tag = _strip_namespace(child.tag)
                if tag == "Folder":
                    name_el = child.find(f"{{{ns}}}name")
                    fname = name_el.text.strip() if name_el is not None and name_el.text else "(unnamed)"
                    _walk(child, folder_stack + [fname])
                elif tag == "Document":
                    _walk(child, folder_stack)
                elif tag == "Placemark":
                    self._process_placemark(child, folder_stack, source_file, records)

        _walk(root, [])

        if _network_depth < NETWORK_LINK_MAX_DEPTH:
            for href in _network_link_hrefs(root):
                try:
                    linked_content = _fetch_network_link(href)
                    records.extend(
                        self.extract_from_bytes(
                            linked_content,
                            source_file=f"{source_file}:{urlparse(href).netloc or 'network-link'}",
                            _network_depth=_network_depth + 1,
                        )
                    )
                except (OSError, URLError, ValueError, ET.ParseError):
                    continue

        # Number them sequentially
        for i, rec in enumerate(records, start=1):
            rec.row_number = i
        return records

    def _process_placemark(
        self,
        placemark: ET.Element,
        folder_stack: list[str],
        source_file: str,
        records: list[RawExtractedRecord],
    ) -> None:
        ns = "http://www.opengis.net/kml/2.2"
        name = _first_text(placemark, "kml:name")
        description = _first_text(placemark, "kml:description")

        folder_path = "/".join(folder_stack) if folder_stack else ""
        category = folder_stack[-1] if folder_stack else ""

        # Determine geometry type and coordinates
        geom_type = "Unknown"
        coordinates = None
        lat, lon = None, None

        point_el = placemark.find(f".//{{{ns}}}Point/{{{ns}}}coordinates")
        line_el = placemark.find(f".//{{{ns}}}LineString/{{{ns}}}coordinates")
        poly_el = placemark.find(f".//{{{ns}}}Polygon//{{{ns}}}coordinates")

        if point_el is not None and point_el.text:
            geom_type = "Point"
            coordinates = clean_value(point_el.text)
            lat, lon = parse_kml_coordinate(coordinates)
        elif line_el is not None and line_el.text:
            geom_type = "LineString"
            coordinates = clean_value(line_el.text)
            # For LineString, use the midpoint as the representative coordinate
            coord_pairs = [c.strip() for c in coordinates.split() if c.strip()]
            if coord_pairs:
                mid_idx = len(coord_pairs) // 2
                lat, lon = parse_kml_coordinate(coord_pairs[mid_idx])
        elif poly_el is not None and poly_el.text:
            geom_type = "Polygon"
            coordinates = clean_value(poly_el.text)
            coord_pairs = [c.strip() for c in coordinates.split() if c.strip()]
            if coord_pairs:
                lat, lon = parse_kml_coordinate(coord_pairs[0])

        is_upload_address_category = _is_upload_address_category(category, folder_path)

        raw_data: dict[str, Any] = {
            "category": category,
            "folder_path": folder_path,
            "geometry_type": geom_type,
            "coordinates": coordinates,
            "latitude": lat,
            "longitude": lon,
            "source_format": "kml",
        }
        raw_data.update(_extract_extended_data(placemark))
        raw_data.update(_parse_description_fields(description))
        street_address = _resolve_placemark_street_address(name, raw_data, description)
        is_address_point = geom_type == "Point" and is_upload_address_category and street_address is not None
        is_polygon_layer = geom_type == "Polygon" and coordinates is not None

        if not is_address_point and not is_polygon_layer:
            return

        # Style info
        style_url = _first_text(placemark, "kml:styleUrl")
        # Parse style for color/type hints
        style_color = ""
        style_type = ""
        if style_url:
            # e.g. #icon-961-FFD600-nodesc or #line-000000-4488-nodesc
            parts = style_url.lstrip("#").split("-")
            if len(parts) >= 2:
                style_type = parts[0]  # "icon" or "line" or "poly"
            if len(parts) >= 3:
                style_color = parts[1] if parts[0] == "poly" else (parts[2] if parts[0] == "icon" else parts[1])

        # Count vertices for lines/polygons
        vertex_count = None
        if geom_type in ("LineString", "Polygon") and coordinates:
            vertex_count = len([c for c in coordinates.split() if c.strip()])

        # Calculate length hint for LineStrings (in coordinate units)
        length_hint = None
        if geom_type == "LineString" and coordinates:
            coord_pairs_list = [c.strip().split(",") for c in coordinates.split() if c.strip()]
            if len(coord_pairs_list) >= 2:
                try:
                    first = coord_pairs_list[0]
                    last = coord_pairs_list[-1]
                    dx = float(last[0]) - float(first[0])
                    dy = float(last[1]) - float(first[1])
                    # Rough distance in meters (at ~34°N latitude: 1°≈111km lat, ~92km lon)
                    length_hint = round(((dy * 111000)**2 + (dx * 92000)**2)**0.5, 1)
                except (ValueError, IndexError):
                    pass

        if is_address_point:
            compact_raw: dict[str, Any] = {
                "Address": street_address,
                "category": category,
                "folder_path": folder_path,
                "geometry_type": "Point",
                "latitude": lat,
                "longitude": lon,
                "source_format": "kml",
                "map_layer_only": False,
            }
            _copy_first_field(raw_data, compact_raw, "City", "City", "city")
            _copy_first_field(raw_data, compact_raw, "State", "State", "state", "ST")
            _copy_first_field(raw_data, compact_raw, "ZIP", "ZIP", "Zip", "Zip Code", "Postal Code")
            raw_data = compact_raw
            resolved_address = street_address
        else:
            raw_data = {
                "polygon_name": name,
                "category": category,
                "folder_path": folder_path,
                "geometry_type": "Polygon",
                "coordinates": coordinates,
                "latitude": lat,
                "longitude": lon,
                "source_format": "kml",
                "vertex_count": vertex_count,
                "style_color": style_color,
                "style_type": style_type,
                "map_layer_only": True,
            }
            resolved_address = name or category or "Polygon"

        raw_data = {k: v for k, v in raw_data.items() if v is not None and v != ""}

        records.append(
            RawExtractedRecord(
                source_file=source_file,
                source_layer=category or "KML Placemark",
                row_number=0,  # Will be set after walk completes
                raw_data={**raw_data, "raw_address": resolved_address},
            )
        )

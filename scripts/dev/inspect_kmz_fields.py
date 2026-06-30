"""Inspect field/column names available in a KMZ file."""
from __future__ import annotations

import json
import re
import zipfile
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

KMZ_PATH = Path(
    r"d:\AI\Solutions\FTTX\Address Validation\Testing Files\TestingFiles_Karthik\MCINFL GPON SEFH26_TS.kmz"
)
NS = "http://www.opengis.net/kml/2.2"
K = f"{{{NS}}}"
HOUSEHOLD = {"household", "households"}


def _norm(value: str | None) -> str:
    return (value or "").strip().lower()


def _is_household(stack: list[str]) -> bool:
    return any(_norm(part) in HOUSEHOLD for part in stack)


def _parse_description(description: str | None) -> dict[str, str]:
    if not description:
        return {}
    fields: dict[str, str] = {}
    for part in re.split(r"<br\s*/?>|\n", description, flags=re.IGNORECASE):
        text = re.sub(r"<[^>]+>", "", part).strip()
        if ":" not in text:
            continue
        key, _, value = text.partition(":")
        key, value = key.strip(), value.strip()
        if key and value and len(key) < 40:
            fields[key] = value
    return fields


def main() -> None:
    extended_keys: Counter[str] = Counter()
    desc_keys: Counter[str] = Counter()
    built_in: Counter[str] = Counter()
    samples: list[dict] = []

    with zipfile.ZipFile(KMZ_PATH) as zf:
        root = ET.fromstring(zf.read("doc.kml"))

    def walk(element: ET.Element, folder_stack: list[str]) -> None:
        local = element.tag.rsplit("}", 1)[-1]
        if local == "Folder":
            name_el = element.find(f"{K}name")
            folder_name = (name_el.text or "").strip() if name_el is not None else "(unnamed)"
            for child in list(element):
                walk(child, folder_stack + [folder_name])
            return
        if local == "Document":
            for child in list(element):
                walk(child, folder_stack)
            return
        if local != "Placemark" or not _is_household(folder_stack):
            return

        name_el = element.find(f"{K}name")
        placemark_name = (name_el.text or "").strip() if name_el is not None else ""
        desc_el = element.find(f"{K}description")
        description = desc_el.text if desc_el is not None and desc_el.text else ""

        extended: dict[str, str] = {}
        for data_el in element.findall(f".//{K}ExtendedData/{K}Data"):
            key = data_el.attrib.get("name")
            value_el = data_el.find(f"{K}value")
            value = (value_el.text or "").strip() if value_el is not None and value_el.text else ""
            if key:
                extended[key] = value
        for simple_el in element.findall(f".//{K}ExtendedData//{K}SimpleData"):
            key = simple_el.attrib.get("name")
            value = (simple_el.text or "").strip() if simple_el.text else ""
            if key:
                extended[key] = value

        point = element.find(f".//{K}Point/{K}coordinates")
        line = element.find(f".//{K}LineString/{K}coordinates")
        polygon = element.find(f".//{K}Polygon//{K}coordinates")
        if point is not None:
            geometry_type = "Point"
            coordinates = (point.text or "").strip()
        elif line is not None:
            geometry_type = "LineString"
            coordinates = (line.text or "").strip()
        elif polygon is not None:
            geometry_type = "Polygon"
            coordinates = (polygon.text or "").strip()
        else:
            geometry_type = "Unknown"
            coordinates = ""

        style_el = element.find(f"{K}styleUrl")
        style_url = (style_el.text or "").strip() if style_el is not None and style_el.text else ""

        for key in extended:
            extended_keys[key] += 1
        for key in _parse_description(description):
            desc_keys[key] += 1

        built_in["placemark_name"] += 1
        built_in["category"] += 1
        built_in["folder_path"] += 1
        if coordinates:
            built_in["coordinates"] += 1
            built_in["latitude"] += 1
            built_in["longitude"] += 1
        if style_url:
            built_in["style_url"] += 1

        if len(samples) < 3:
            lat = lon = None
            if coordinates and "," in coordinates.split()[0]:
                lon_s, lat_s, *_ = coordinates.split()[0].split(",")
                lat, lon = lat_s.strip(), lon_s.strip()
            category = folder_stack[-1] if folder_stack else ""
            samples.append(
                {
                    "folder_path": "/".join(folder_stack),
                    "category": category,
                    "placemark_name": placemark_name,
                    "raw_address_display": f"{category}: {placemark_name}" if category and placemark_name else placemark_name,
                    "geometry_type": geometry_type,
                    "coordinates": coordinates[:120],
                    "latitude": lat,
                    "longitude": lon,
                    "style_url": style_url,
                    "description_raw": description[:250],
                    "description_fields": _parse_description(description),
                    "extended_data": extended,
                }
            )

    for child in list(root):
        walk(child, [])

    print("=== Built-in KMZ fields extracted by this project ===")
    for key, count in sorted(built_in.items()):
        print(f"  {key}: {count} household placemarks")

    print("\n=== ExtendedData / SimpleData columns ===")
    if extended_keys:
        for key, count in extended_keys.most_common():
            print(f"  {key}: {count}")
    else:
        print("  None in this KMZ")

    print("\n=== Description HTML columns ===")
    if desc_keys:
        for key, count in desc_keys.most_common():
            print(f"  {key}: {count}")
    else:
        print("  None in this KMZ")

    print("\n=== Sample household placemarks ===")
    print(json.dumps(samples, indent=2))


if __name__ == "__main__":
    main()

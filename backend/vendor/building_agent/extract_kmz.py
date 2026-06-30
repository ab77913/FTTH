import argparse
import csv
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

KML_NS = "http://www.opengis.net/kml/2.2"


def extract_kml_from_kmz(kmz_path: Path) -> str:
    with zipfile.ZipFile(kmz_path, "r") as zf:
        kml_names = [n for n in zf.namelist() if n.lower().endswith(".kml")]
        if not kml_names:
            raise ValueError("No .kml file found inside KMZ archive")
        with zf.open(kml_names[0]) as fh:
            return fh.read().decode("utf-8")


def _strip_namespaces(kml_text: str) -> str:
    """Remove all namespace declarations and prefixes so ElementTree can parse cleanly."""
    # Remove xmlns declarations: xmlns:foo="..." and xmlns="..."
    kml_text = re.sub(r'\s+xmlns(?::\w+)?="[^"]*"', "", kml_text)
    # Remove prefixed attributes entirely: xsi:schemaLocation="..." -> (removed)
    kml_text = re.sub(r'\s+\w+:\w+="[^"]*"', "", kml_text)
    # Strip tag prefixes: <gx:Track> -> <Track>, </atom:link> -> </link>
    kml_text = re.sub(r"<(/?)(\w+):", r"<\1", kml_text)
    return kml_text


def parse_placemarks(kml_text: str) -> list[dict]:
    kml_text = _strip_namespaces(kml_text)
    root = ET.fromstring(kml_text)
    records = []
    for idx, pm in enumerate(root.iter("Placemark"), start=1):
        name_el = pm.find("name")
        name = (name_el.text or "").strip() if name_el is not None else ""

        coords_el = pm.find(".//coordinates")
        if coords_el is None or not (coords_el.text or "").strip():
            continue

        raw = coords_el.text.strip().split()
        first_point = raw[0]
        parts = first_point.split(",")
        if len(parts) < 2:
            continue

        try:
            lon = float(parts[0])
            lat = float(parts[1])
        except ValueError:
            continue

        if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
            continue

        records.append({
            "address_id": f"addr_{idx:03d}",
            "name": name or f"placemark_{idx:03d}",
            "lat": lat,
            "lon": lon,
        })

    return records


def save_csv(records: list[dict], output_path: Path) -> None:
    with output_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["address_id", "name", "lat", "lon"])
        writer.writeheader()
        writer.writerows(records)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract placemarks from KMZ/KML to CSV.")
    parser.add_argument("input", type=Path, help="Path to .kmz or .kml file.")
    parser.add_argument("--output", type=Path, default=Path("kmz_output.csv"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if not args.input.exists():
        print(f"error: file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    suffix = args.input.suffix.lower()
    if suffix == ".kmz":
        kml_text = extract_kml_from_kmz(args.input)
    elif suffix == ".kml":
        kml_text = args.input.read_text(encoding="utf-8")
    else:
        print(f"error: unsupported file type '{suffix}' — expected .kmz or .kml", file=sys.stderr)
        sys.exit(1)

    records = parse_placemarks(kml_text)
    if not records:
        print("warning: no valid placemarks found", file=sys.stderr)
        sys.exit(0)

    save_csv(records, args.output)
    print(f"extracted {len(records)} placemarks -> {args.output}")


if __name__ == "__main__":
    main()

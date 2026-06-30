import zipfile
import csv
import re
import os
from html.parser import HTMLParser

KMZ_FILE = r"samples\NDBS023-KMZ 1 1.kmz"
OUTPUT_CSV = r"output\addresses_extracted_v2.csv"


class TableParser(HTMLParser):
    """Parses the HTML table inside KML description to extract key-value pairs."""

    def __init__(self):
        super().__init__()
        self.data = {}
        self._cells = []
        self._in_td = False
        self._current_text = ""

    def handle_starttag(self, tag, attrs):
        if tag == "td":
            self._in_td = True
            self._current_text = ""

    def handle_endtag(self, tag):
        if tag == "td":
            self._in_td = False
            text = self._current_text.strip()
            if text:
                self._cells.append(text)

        if tag == "tr" and len(self._cells) == 2:
            self.data[self._cells[0]] = self._cells[1]
            self._cells = []
        elif tag == "tr":
            self._cells = []

    def handle_data(self, data):
        if self._in_td:
            self._current_text += data

    def handle_entityref(self, name):
        if self._in_td:
            if name == "lt":
                self._current_text += "<"
            elif name == "gt":
                self._current_text += ">"
            elif name == "amp":
                self._current_text += "&"


def parse_description(html_text):
    parser = TableParser()
    html_text = html_text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
    parser.feed(html_text)
    return parser.data


def extract_coordinates(placemark_text):
    match = re.search(r"<coordinates>\s*([-\d.]+),([-\d.]+)", placemark_text)
    if match:
        lon, lat = match.group(1), match.group(2)
        return lat, lon
    return "", ""


def is_address_placemark(placemark_id):
    return placemark_id and placemark_id.startswith("Address_")


def clean(val):
    return "" if val in ("<Null>", "&lt;Null&gt;", "Null", None) else str(val).strip()


def main():
    os.makedirs(os.path.dirname(OUTPUT_CSV), exist_ok=True)

    print(f"Reading KMZ: {KMZ_FILE}")

    with zipfile.ZipFile(KMZ_FILE, "r") as kmz:
        kml_content = kmz.read("doc.kml").decode("utf-8")

    parts = kml_content.split("<Placemark")
    placemarks = parts[1:]

    print(f"Total placemarks found: {len(placemarks)}")

    rows = []

    for raw in placemarks:
        id_match = re.match(r'\s+id="([^"]*)"', raw)
        placemark_id = id_match.group(1) if id_match else ""

        if not is_address_placemark(placemark_id):
            continue

        desc_match = re.search(
            r"<description><!\[CDATA\[(.*?)\]\]></description>",
            raw,
            re.DOTALL
        )

        fields = {}
        if desc_match:
            fields = parse_description(desc_match.group(1))

        kml_lat, kml_lon = extract_coordinates(raw)

        lon = clean(fields.get("Longitude", kml_lon))
        lat = clean(fields.get("Latitude", kml_lat))

        address = clean(fields.get("Address Text", ""))
        city = clean(fields.get("City", ""))
        state = clean(fields.get("State", ""))
        zip_code = clean(fields.get("Zip Code", ""))

        address_type = clean(fields.get("Address Type", ""))
        house_number = clean(fields.get("House Number", ""))
        street_address = clean(fields.get("Street Address", ""))
        street_direction = clean(fields.get("Street Direction", ""))
        street_suffix = clean(fields.get("Street Suffix", ""))
        unit_number = clean(fields.get("Unit Number", ""))

        rows.append({
            "Longitude": lon,
            "Latitude": lat,
            "Address": address,
            "City": city,
            "Postal Code": "",   # preserving existing column
            "Zip Code": zip_code,

            "State": state,
            "Address Type": address_type,
            "House Number": house_number,
            "Street Address": street_address,
            "Street Direction": street_direction,
            "Street Suffix": street_suffix,
            "Unit Number": unit_number,
        })

    print(f"Address placemarks extracted: {len(rows)}")

    fieldnames = [
        "Longitude",
        "Latitude",
        "Address",
        "City",
        "Postal Code",
        "Zip Code",

        "State",
        "Address Type",
        "House Number",
        "Street Address",
        "Street Direction",
        "Street Suffix",
        "Unit Number",
    ]

    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"CSV saved to: {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
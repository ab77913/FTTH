"""Check what HTML descriptions look like in the KMZ."""
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

kmz_path = Path(r"c:\Users\ab77913\SEFH16756 - EWR24675 LEXINGTON (1)\SEFH16756 - EWR24675 LEXINGTON.kmz")
ns = "http://www.opengis.net/kml/2.2"

with zipfile.ZipFile(kmz_path) as z:
    content = z.read('doc.kml')
    root = ET.fromstring(content)
    
    # Find all placemarks with descriptions
    count = 0
    for pm in root.findall(f".//{{{ns}}}Placemark"):
        desc_el = pm.find(f"{{{ns}}}description")
        if desc_el is not None and desc_el.text and desc_el.text.strip():
            count += 1
            if count <= 5:
                name_el = pm.find(f"{{{ns}}}name")
                print(f"--- Placemark: {name_el.text if name_el is not None else '?'} ---")
                print(f"  Description: {desc_el.text[:200]}")
                print()
    
    print(f"\nTotal placemarks with descriptions: {count}")

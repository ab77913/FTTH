"""Inspect KMZ folders and placemark distribution."""
import zipfile
from xml.etree import ElementTree as ET
from pathlib import Path

kmz_path = Path(r"c:\Users\ab77913\SEFH16756 - EWR24675 LEXINGTON (1)\SEFH16756 - EWR24675 LEXINGTON.kmz")

with zipfile.ZipFile(kmz_path) as z:
    content = z.read('doc.kml')
    root = ET.fromstring(content)
    
    ns = "http://www.opengis.net/kml/2.2"
    
    # Get all folders and count their placemarks
    def get_folder_placemarks(element, parent_path=""):
        results = []
        for child in element:
            tag = child.tag.split('}')[-1]
            if tag == "Folder":
                folder_name = child.find(f"{{{ns}}}name")
                fname = folder_name.text if folder_name is not None else "(unnamed)"
                path = f"{parent_path}/{fname}" if parent_path else fname
                # Count direct placemarks
                pms = child.findall(f"{{{ns}}}Placemark")
                if pms:
                    results.append((path, len(pms), pms[0]))
                # Recurse
                results.extend(get_folder_placemarks(child, path))
        return results
    
    folder_data = get_folder_placemarks(root)
    
    print("Folder structure with placemark counts:")
    print("-" * 60)
    total = 0
    for path, count, sample_pm in folder_data:
        total += count
        name_el = sample_pm.find(f"{{{ns}}}name")
        style_el = sample_pm.find(f"{{{ns}}}styleUrl")
        sample_name = name_el.text if name_el is not None else "?"
        sample_style = style_el.text if style_el is not None else "?"
        print(f"  {path:30s} | {count:4d} placemarks | sample: name='{sample_name}', style='{sample_style}'")
    print(f"\n  TOTAL: {total} placemarks")
    
    # Check if any placemarks have LineString or Polygon
    print("\nGeometry types:")
    points = root.findall(f".//{{{ns}}}Point")
    lines = root.findall(f".//{{{ns}}}LineString")
    polygons = root.findall(f".//{{{ns}}}Polygon")
    print(f"  Points: {len(points)}, LineStrings: {len(lines)}, Polygons: {len(polygons)}")

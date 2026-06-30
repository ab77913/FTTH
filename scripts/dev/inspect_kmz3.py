"""Inspect KMZ - find where placemarks actually live."""
import zipfile
from xml.etree import ElementTree as ET
from pathlib import Path
from collections import Counter

kmz_path = Path(r"c:\Users\ab77913\SEFH16756 - EWR24675 LEXINGTON (1)\SEFH16756 - EWR24675 LEXINGTON.kmz")

with zipfile.ZipFile(kmz_path) as z:
    content = z.read('doc.kml')
    root = ET.fromstring(content)
    
    ns = "http://www.opengis.net/kml/2.2"
    
    # Walk the tree to find parent folder of each placemark
    def walk(element, folder_stack=[]):
        results = []
        for child in element:
            tag = child.tag.split('}')[-1]
            if tag == "Folder":
                name_el = child.find(f"{{{ns}}}name")
                fname = name_el.text if name_el is not None else "(unnamed)"
                results.extend(walk(child, folder_stack + [fname]))
            elif tag == "Placemark":
                name_el = child.find(f"{{{ns}}}name")
                pname = name_el.text if name_el is not None else "?"
                style_el = child.find(f"{{{ns}}}styleUrl")
                style = style_el.text if style_el is not None else ""
                # Geometry type
                has_point = child.find(f".//{{{ns}}}Point") is not None
                has_line = child.find(f".//{{{ns}}}LineString") is not None
                has_poly = child.find(f".//{{{ns}}}Polygon") is not None
                geom = "Point" if has_point else "LineString" if has_line else "Polygon" if has_poly else "None"
                folder_path = "/".join(folder_stack) if folder_stack else "(root)"
                results.append({
                    "folder": folder_path,
                    "name": pname,
                    "style": style,
                    "geom_type": geom,
                })
            elif tag == "Document":
                results.extend(walk(child, folder_stack))
        return results
    
    all_pms = walk(root)
    print(f"Total placemarks found: {len(all_pms)}")
    
    # Group by folder
    folder_counts = Counter(p["folder"] for p in all_pms)
    geom_by_folder = {}
    for p in all_pms:
        key = p["folder"]
        if key not in geom_by_folder:
            geom_by_folder[key] = Counter()
        geom_by_folder[key][p["geom_type"]] += 1
    
    print("\nFolder breakdown:")
    print(f"{'Folder':<30} {'Count':>6}  Geometry types")
    print("-" * 70)
    for folder, count in sorted(folder_counts.items(), key=lambda x: -x[1]):
        geoms = dict(geom_by_folder[folder])
        print(f"  {folder:<28} {count:>6}  {geoms}")
    
    # Show a sample from each folder
    print("\nSamples from each folder:")
    seen_folders = set()
    for p in all_pms:
        if p["folder"] not in seen_folders:
            seen_folders.add(p["folder"])
            print(f"  [{p['folder']}] name='{p['name']}', geom={p['geom_type']}, style='{p['style']}'")

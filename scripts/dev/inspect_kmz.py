"""Inspect the raw KML content from the KMZ to see all available data."""
import zipfile
from xml.etree import ElementTree as ET
from pathlib import Path

kmz_path = Path(r"c:\Users\ab77913\SEFH16756 - EWR24675 LEXINGTON (1)\SEFH16756 - EWR24675 LEXINGTON.kmz")

with zipfile.ZipFile(kmz_path) as z:
    kml_names = [n for n in z.namelist() if n.lower().endswith('.kml')]
    print(f"KML files in KMZ: {kml_names}")
    
    content = z.read(kml_names[0])
    root = ET.fromstring(content)
    
    # Find namespaces
    ns = {"kml": "http://www.opengis.net/kml/2.2"}
    
    # Show structure: folders
    folders = root.findall(".//{http://www.opengis.net/kml/2.2}Folder")
    print(f"\nFolders ({len(folders)}):")
    for f in folders[:5]:
        name = f.find("{http://www.opengis.net/kml/2.2}name")
        print(f"  - {name.text if name is not None else '(unnamed)'}")
    
    # Show first few placemarks in detail
    placemarks = root.findall(".//{http://www.opengis.net/kml/2.2}Placemark")
    print(f"\nTotal placemarks: {len(placemarks)}")
    
    for i, pm in enumerate(placemarks[:3]):
        print(f"\n{'='*60}")
        print(f"Placemark {i+1}:")
        # Name
        name = pm.find("{http://www.opengis.net/kml/2.2}name")
        print(f"  Name: {name.text if name is not None else None}")
        
        # Description
        desc = pm.find("{http://www.opengis.net/kml/2.2}description")
        if desc is not None and desc.text:
            print(f"  Description ({len(desc.text)} chars): {desc.text[:500]}")
        else:
            print(f"  Description: None")
        
        # ExtendedData
        ext = pm.findall(".//{http://www.opengis.net/kml/2.2}ExtendedData")
        if ext:
            print(f"  ExtendedData elements: {len(ext)}")
            for data_el in pm.findall(".//{http://www.opengis.net/kml/2.2}Data"):
                name_attr = data_el.attrib.get("name", "?")
                val = data_el.find("{http://www.opengis.net/kml/2.2}value")
                print(f"    Data[@name='{name_attr}'] = {val.text if val is not None else None}")
            for sd in pm.findall(".//{http://www.opengis.net/kml/2.2}SimpleData"):
                name_attr = sd.attrib.get("name", "?")
                print(f"    SimpleData[@name='{name_attr}'] = {sd.text}")
        
        # Coordinates
        coords = pm.find(".//{http://www.opengis.net/kml/2.2}coordinates")
        if coords is not None:
            print(f"  Coordinates: {coords.text.strip()[:80]}")
        
        # Style
        style_url = pm.find("{http://www.opengis.net/kml/2.2}styleUrl")
        if style_url is not None:
            print(f"  StyleUrl: {style_url.text}")
        
        # All child elements
        print(f"  All child tags: {[c.tag.split('}')[-1] for c in pm]}")

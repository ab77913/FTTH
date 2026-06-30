import pandas as pd
import json

# ✅ Change this if needed
input_csv = "kmz_output.csv"

df = pd.read_csv(input_csv)

# ✅ Compute bounding box
min_lat = df["lat"].min()
max_lat = df["lat"].max()
min_lon = df["lon"].min()
max_lon = df["lon"].max()

# ✅ Add buffer (important)
buffer = 0.002

min_lat -= buffer
max_lat += buffer
min_lon -= buffer
max_lon += buffer

# ✅ Create GeoJSON
geojson = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [min_lon, min_lat],
                    [max_lon, min_lat],
                    [max_lon, max_lat],
                    [min_lon, max_lat],
                    [min_lon, min_lat]
                ]]
            }
        }
    ]
}

# ✅ Save file
output_file = "area.geojson"
with open(output_file, "w") as f:
    json.dump(geojson, f)

print("✅ area.geojson created successfully")
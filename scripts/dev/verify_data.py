"""Verify the re-ingested KMZ data in the database."""
import requests
import json

resp = requests.get("http://localhost:8000/api/records", params={
    "page": 1, "page_size": 3,
    "job_id": "43b33885-e27b-4e75-893b-c7acd27dc661"
})
data = resp.json()
print(f"Columns ({len(data['columns'])}):")
for c in data["columns"]:
    print(f"  {c['key']:25s} source={c.get('source','')}")
print()
for rec in data["records"][:2]:
    print(f"Record {rec['id']}:")
    if rec.get("raw_data"):
        for k, v in rec["raw_data"].items():
            print(f"  {k}: {str(v)[:70]}")
    print()

# Check geo records too
geo = requests.get("http://localhost:8000/api/records/geo", params={"limit": 3}).json()
print(f"\nGeo features sample:")
for f in geo["features"][:2]:
    print(f"  {f}")

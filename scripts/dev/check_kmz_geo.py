"""Check KMZ geometry data in the database."""
from data_ingestion.database.db import get_session_factory
from data_ingestion.database.models import Address
from sqlalchemy import select, func, text

session = get_session_factory()()
job_id = '43b33885-e27b-4e75-893b-c7acd27dc661'

# Get distinct geometry types and categories
geom_col = text("raw_metadata->>'geometry_type'")
cat_col = text("raw_metadata->>'category'")
rows = session.execute(
    select(geom_col, cat_col, func.count(Address.id))
    .where(Address.job_id == job_id)
    .group_by(geom_col, cat_col)
).all()
print("Geometry types and categories:")
for r in rows:
    print(f"  {r[0]} / {r[1]}: {r[2]} records")

# Check LineString
ls_filter = text("raw_metadata->>'geometry_type' = 'LineString'")
ls = session.execute(
    select(Address).where(Address.job_id == job_id, ls_filter).limit(1)
).scalar()
if ls:
    meta = ls.raw_metadata or {}
    coords = meta.get('coordinates', '')
    print(f"\nLineString sample: lat={ls.latitude}, lon={ls.longitude}")
    print(f"  coords type: {type(coords).__name__}")
    print(f"  coords preview: {str(coords)[:400]}")
else:
    print("\nNo LineString records found")

# Check Polygon
poly_filter = text("raw_metadata->>'geometry_type' = 'Polygon'")
poly = session.execute(
    select(Address).where(Address.job_id == job_id, poly_filter).limit(1)
).scalar()
if poly:
    meta = poly.raw_metadata or {}
    coords = meta.get('coordinates', '')
    print(f"\nPolygon sample: lat={poly.latitude}, lon={poly.longitude}")
    print(f"  coords type: {type(coords).__name__}")
    print(f"  coords preview: {str(coords)[:400]}")
else:
    print("\nNo Polygon records found")

# Now check what the /api/records/geo endpoint returns
print("\n--- Simulating geo endpoint query ---")
stmt = select(
    Address.id, Address.latitude, Address.longitude, Address.raw_address,
    Address.city, Address.state, Address.raw_metadata,
).where(
    Address.latitude.isnot(None),
    Address.longitude.isnot(None),
    Address.job_id == job_id,
).limit(3)
rows = session.execute(stmt).all()
print(f"Geo query returned {len(rows)} rows")
for row in rows:
    meta = row.raw_metadata or {}
    print(f"  id={row.id}, lat={row.latitude}, lon={row.longitude}")
    print(f"    geom_type={meta.get('geometry_type')}, cat={meta.get('category')}")
    print(f"    style_color={meta.get('style_color')}")

session.close()

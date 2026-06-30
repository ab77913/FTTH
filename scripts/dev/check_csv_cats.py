"""Check CSV category-like fields."""
import csv
from collections import Counter

with open(r'c:\Users\ab77913\SEFH16756 - EWR24675 LEXINGTON (1)\6BA8.csv', 'r') as f:
    reader = csv.DictReader(f)
    rows = list(reader)

print(f"Total rows: {len(rows)}")
print()

# Check caf_cb_type
vals = Counter(r['caf_cb_type'] for r in rows if r.get('caf_cb_type'))
print(f"caf_cb_type: {dict(vals)}")

# Check addr_link_type  
vals = Counter(r['addr_link_type'] for r in rows if r.get('addr_link_type'))
print(f"addr_link_type: {dict(vals)}")

# Check noc_plan_desc
vals = Counter(r['noc_plan_desc'] for r in rows if r.get('noc_plan_desc'))
print(f"noc_plan_desc: {dict(vals)}")

# Check data_source
vals = Counter(r['data_source'] for r in rows if r.get('data_source'))
print(f"data_source: {dict(vals)}")

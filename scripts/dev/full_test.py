"""
Full end-to-end test of the FTTH Data Ingestion API.
Run: python full_test.py
"""
import json, os, sys, traceback
import requests

BASE = "http://localhost:8000"
PASS_ = "Meridian@2026"
CSV_PATH  = os.path.join(os.path.dirname(__file__), "uploads", "6BA8.csv")
KMZ_PATH  = os.path.join(os.path.dirname(__file__), "uploads", "SEFH16756 - EWR24675 LEXINGTON.kmz")
ZIP_PATH  = os.path.join(os.path.dirname(__file__), "SEFH16756 - EWR24675 LEXINGTON.zip")

PASS_COLOR = "\033[92m✓\033[0m"
FAIL_COLOR = "\033[91m✗\033[0m"
INFO_COLOR = "\033[94m•\033[0m"
WARN_COLOR = "\033[93m!\033[0m"

results = []

def ok(name, detail=""):
    results.append(("PASS", name))
    print(f"  {PASS_COLOR} {name}" + (f"  [{detail}]" if detail else ""))

def fail(name, detail=""):
    results.append(("FAIL", name))
    print(f"  {FAIL_COLOR} {name}" + (f"  [{detail}]" if detail else ""))

def info(msg):
    print(f"  {INFO_COLOR} {msg}")

def section(title):
    print(f"\n{'─'*60}")
    print(f"  {title}")
    print(f"{'─'*60}")

def check(name, cond, detail=""):
    if cond:
        ok(name, detail)
    else:
        fail(name, detail)

# ── 1. Static frontend ────────────────────────────────────────────────────────
section("1. Static frontend")
try:
    r = requests.get(f"{BASE}/")
    check("GET / returns 200", r.status_code == 200, str(r.status_code))
    check("Returns HTML", "text/html" in r.headers.get("content-type",""), r.headers.get("content-type"))
    check("React root present", 'id="root"' in r.text)
    check("Babel script loaded", "babel" in r.text.lower())
    check("Leaflet loaded", "leaflet" in r.text.lower())
    check("apiFetch defined", "apiFetch" in r.text)
    check("ftth-unauthorized event", "ftth-unauthorized" in r.text)
    check(".zip in accept attr", '.zip' in r.text)
    check("Error display in UploadModal", "setError" in r.text)
    check("RecordsTable has all cols (no slice 10)", "columns.slice(0, 10)" not in r.text)
    check("Search bar in records", "searchInput" in r.text)
    check("Map pin per row", "longitude" in r.text and "google.com/maps?q=" in r.text)
    check("navigate prop on RecordsTable", 'navigate={navigate}' in r.text)
except Exception as e:
    fail("Frontend fetch", str(e))

# ── 2. Auth ───────────────────────────────────────────────────────────────────
section("2. Authentication")
TOKEN = None
try:
    # Bad credentials
    r = requests.post(f"{BASE}/api/login", json={"username":"bad","password":"bad"})
    check("Bad credentials → 401", r.status_code == 401, str(r.status_code))

    # Good credentials
    r = requests.post(f"{BASE}/api/login", json={"username":"ftth_team","password":PASS_})
    check("Login returns 200", r.status_code == 200, str(r.status_code))
    data = r.json()
    check("Token in response", "token" in data)
    check("Username in response", data.get("username") == "ftth_team")
    TOKEN = data["token"]
    info(f"Token: {TOKEN[:20]}…")

    # Stale / bad token → 401
    r = requests.get(f"{BASE}/api/jobs", headers={"Authorization": "Bearer bad_token_xyz"})
    check("Stale token → 401", r.status_code == 401, str(r.status_code))

    # Valid token works
    H = {"Authorization": f"Bearer {TOKEN}"}
    r = requests.get(f"{BASE}/api/jobs", headers=H)
    check("Valid token → 200", r.status_code == 200, str(r.status_code))

    # Logout
    r = requests.post(f"{BASE}/api/logout", headers=H)
    check("Logout 200", r.status_code == 200, str(r.status_code))

    # Token invalid after logout
    r = requests.get(f"{BASE}/api/jobs", headers=H)
    check("Token invalid after logout", r.status_code == 401, str(r.status_code))

    # Re-login
    r = requests.post(f"{BASE}/api/login", json={"username":"ftth_team","password":PASS_})
    TOKEN = r.json()["token"]
    ok("Re-login works", f"new token: {TOKEN[:15]}…")

except Exception as e:
    fail("Auth section", str(e))
    traceback.print_exc()

H = {"Authorization": f"Bearer {TOKEN}"}

# ── 3. Upload ─────────────────────────────────────────────────────────────────
section("3. File Upload")
csv_job_id = kmz_job_id = zip_job_id = None
try:
    # CSV upload
    if os.path.exists(CSV_PATH):
        with open(CSV_PATH, "rb") as f:
            r = requests.post(f"{BASE}/api/upload?customer_id=test_csv", headers=H, files={"file": f})
        check("CSV upload 200", r.status_code == 200, str(r.status_code))
        if r.status_code == 200:
            j = r.json()
            csv_job_id = j["job_id"]
            check("CSV job_id present", bool(csv_job_id))
            check("CSV has records", j.get("stored_records", 0) > 0, str(j.get("stored_records")))
            check("CSV status COMPLETED or PARTIAL", j.get("status") in ("COMPLETED","PARTIAL"), j.get("status"))
            info(f"CSV job: {csv_job_id[:8]} — {j.get('stored_records')} records")
        else:
            fail("CSV upload body", r.text[:200])
    else:
        fail("CSV file missing", CSV_PATH)

    # KMZ upload
    if os.path.exists(KMZ_PATH):
        with open(KMZ_PATH, "rb") as f:
            r = requests.post(f"{BASE}/api/upload?customer_id=test_kmz", headers=H, files={"file": f})
        check("KMZ upload 200", r.status_code == 200, str(r.status_code))
        if r.status_code == 200:
            j = r.json()
            kmz_job_id = j["job_id"]
            check("KMZ has records", j.get("stored_records", 0) > 0, str(j.get("stored_records")))
            info(f"KMZ job: {kmz_job_id[:8]} — {j.get('stored_records')} records")
        else:
            fail("KMZ upload body", r.text[:200])
    else:
        fail("KMZ file missing", KMZ_PATH)

    # ZIP upload
    if os.path.exists(ZIP_PATH):
        with open(ZIP_PATH, "rb") as f:
            r = requests.post(f"{BASE}/api/upload?customer_id=test_zip", headers=H, files={"file": f})
        check("ZIP upload 200", r.status_code == 200, str(r.status_code))
        if r.status_code == 200:
            j = r.json()
            zip_job_id = j["job_id"]
            check("ZIP has records", j.get("stored_records", 0) > 0, str(j.get("stored_records")))
            info(f"ZIP job: {zip_job_id[:8]} — {j.get('stored_records')} records")
        else:
            fail("ZIP upload body", r.text[:200])
    else:
        fail("ZIP file missing", ZIP_PATH)

    # Unsupported file type
    import io as _io
    r = requests.post(f"{BASE}/api/upload?customer_id=test", headers=H,
        files={"file": ("test.txt", _io.BytesIO(b"hello"), "text/plain")})
    check("Unsupported type → 400", r.status_code == 400, str(r.status_code))

    # Upload without auth → 401
    r = requests.post(f"{BASE}/api/upload", files={"file": ("x.csv", _io.BytesIO(b"a,b"), "text/csv")})
    check("Upload without auth → 401", r.status_code == 401, str(r.status_code))

except Exception as e:
    fail("Upload section", str(e))
    traceback.print_exc()

# Use best job_id for remaining tests
test_job_id = kmz_job_id or csv_job_id or zip_job_id

# ── 4. Jobs ───────────────────────────────────────────────────────────────────
section("4. Jobs API")
try:
    r = requests.get(f"{BASE}/api/jobs", headers=H)
    check("GET /api/jobs 200", r.status_code == 200)
    jobs = r.json()
    check("Jobs is a list", isinstance(jobs, list))
    check("Has jobs", len(jobs) > 0, f"{len(jobs)} jobs")
    if jobs:
        j = jobs[0]
        for field in ("id","source_file","row_count","status","created_at"):
            check(f"Job has {field}", field in j)

    if test_job_id:
        r = requests.get(f"{BASE}/api/jobs/{test_job_id}", headers=H)
        check("GET /api/jobs/{id} 200", r.status_code == 200, str(r.status_code))
        check("Job id matches", r.json().get("id") == test_job_id)

    r = requests.get(f"{BASE}/api/jobs/00000000-0000-0000-0000-000000000000", headers=H)
    check("Non-existent job → 404", r.status_code == 404, str(r.status_code))

except Exception as e:
    fail("Jobs section", str(e))

# ── 5. Records ────────────────────────────────────────────────────────────────
section("5. Records API")
try:
    if test_job_id:
        # Basic pagination
        r = requests.get(f"{BASE}/api/records", params={"job_id": test_job_id, "page": 1, "page_size": 10}, headers=H)
        check("GET /api/records 200", r.status_code == 200, str(r.status_code))
        d = r.json()
        check("Has records list", isinstance(d.get("records"), list))
        check("Has columns", isinstance(d.get("columns"), list) and len(d.get("columns",[])) > 0,
              f"{len(d.get('columns',[]))} cols")
        check("Has total", d.get("total", 0) > 0, str(d.get("total")))
        check("Has total_pages", d.get("total_pages", 0) > 0)
        check("All columns present (>10)", len(d.get("columns",[])) > 10,
              f"{len(d.get('columns',[]))} cols")
        info(f"Records: {d['total']} total, {len(d['columns'])} columns")

        if d.get("records"):
            r0 = d["records"][0]
            check("Record has id", "id" in r0)
            check("Record has raw_metadata", "raw_metadata" in r0)
            check("Record has raw_data", "raw_data" in r0)
            has_coords = any(rec.get("latitude") and rec.get("longitude") for rec in d["records"])
            info(f"First 10 records have coords: {has_coords}")

        # Search
        r = requests.get(f"{BASE}/api/records", params={"job_id": test_job_id, "search": "SC", "page_size": 5}, headers=H)
        check("Search param works 200", r.status_code == 200)

        # Geo records
        r = requests.get(f"{BASE}/api/records/geo", params={"job_id": test_job_id, "limit": 100}, headers=H)
        check("GET /api/records/geo 200", r.status_code == 200, str(r.status_code))
        gd = r.json()
        check("Geo has features", isinstance(gd.get("features"), list))
        check("Geo count > 0", gd.get("count", 0) > 0, str(gd.get("count")))
        if gd.get("features"):
            f0 = gd["features"][0]
            check("Geo feature has lat", "lat" in f0)
            check("Geo feature has lon", "lon" in f0)
            check("Geo feature has id", "id" in f0)
            check("Geo style_color has # prefix",
                  not f0.get("style_color") or f0["style_color"].startswith("#"),
                  repr(f0.get("style_color")))
        info(f"Geo features: {gd.get('count')}")

        # Single record
        if d["records"]:
            rid = d["records"][0]["id"]
            r = requests.get(f"{BASE}/api/records/{rid}", headers=H)
            check(f"GET /api/records/{rid} 200", r.status_code == 200)

    # Without auth → 401
    r = requests.get(f"{BASE}/api/records")
    check("Records without auth → 401", r.status_code == 401)

except Exception as e:
    fail("Records section", str(e))
    traceback.print_exc()

# ── 6. Categories / Columns / Stats ──────────────────────────────────────────
section("6. Categories / Columns / Stats")
try:
    r = requests.get(f"{BASE}/api/categories", params={"job_id": test_job_id}, headers=H)
    check("GET /api/categories 200", r.status_code == 200)
    check("categories is list", isinstance(r.json().get("categories"), list))

    r = requests.get(f"{BASE}/api/columns", params={"job_id": test_job_id}, headers=H)
    check("GET /api/columns 200", r.status_code == 200)
    cols = r.json()
    check("columns list returned", isinstance(cols, list) and len(cols) > 0, f"{len(cols)} cols")

    r = requests.get(f"{BASE}/api/stats", params={"job_id": test_job_id}, headers=H)
    check("GET /api/stats 200", r.status_code == 200)
    check("stats has total_records", "total_records" in r.json())

except Exception as e:
    fail("Categories/Columns section", str(e))

# ── 7. Agent Tables ───────────────────────────────────────────────────────────
section("7. Agent Tables")
AGENT = "e2e_test_agent"
try:
    # Register
    r = requests.post(f"{BASE}/api/agent/tables", headers=H, json={
        "agent_name": AGENT,
        "display_name": "E2E Test Agent",
        "description": "Created by full_test.py",
        "color_rules": [
            {"field": "status", "value": "PASS", "color": "#22c55e", "label": "Pass"},
            {"field": "status", "value": "FAIL", "color": "#ef4444", "label": "Fail"},
        ]
    })
    if r.status_code == 400 and "already exists" in r.text:
        ok("Agent table already registered (skipping create)")
    else:
        check("Create agent table 200", r.status_code == 200, r.text[:100] if r.status_code != 200 else "")

    # List
    r = requests.get(f"{BASE}/api/agent/tables", headers=H)
    check("List agent tables 200", r.status_code == 200)
    tables = r.json()
    check("e2e agent found", any(t["agent_name"] == AGENT for t in tables))

    # Get single
    r = requests.get(f"{BASE}/api/agent/tables/{AGENT}", headers=H)
    check("Get agent table 200", r.status_code == 200)
    check("Correct agent_name", r.json().get("agent_name") == AGENT)

    # Update color rules
    r = requests.put(f"{BASE}/api/agent/tables/{AGENT}/color-rules", headers=H,
        json=[{"field":"status","value":"PASS","color":"#22c55e","label":"Pass"},
              {"field":"status","value":"FAIL","color":"#ef4444","label":"Fail"},
              {"field":"status","value":"REVIEW","color":"#f59e0b","label":"Review"}])
    check("Update color rules 200", r.status_code == 200, r.text[:100] if r.status_code != 200 else "")

    # Non-existent table
    r = requests.get(f"{BASE}/api/agent/tables/nonexistent_xyz", headers=H)
    check("Non-existent agent table → 404", r.status_code == 404)

except Exception as e:
    fail("Agent tables section", str(e))
    traceback.print_exc()

# ── 8. Agent Input / Results ──────────────────────────────────────────────────
section("8. Agent Input / Results")
try:
    if test_job_id:
        # Fetch input records
        r = requests.get(f"{BASE}/api/agent/records", params={"job_id": test_job_id, "limit": 5}, headers=H)
        check("GET /api/agent/records 200", r.status_code == 200)
        ard = r.json()
        check("agent records list", isinstance(ard.get("records"), list))
        check("agent records count", len(ard.get("records",[])) > 0)
        check("agent records total", ard.get("total", 0) > 0)

        if ard.get("records"):
            r0 = ard["records"][0]
            for f in ("id","raw_address","latitude","longitude","raw_metadata"):
                check(f"Agent input has {f}", f in r0)

        # Push bulk results
        batch = [
            {"address_id": rec["id"], "job_id": test_job_id,
             "data": {"status": ["PASS","FAIL","REVIEW"][i%3], "score": 90-i*5}}
            for i, rec in enumerate(ard.get("records", []))
        ]
        r = requests.post(f"{BASE}/api/agent/results/{AGENT}", headers=H, json=batch)
        check("POST agent results 200", r.status_code == 200, r.text[:100] if r.status_code != 200 else "")
        check("saved count correct", r.json().get("saved") == len(batch), str(r.json()))

        # Read back results
        r = requests.get(f"{BASE}/api/agent/results/{AGENT}", params={"job_id": test_job_id}, headers=H)
        check("GET agent results 200", r.status_code == 200)
        rd = r.json()
        check("results returned", rd.get("total", 0) > 0, str(rd.get("total")))
        if rd.get("results"):
            res0 = rd["results"][0]
            check("Result has address_id", "address_id" in res0)
            check("Result has data", "data" in res0)
            check("Result data has status", "status" in res0.get("data",{}))

        # Patch single result
        if rd.get("results"):
            addr_id = rd["results"][0]["address_id"]
            r = requests.patch(f"{BASE}/api/agent/results/{AGENT}/{addr_id}",
                headers=H, json={"status":"PASS","score":99,"updated":True})
            check("PATCH single result 200", r.status_code == 200, r.text[:100] if r.status_code != 200 else "")

        # Bulk patch via raw_metadata path
        r2_batch = [{"id": rec["id"], "agent_id": AGENT, "output": {"status":"PASS"}}
                    for rec in ard.get("records", [])[:3]]
        r = requests.patch(f"{BASE}/api/agent/records", headers=H, json=r2_batch)
        check("PATCH bulk agent records 200", r.status_code == 200, r.text[:100] if r.status_code != 200 else "")

        # Max 500 limit
        big_batch = [{"id": i, "agent_id": "x", "output": {}} for i in range(501)]
        r = requests.patch(f"{BASE}/api/agent/records", headers=H, json=big_batch)
        check("Bulk patch >500 → 400", r.status_code == 400)

        # Max 1000 results limit
        big_results = [{"address_id": i, "job_id": test_job_id, "data": {}} for i in range(1001)]
        r = requests.post(f"{BASE}/api/agent/results/{AGENT}", headers=H, json=big_results)
        check("Post results >1000 → 400", r.status_code == 400)

except Exception as e:
    fail("Agent results section", str(e))
    traceback.print_exc()

# ── 9. Map Overlay ────────────────────────────────────────────────────────────
section("9. Map Overlay")
try:
    if test_job_id:
        r = requests.get(f"{BASE}/api/map/overlay", params={"job_id": test_job_id}, headers=H)
        check("GET /api/map/overlay 200", r.status_code == 200)
        ov = r.json()
        check("Overlay has 'overlay' key", "overlay" in ov)
        check("Overlay has 'legend' key", "legend" in ov)
        check("Overlay is a dict", isinstance(ov.get("overlay"), dict))
        check("Legend is a list", isinstance(ov.get("legend"), list))
        addr_count = len(ov.get("overlay", {}))
        check("Overlay has entries (agent data submitted)", addr_count > 0, f"{addr_count} entries")
        if ov.get("overlay"):
            first_entry = next(iter(ov["overlay"].values()))
            check("Overlay entry has color", "color" in first_entry)
            check("Overlay color has # prefix",
                  first_entry.get("color","").startswith("#"), first_entry.get("color"))
            check("Overlay entry has label", "label" in first_entry)
            check("Overlay entry has agent", "agent" in first_entry)
        if ov.get("legend"):
            litem = ov["legend"][0]
            check("Legend item has display_name", "display_name" in litem)
            check("Legend item has color", "color" in litem)

except Exception as e:
    fail("Map overlay section", str(e))
    traceback.print_exc()

# ── 10. Export ────────────────────────────────────────────────────────────────
section("10. Exports")
try:
    if test_job_id:
        # CSV export with token query param
        r = requests.get(f"{BASE}/api/export/csv",
            params={"job_id": test_job_id, "_t": TOKEN}, allow_redirects=True)
        check("CSV export 200", r.status_code == 200, str(r.status_code))
        check("CSV content-type", "text/csv" in r.headers.get("content-type",""), r.headers.get("content-type"))
        check("CSV has content-disposition", "attachment" in r.headers.get("content-disposition",""))
        lines = r.text.split("\n")
        check("CSV has header row", len(lines) > 1)
        check("CSV has data rows", len(lines) > 2, f"{len(lines)} lines")
        info(f"CSV export: {len(lines)} lines, {len(lines[0].split(','))} cols in header")

        # KMZ export  
        r = requests.get(f"{BASE}/api/export/kmz",
            params={"job_id": test_job_id, "_t": TOKEN}, allow_redirects=True)
        check("KMZ export 200", r.status_code == 200, str(r.status_code))
        check("KMZ content-type", "kmz" in r.headers.get("content-type","") or
              "zip" in r.headers.get("content-type",""), r.headers.get("content-type"))
        check("KMZ has content-disposition", "attachment" in r.headers.get("content-disposition",""))
        import io as _io
        import zipfile as _zf
        try:
            with _zf.ZipFile(_io.BytesIO(r.content)) as zf:
                check("KMZ contains doc.kml", "doc.kml" in zf.namelist())
                kml_content = zf.read("doc.kml").decode("utf-8")
                check("KML has Placemarks", "<Placemark>" in kml_content)
                check("KML has Folders", "<Folder>" in kml_content)
        except Exception as ze:
            fail("KMZ zip extraction", str(ze))

        # Export without auth → 401
        r = requests.get(f"{BASE}/api/export/csv", params={"job_id": test_job_id})
        check("CSV export without auth → 401", r.status_code == 401)

except Exception as e:
    fail("Exports section", str(e))
    traceback.print_exc()

# ── 11. SSE / Progress ────────────────────────────────────────────────────────
section("11. Job Processing & SSE")
try:
    if test_job_id:
        r = requests.post(f"{BASE}/api/jobs/{test_job_id}/process", headers=H)
        check("Start processing 200", r.status_code == 200, r.text[:100] if r.status_code != 200 else "")
        check("Returns job_id", r.json().get("job_id") == test_job_id)

        r = requests.get(f"{BASE}/api/jobs/{test_job_id}/agents", headers=H)
        check("GET /api/jobs/{id}/agents 200", r.status_code == 200)
        ag = r.json()
        check("agents list returned", isinstance(ag.get("agents"), list))
        check("agent list not empty", len(ag.get("agents",[])) > 0)
        if ag.get("agents"):
            a0 = ag["agents"][0]
            check("Agent has agent_id", "agent_id" in a0)
            check("Agent has status", "status" in a0)
            check("Agent has progress", "progress" in a0)

except Exception as e:
    fail("Processing section", str(e))

# ── Summary ───────────────────────────────────────────────────────────────────
section("SUMMARY")
passed = sum(1 for r,_ in results if r=="PASS")
failed = sum(1 for r,_ in results if r=="FAIL")
print(f"  {'─'*40}")
print(f"  Passed: {PASS_COLOR} {passed}")
print(f"  Failed: {FAIL_COLOR} {failed}")
print(f"  Total : {passed+failed}")
if failed:
    print(f"\n  {'─'*40}")
    print("  FAILURES:")
    for status, name in results:
        if status == "FAIL":
            print(f"    {FAIL_COLOR} {name}")
print()
sys.exit(0 if failed == 0 else 1)

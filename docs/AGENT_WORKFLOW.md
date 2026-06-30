# FTTH Pipeline — Complete Agent Workflow Reference
# Input / Output / Table Names / JSON Storage / Structural Changes
================================================================
Last updated: 2026-06-08  (includes Flow Builder + Per-Agent Tables + FinalAddressResult + Color Coding detail)

---

## COLOR CODING — How It Works (Full Detail)

### When Does Color Coding Run?
Color coding is applied **AFTER the Process button is clicked** and **all 6 agents have finished**.
The function `_refresh_rule_classification_after_processing()` in `pipeline_runner.py`
runs once as the very last step of the pipeline.

### Step-by-step When You Click Process

```
1. User clicks [Process]
   │
   ▼
2. api_server.py → POST /api/jobs/{id}/process
   Calls run_full_pipeline(job_id)
   │
   ▼
3. Stage 0: Classify addresses (coord-only vs real address)
   │
   ▼
4. Agent 2 runs → writes final lat/lon + confidence into agent_results
   Also back-fills addresses.validated_latitude / validated_longitude
   │
   ▼
5. Agent 1 runs → validates address, sets confidence_score, validation_status
   │
   ▼
6. Agents 3, 4, 5 run → write parcel / building / street-view data
   │
   ▼
7. Agent 6 runs → synthesizes all results, sets final_resolution JSON:
   {
     "address": "1603 LAFAYETTE ST, AMERICUS, GA 31709",
     "latitude": 32.086591,
     "longitude": -84.241345,
     "confidence": 85,
     "source_agent": "agent2_geocoding"
   }
   Also writes final_address_results table (20-column flat record)
   │
   ▼
8. _refresh_rule_classification_after_processing() runs:

   For EVERY address row in this job:
   ┌─────────────────────────────────────────────────────────────┐
   │  raw_lat/lon   = addresses.latitude / longitude (AS UPLOADED)│
   │  final_lat/lon = addresses.raw_metadata["final_resolution"] │
   │                  or validated_latitude / longitude           │
   │  confidence    = final_resolution.confidence (0–100)        │
   │  distance      = Haversine(raw_lat/lon, final_lat/lon) in m  │
   └─────────────────────────────────────────────────────────────┘

   Color Decision Logic (evaluated in this order):
   ─────────────────────────────────────────────────────────────
   IF  same address key appears twice from same source file
       → STATUS: duplicate   COLOR: white  (⚪ White)
       REASON: "Duplicate address in uploaded raw data"

   ELSE IF  address came from KML/KMZ (file_role="geospatial")
            AND address NOT found in any CSV/Excel tabular file
       → STATUS: new         COLOR: yellow  (🟡 Yellow)
       REASON: "New address identified from KML/KMZ but not present in CSV/Excel"

   ELSE IF  final_address is populated
            AND final_latitude/longitude are populated
            AND confidence >= 70
            AND distance <= 50 metres (or no raw coords)
       → STATUS: verified    COLOR: green   (🟢 Green)
       REASON: "Processed final address and coordinates verified against raw data"

   ELSE
       → STATUS: invalid     COLOR: red     (🔴 Red)
       REASON: "Address not found or failed final verification"
   ─────────────────────────────────────────────────────────────

9. Results written to:
   a) addresses.raw_metadata:
      {
        "merge_status": "verified",
        "merge_color": "green",
        "merge_reason": "...",
        "merge_raw_final_distance_m": 12.5,
        "merge_classification": {
          "status": "verified", "color": "green",
          "reason": "...", "raw_final_distance_m": 12.5, "final_confidence": 85
        }
      }
   b) uploaded_source_records:
      merge_status = "verified",  merge_color = "green"

10. UI map reads merge_color per record and displays colored pin
```

### Color Summary Table

| Color | Status | Trigger Condition | Applied |
|-------|--------|-------------------|---------|
| 🟢 Green | `verified` | Confidence ≥70 AND distance ≤50m from raw coords | After all agents |
| ⚪ White | `duplicate` | Same address seen twice in uploaded data | After all agents |
| 🟡 Yellow | `new` | In KML/KMZ but NOT in CSV/Excel | After all agents |
| 🔴 Red | `invalid` | Confidence <70 OR distance >50m OR no final coords | After all agents |

### Map Color Mode Buttons (toolbar)
| Button | What it shows |
|--------|---------------|
| **Rule** | The 4-status rule colors above — DEFAULT |
| **Quality** | Confidence buckets: >90% green, 70–90% amber, <70% grey, invalid red |
| **KMZ** | Original KMZ folder/category colors from uploaded file |
| **Agent** | Colors from last agent run (HIGH=green, MEDIUM=blue, SKIP=grey from Agent 6) |
| **Category** | Mode-aware filter. Works with Rule / Quality / KMZ / Agent modes and shows matching category options for the selected mode |

---

## INGESTED DATA — Merged JSON (ingest_payload)

At upload time `repositories.py` builds a canonical `ingest_payload` JSON stored in
`addresses.raw_metadata["ingest_payload"]`.  Every agent reads this for stable original values.

```json
{
  "original_address":   "1603 Lafayette St, Americus, GA",
  "original_street":    "1603 Lafayette St",
  "original_city":      "Americus",
  "original_state":     "GA",
  "original_country":   null,
  "original_zip":       "31709",
  "original_latitude":  32.086,
  "original_longitude": -84.241,
  "network_node":       "NODE-01",
  "terminal_id":        "T001",
  "address_id_field":   "ADDR-001",
  "source_file":        "DVNPGA11A1.csv",
  "source_sheet":       null,
  "source_layer":       null,
  "source_row_number":  5,
  "all_raw_fields": { "every_column_from_source": "value" }
}
```

---

## Overview: Pipeline Execution Order

```
Upload (CSV / KMZ / KML / Excel)
         │
         ▼
   [ Ingestion ]          → tables: ingestion_jobs, addresses, uploaded_source_tables,
                                    uploaded_source_records
         │
         ▼
   [ Agent 1 ]  Address Validation (Smarty + Melissa)
         │                         → table: agent1_results
         ▼
   [ Agent 2 ]  Geocoding (Reverse + Forward)
         │                         → table: agent_results  (agent_name = "agent2_geocoding")
         ▼
   [ Agent 3 ]  Parcel & Land-Use Lookup
         │                         → table: agent_results  (agent_name = "agent3_parcel")
         ▼
   [ Agent 4 ]  Building Classification (footprint + Street View)
         │                         → table: agent_results  (agent_name = "agent4_building")
         ▼
   [ Agent 5 ]  Street View + Azure Vision Analysis
         │                         → table: agent_results  (agent_name = "agent5_streetview")
         ▼
   [ Agent 6 ]  Final FTTH Classification
                                   → table: agent_results  (agent_name = "agent6_final")
```

---

## INGESTION (Pre-Agent)

### Input
| Source | Description |
|--------|-------------|
| Uploaded file | CSV, Excel (.xlsx), KMZ, KML |
| `customer_id` | URL query param during upload |

### Tables Written

#### `ingestion_jobs`
| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID (PK) | Unique job identifier |
| `customer_id` | VARCHAR(255) | Customer/project label |
| `source_file` | TEXT | Original filename |
| `row_count` | INTEGER | Number of address rows |
| `status` | VARCHAR(50) | INGESTED / PROCESSING / COMPLETED / FAILED |
| `error_message` | TEXT | Pipeline error if any |
| `created_at` | DATETIME | Upload timestamp |
| `updated_at` | DATETIME | Last change timestamp |

#### `addresses` (one row per address record)
| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER (PK) | Auto-increment |
| `record_uuid` | UUID | Stable unique reference |
| `job_id` | UUID FK | Links to ingestion_jobs |
| `customer_id` | VARCHAR(255) | Inherited from job |
| `raw_address` | TEXT | As-uploaded address string |
| `city` | VARCHAR(255) | As-uploaded city |
| `state` | VARCHAR(32) | As-uploaded state |
| `zip_code` | VARCHAR(32) | As-uploaded ZIP |
| `latitude` | FLOAT | As-uploaded latitude |
| `longitude` | FLOAT | As-uploaded longitude |
| `source_raw_address` | TEXT | Frozen copy of raw_address at ingest |
| `source_latitude` | FLOAT | Frozen copy of lat at ingest |
| `source_longitude` | FLOAT | Frozen copy of lon at ingest |
| `network_node` | VARCHAR(255) | From source file column |
| `terminal_id` | VARCHAR(255) | From source file column |
| `address_id` | VARCHAR(255) | From source file column |
| `normalized_key` | TEXT | Canonical dedup key |
| `source_file` | TEXT | Filename |
| `source_sheet` | TEXT | Sheet name (Excel) |
| `source_layer` | TEXT | Layer name (KMZ) |
| `source_row_number` | INTEGER | Row in original file |
| `validation_errors` | **JSON** | Ingest-time field errors |
| `validation_warnings` | **JSON** | Ingest-time warnings |
| `raw_metadata` | **JSON** | All extra columns + agent scratch space (see below) |

##### `raw_metadata` JSON Structure
```json
{
  "old": {
    "address": "...",
    "lat": 32.086,
    "lon": -84.241,
    "network_node": "NODE-01",
    "terminal_id": "T001"
  },
  "final_address": "1603 LAFAYETTE ST, AMERICUS, GA 31709",
  "final_latitude": 32.086591,
  "final_longitude": -84.241345,
  "final_resolution": {
    "address": "...",
    "latitude": 32.086591,
    "longitude": -84.241345,
    "source_agent": "agent2_geocoding",
    "confidence": 87
  },
  "buildings_address": { ... },
  "agent5_streetview": { ... }
}
```

#### `uploaded_source_tables`
| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER (PK) | |
| `job_id` | UUID FK | |
| `source_file` | TEXT | Filename |
| `table_name` | VARCHAR(255) | Sanitized logical table name |
| `source_format` | VARCHAR(32) | csv / kml / kmz / xlsx |
| `file_role` | VARCHAR(32) | primary / supplement |
| `stored_path` | TEXT | Filesystem path of saved file |
| `record_count` | INTEGER | |

#### `uploaded_source_records`
| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER (PK) | |
| `source_table_id` | INTEGER FK | → uploaded_source_tables |
| `job_id` | UUID FK | |
| `address_id` | INTEGER FK | → addresses |
| `row_number` | INTEGER | Row in source file |
| `raw_data` | **JSON** | Original row as dict |
| `canonical_data` | **JSON** | Normalized/renamed fields |
| `merge_status` | VARCHAR(32) | MERGED / DUPLICATE / ERROR |
| `merge_color` | VARCHAR(32) | UI color indicator |

---

## AGENT 1 — Address Validation (Smarty + Melissa)

### Purpose
Validates and standardizes each address against two external APIs (Smarty, Melissa),
arbitrates results, assigns a confidence score and validation_status.

### Input (reads from)
| Table / Source | What is read |
|----------------|--------------|
| `addresses` | `raw_address`, `city`, `state`, `zip_code`, `latitude`, `longitude`, `raw_metadata` |
| `agent1_results` | Prior result for this address_id (cache check) |
| `cache/cache.json` | In-memory address → result cache (persisted to disk) |

### External APIs Called
| Provider | Purpose |
|----------|---------|
| **Smarty Streets** | Address standardization, DPV, ZIP+4, lat/lon |
| **Melissa Data** | Address standardization, DPV, ZIP+4, record type |

### Output (writes to)

#### Table: `agent1_results`  (typed columns — one row per address)
| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER (PK) | |
| `job_id` | UUID FK | |
| `address_id` | INTEGER FK | → addresses |
| `raw_address` | TEXT | Input address string |
| `canonical_address` | TEXT | Parsed canonical form |
| `smarty_standardized_address` | TEXT | Smarty output address |
| `smarty_dpv` | VARCHAR(10) | Y/S/D/N = valid/vacant/drop/no-match |
| `smarty_zip_plus_4` | VARCHAR(20) | ZIP+4 from Smarty |
| `smarty_vacant` | BOOLEAN | Vacancy flag |
| `smarty_record_type` | VARCHAR(10) | R=Residential/H=HighRise/P=POBox |
| `smarty_lat` | FLOAT | Smarty geocoded latitude |
| `smarty_lon` | FLOAT | Smarty geocoded longitude |
| `melissa_standardized_address` | TEXT | Melissa output address |
| `melissa_dpv` | VARCHAR(10) | Melissa delivery point validation |
| `melissa_zip_plus_4` | VARCHAR(20) | ZIP+4 from Melissa |
| `melissa_vacant` | BOOLEAN | Vacancy flag |
| `melissa_record_type` | VARCHAR(10) | Melissa record type |
| `chosen_standardized_address` | TEXT | Winning provider's address |
| `chosen_provider` | VARCHAR(20) | "smarty" or "melissa" |
| `structure_hint` | VARCHAR(50) | SFU / MDU_LOW / MDU_MID / MDU_HIGH |
| `confidence_score` | INTEGER | 0–100 |
| `validation_status` | VARCHAR(30) | AUTO_ACCEPT / MANUAL_REVIEW / REJECT |
| `exception_reason` | TEXT | Why rejected (if REJECT) |
| `comparison_reason` | TEXT | Arbitration explanation |
| `data` | **JSON** | Full output blob — mirrors all typed columns above |

#### Table: `agent_results`  (agent_name = `"agent1_address_validator"`)
Same `data` JSON written to the shared table so Agent 1 appears alongside Agents 2–6.

The `data` column (JSON) contains:

```json
{
  "status": "AUTO_ACCEPT",
  "source": "smarty",
  "chosen_provider": "smarty",
  "chosen_standardized_address": "1603 LAFAYETTE ST, AMERICUS, GA 31709-3318",
  "confidence_score": 88,
  "validation_status": "AUTO_ACCEPT",
  "structure_hint": "SFU",
  "exception_reason": null,
  "comparison_reason": "smarty_only",
  "smarty_standardized_address": "1603 LAFAYETTE ST, AMERICUS, GA 31709-3318",
  "smarty_dpv": "Y",
  "smarty_zip_plus_4": "31709-3318",
  "smarty_vacant": false,
  "smarty_record_type": "R",
  "smarty_lat": 32.086591,
  "smarty_lon": -84.241345,
  "melissa_standardized_address": null,
  "melissa_dpv": null,
  "melissa_zip_plus_4": null,
  "melissa_vacant": null,
  "melissa_record_type": null,
  "raw_address": "1603 Lafayette St, Americus, GA",
  "canonical_address": "1603 LAFAYETTE ST AMERICUS GA 31709"
}
```

#### Also back-fills `addresses` table
- Sets `validated_latitude`, `validated_longitude` if coordinates were missing
- Sets `coord_address_match_status`, `coord_address_distance_m`, `coord_address_validation_notes`
- Sets `reverse_geocode_confidence_score`

### Log File
```
logs/agent1_address_validation.log
```

---

## AGENT 2 — Geocoding (Reverse + Forward)

### Purpose
Phase 1: Reverse-geocodes coordinate-only rows (lat/lon → address text).
Phase 2: Cross-checks uploaded address vs uploaded coordinates.
Phase 3: Forward-geocodes addresses using tiered fallback:
  Google Geocoding API → OSM Nominatim → Street centerline interpolation.

### Input (reads from)
| Table | What is read |
|-------|--------------|
| `addresses` | `raw_address`, `latitude`, `longitude`, `source_latitude`, `source_longitude` |
| `agent1_results` | `chosen_standardized_address`, `smarty_lat`, `smarty_lon`, `validation_status` |

### External APIs Called
| Provider | Confidence Threshold | Notes |
|----------|---------------------|-------|
| **Google Geocoding API** | 85+ → stop | ROOFTOP=95, RANGE_INTERPOLATED=75, GEOMETRIC_CENTER=60, APPROXIMATE=40 |
| **OSM Nominatim** | 80+ → stop | Rate-limited: 1.05 s between calls |
| **Street Centerline Interpolation** | Always last resort | Estimated flag set |
| **Nominatim Reverse** | — | Phase 1 reverse geocoding |
| **Google Reverse** | — | Phase 1 fallback reverse geocoding |

### Output (writes to)

#### Table: `agent_results`  (agent_name = `"agent2_geocoding"`)
The `data` column (JSON) contains:

```json
{
  "status": "geocoded",
  "source": "google",
  "formatted_address": "1603 Lafayette St, Americus, GA 31709, USA",
  "latitude": 32.086591,
  "longitude": -84.241345,
  "location_type": "ROOFTOP",
  "confidence": 95.0,
  "coord_distance_m": 42.3,
  "is_estimated": false,
  "pinned_to_source": false,
  "fallback_used": false,
  "_decision_reason": "Google ROOFTOP high confidence",
  "reverse_geocoded_address": "1603 Lafayette St, Americus, GA",
  "coord_match_status": "MATCH",
  "coord_match_distance_m": 42.3,
  "coord_match_notes": "Address and coordinates agree within 100m"
}
```

#### Also writes to `addresses` table
- Sets `validated_raw_address`, `validated_street_line`, `validated_postcode`
- Sets `validated_latitude`, `validated_longitude` (if still missing after Agent 1)
- Sets `coord_address_match_status`, `coord_address_distance_m`

### Log File
```
logs/agent2_geocoding.log
```

---

## AGENT 3 — Parcel & Land-Use Lookup

### Purpose
Identifies the land parcel at each address coordinate.
Determines land use classification (residential/commercial/agricultural),
county name, FIPS codes, and parcel/owner data.

### Input (reads from)
| Table | What is read |
|-------|--------------|
| `addresses` | `latitude`, `longitude`, `source_latitude`, `source_longitude` |
| `agent1_results` | `smarty_lat`, `smarty_lon`, `validation_status` |
| `agent_results` (agent2) | `data.latitude`, `data.longitude` |

### External APIs / Sources Called (in order)
| Step | Source | Notes |
|------|--------|-------|
| 1 | **Regrid API v2** | Requires `REGRID_API_TOKEN` env var. Returns parcel, owner, land_use, county |
| 2 | **TIGER/Line Shapefile** | Optional. `TIGER_SHAPEFILE_PATH` env var → local .shp file. ST_WITHIN → ST_DWITHIN → NEAREST |
| 3 | **US Census Geocoder** | County boundary via `geocoding.geo.census.gov/geocoder/geographies/coordinates` |
| 4 | **OSM Nominatim Reverse** | Last resort — county name only |

### Output (writes to)

#### Table: `agent_results`  (agent_name = `"agent3_parcel"`)
The `data` column (JSON) contains:

```json
{
  "status": "ok",
  "source": "regrid",
  "match_type": "REGRID",
  "parcel_id": "SCT-014-009",
  "owner": "SMITH JOHN D",
  "land_use": "Single Family Residential",
  "land_use_code": "R1",
  "county_name": "Sumter County",
  "state": "GA",
  "state_fips": "13",
  "county_fips": "261",
  "confidence": 95
}
```
When Regrid is unavailable and TIGER/Line is used:
```json
{
  "status": "ok",
  "source": "tigerline_st_within",
  "match_type": "ST_WITHIN",
  "parcel_id": "13261",
  "county_name": "Sumter County",
  "state_fips": "13",
  "county_fips": "261",
  "land_use": "County Boundary",
  "confidence": 85
}
```
When Census Geocoder is used:
```json
{
  "status": "ok",
  "source": "census_geocoder",
  "county_name": "Sumter County",
  "state_fips": "13",
  "county_fips": "261",
  "land_use": "",
  "confidence": 70
}
```

### Log File
```
logs/agent3_parcel.log
```

---

## AGENT 4 — Building Classification

### Purpose
Classifies each address as SFH / MDU / Commercial / Unknown using:
- Microsoft Building Footprints (via reference BuildingDataAgent)
- Street View metadata (Google API) for coverage checks
- Source address metadata from `addresses.raw_metadata['old']`

### Input (reads from)
| Table | What is read |
|-------|--------------|
| `addresses` | `source_latitude`, `source_longitude`, `latitude`, `longitude`, `raw_metadata` |
| `agent1_results` | `smarty_lat`, `smarty_lon`, `chosen_standardized_address`, `structure_hint` |
| `agent_results` (agent2) | `data.latitude`, `data.longitude`, `data.formatted_address` |
| `addresses.raw_metadata['old']` | Legacy bundle used as building-agent input |

### External APIs Called
| Service | Purpose |
|---------|---------|
| **Google Street View Metadata API** | Checks coverage before footprint query |
| **BuildingDataAgent (reference)** | Footprint area, type, unit count from local state files |

### Output (writes to)

#### Table: `agent_results`  (agent_name = `"agent4_building"`)
The `data` column (JSON) contains:

```json
{
  "status": "ok",
  "structure_type": "SFH",
  "structure_type_detail": "single_family_residential",
  "confidence": 80,
  "is_mdu": false,
  "unit_count": 1,
  "footprint_area_sqm": 142.5,
  "footprint_source": "microsoft_footprints",
  "address_used": "1603 LAFAYETTE ST, AMERICUS, GA 31709",
  "lat": 32.086591,
  "lon": -84.241345,
  "sv_available": true
}
```

#### Also writes to `addresses.raw_metadata`
- Sets `addresses.raw_metadata['buildings_address']` — full Agent 4 enrichment bundle

### Log File
```
logs/agent4_building.log
```

---

## AGENT 5 — Street View + Azure Vision Analysis

### Purpose
Downloads multi-variant Google Street View imagery (front, oblique, satellite/ESRI fallback),
sends to Azure Computer Vision for detailed property analysis:
OCR house-number validation, structure type, unit count, floor count, mailbox count,
commercial signage, construction status.

### Input (reads from)
| Table | What is read |
|-------|--------------|
| `addresses` | `latitude`, `longitude`, `raw_address`, `city`, `state`, `zip_code`, `raw_metadata` |
| `agent1_results` | `smarty_lat`, `smarty_lon`, `chosen_standardized_address` |

### External APIs Called
| Service | Purpose |
|---------|---------|
| **Google Street View Static API** | Download street-level imagery variants |
| **Google Street View Metadata API** | Check availability at coordinates |
| **ESRI ArcGIS REST API** | Satellite imagery fallback |
| **Azure Computer Vision** | OCR + object detection + scene classification |

### Output (writes to)

#### Table: `agent_results`  (agent_name = `"agent5_streetview"`)
The `data` column (JSON) contains:

```json
{
  "status": "ok",
  "structure_type": "SFH",
  "structure_type_detail": "detached_house",
  "confidence": 92,
  "imagery_source": "street_view",
  "is_mdu": false,
  "visible_units_min": 1,
  "visible_units_max": 1,
  "floor_count": 1,
  "multiple_entrances": false,
  "multiple_mailboxes": false,
  "commercial_signage": false,
  "under_construction": false,
  "image_quality": "clear",
  "ocr_house_number": "1603",
  "ocr_match": true,
  "ocr_confidence": 94,
  "images_analyzed": 3,
  "raw_vision": {
    "description": { "captions": [ {"text": "a house on a tree lined road", "confidence": 0.91} ] },
    "tags": ["tree", "road", "house", "outdoor"],
    "objects": [ {"object": "Building", "confidence": 0.89} ]
  }
}
```

When no imagery is available:
```json
{
  "status": "skipped",
  "reason": "no coordinates",
  "structure_type": "Unknown",
  "confidence": 0,
  "imagery_source": "none"
}
```

#### Also writes to `addresses.raw_metadata`
- Sets `addresses.raw_metadata['agent5_streetview']` — sync'd summary for quick access

#### Table: `address_results` (optional full record)
| Column | Description |
|--------|-------------|
| `structure_type` | SFH / MDU / Commercial / Unknown |
| `visible_units_min/max` | Unit count range |
| `floor_count` | Number of floors |
| `multiple_entrances` | Boolean |
| `multiple_mailboxes` | Boolean |
| `commercial_signage` | Boolean |
| `under_construction` | Boolean |
| `image_quality` | clear / blurry / blocked |
| `confidence` | 0–100 |
| `raw_vision` | **JSON** — full Azure Vision API response |
| `imagery_source` | street_view / satellite / esri / none |

#### Table: `address_images`
| Column | Description |
|--------|-------------|
| `result_id` | FK → address_results |
| `image_type` | front / oblique / satellite |
| `image_url` | Source URL |
| `image_data` | BYTEA — raw image binary |

### Log File
```
logs/agent5_streetview.log
```

---

## AGENT 6 — Final FTTH Classification

### Purpose
Synthesizes all prior agent outputs into a single FTTH suitability decision:
- Assigns `final_structure_type` (SFH / MDU_SMALL / MDU_LARGE / Commercial / Unknown)
- Sets `final_confidence` (0–100) weighted from Agents 1–5
- Assigns `ftth_priority` (HIGH / MEDIUM / LOW / SKIP)
- Writes a human-readable `validation_summary`

### Input (reads from)
| Table | What is read |
|-------|--------------|
| `addresses` | `raw_address`, `city`, `state`, `zip_code`, `source_latitude`, `source_longitude`, `raw_metadata` |
| `agent1_results` | `validation_status`, `confidence_score`, `exception_reason`, `structure_hint`, `smarty_lat`, `smarty_lon` |
| `agent_results` (agent2) | `data.latitude`, `data.longitude`, `data.confidence`, `data.formatted_address` |
| `agent_results` (agent3) | `data.land_use`, `data.county_name`, `data.source` |
| `agent_results` (agent4) | `data.structure_type`, `data.structure_type_detail`, `data.is_mdu`, `data.unit_count`, `data.confidence` |
| `agent_results` (agent5) | `data.structure_type`, `data.structure_type_detail`, `data.confidence`, `data.is_mdu` |

### Priority Logic
| Condition | Priority |
|-----------|---------|
| Agent 1 status = REJECT | SKIP |
| MDU with ≥8 units | HIGH |
| MDU with 2–7 units OR SFH | MEDIUM |
| Commercial | LOW |
| No data / Unknown | SKIP |

### Output (writes to)

#### Table: `agent_results`  (agent_name = `"agent6_final"`)
The `data` column (JSON) contains:

```json
{
  "status": "classified",
  "final_structure_type": "SFH",
  "final_is_mdu": false,
  "final_unit_count": 1,
  "final_confidence": 85,
  "ftth_priority": "MEDIUM",
  "validation_summary": "Validated SFH at 1603 Lafayette St, Americus GA (confidence=85). Agent1=VALID, Agent4=SFH, Agent5=SFH. Priority MEDIUM.",
  "a1_status": "VALID",
  "a1_score": 88,
  "a3_land_use": "Single Family Residential",
  "a3_county": "Sumter County",
  "a4_structure": "SFH",
  "a4_confidence": 80,
  "a5_structure": "SFH",
  "a5_confidence": 92,
  "final_latitude": 32.086591,
  "final_longitude": -84.241345,
  "final_source_agent": "agent2_geocoding"
}
```

When rejected by Agent 1:
```json
{
  "status": "rejected",
  "final_structure_type": "Unknown",
  "final_is_mdu": null,
  "final_unit_count": null,
  "final_confidence": 0,
  "ftth_priority": "SKIP",
  "validation_summary": "Address rejected by Agent 1 (score=12). Reason: no_usps_match",
  "a1_status": "REJECT"
}
```

### Log File
```
logs/agent6_final.log
```

---

## SHARED INFRASTRUCTURE TABLES

### `agent_tables` — Agent Registry
Each agent self-registers here on first run.
| Column | Description |
|--------|-------------|
| `agent_name` | Unique key: agent2_geocoding / agent3_parcel / agent4_building / agent5_streetview / agent6_final |
| `display_name` | Human label |
| `owner` | "system" |
| `description` | Plain text |
| `color_rules` | **JSON** — map legend rules for UI |

##### `color_rules` JSON Example (Agent 6)
```json
[
  {"field": "ftth_priority", "value": "HIGH",   "color": "#16a34a", "label": "High Priority"},
  {"field": "ftth_priority", "value": "MEDIUM", "color": "#2563eb", "label": "Medium Priority"},
  {"field": "ftth_priority", "value": "LOW",    "color": "#d97706", "label": "Low Priority"},
  {"field": "ftth_priority", "value": "SKIP",   "color": "#6b7280", "label": "Skip"}
]
```

### `agent_results` — Shared Agent Output Table
Agents 1–6 write here (Agent 1 writes in parallel with `agent1_results`). One row per (agent_name, address_id) pair.
| Column | Description |
|--------|-------------|
| `id` | PK |
| `agent_name` | FK → agent_tables.agent_name |
| `job_id` | UUID FK → ingestion_jobs |
| `address_id` | INTEGER FK → addresses |
| `data` | **JSON** — full agent output (see each agent above) |
| `created_at` / `updated_at` | Timestamps |

### `ingestion_logs`
| Column | Description |
|--------|-------------|
| `job_id` | FK → ingestion_jobs |
| `source_file` | Filename |
| `records_processed` | Count |
| `records_valid` | Count |
| `records_invalid` | Count |
| `records_duplicate` | Count |
| `status` | STARTED / COMPLETED / FAILED |
| `message` | Summary text |

### `dispatch_queue`
Controls which addresses are queued for agent processing.
| Column | Description |
|--------|-------------|
| `job_id` | FK |
| `address_id` | FK → addresses |
| `status` | PENDING / RUNNING / DONE / FAILED |
| `attempts` | Retry count |
| `last_error` | Last failure message |

---

## TABLE → AGENT MAPPING QUICK REFERENCE

| Table | Written by | Read by |
|-------|-----------|---------|
| `ingestion_jobs` | Ingestion | All agents (job lookup) |
| `addresses` | Ingestion | All agents (primary input) |
| `uploaded_source_tables` | Ingestion | — |
| `uploaded_source_records` | Ingestion | — |
| `ingestion_logs` | Ingestion | — |
| `dispatch_queue` | Pipeline runner | Pipeline runner |
| `agent_tables` | Each agent (self-register) | UI / Agent 6 |
| `agent1_results` | **Agent 1** | Agents 2, 3, 4, 5, 6 | Flat typed columns + `data` JSON blob |
| `agent_results` (agent1_address_validator) | **Agent 1** | Agents 2–6 | **NEW** — same JSON as agent1_results.data |
| `agent_results` (agent2_geocoding) | **Agent 2** | Agents 3, 4, 5, 6 |
| `agent_results` (agent3_parcel) | **Agent 3** | Agent 6 |
| `agent_results` (agent4_building) | **Agent 4** | Agent 6 |
| `agent_results` (agent5_streetview) | **Agent 5** | Agent 6 |
| `agent_results` (agent6_final) | **Agent 6** | UI / exports |
| `address_results` | Agent 5 (optional) | UI |
| `address_images` | Agent 5 (optional) | UI |
| `network_assets` | KMZ ingestion | — |

---

## JSON STORAGE SUMMARY

| Table | Column | Agent | What it stores |
|-------|--------|-------|----------------|
| `addresses` | `raw_metadata` | Ingestion + all agents | Source row data, final coords, agent scratch |
| `addresses` | `validation_errors` | Ingestion | Ingest-time field errors |
| `addresses` | `validation_warnings` | Ingestion | Ingest-time warnings |
| `uploaded_source_records` | `raw_data` | Ingestion | Original row as dict |
| `uploaded_source_records` | `canonical_data` | Ingestion | Normalized row |
| `agent_tables` | `color_rules` | Each agent | Map UI legend rules |
| `agent1_results` | (flat columns) | Agent 1 | **NEW: also has `data` JSON** — same blob as typed columns |
| `agent_results` | `data` | Agents 2–6 | Full agent output JSON per address |
| `address_results` | `raw_vision` | Agent 5 | Full Azure Vision API response |
| `network_assets` | `metadata_json` | Ingestion (KMZ) | KMZ placemark metadata |

---

## CACHE / FILESYSTEM FILES

| File | Used by | Purpose |
|------|---------|---------|
| `backend/vendor/address_validation_agent/cache/cache.json` | Agent 1 | In-memory address→result cache; keyed by canonical address; written at end of each run |
| `cache/reverse_geocode_cache.json` | Agent 2 (reverse) | lat/lon → address string cache |
| `logs/agent1_address_validation.log` | Agent 1 | Full validation trace per address |
| `logs/agent2_geocoding.log` | Agent 2 | Geocoding fallback chain per address |
| `logs/agent3_parcel.log` | Agent 3 | Regrid / TIGER / Census call trace |
| `logs/agent4_building.log` | Agent 4 | Footprint + SV metadata trace |
| `logs/agent5_streetview.log` | Agent 5 | Vision API + OCR results |
| `logs/agent6_final.log` | Agent 6 | Synthesis decisions |

---

## EXPORT OUTPUTS (UPDATED)

### CSV download
- Endpoint: `/api/export/csv?job_id=...`
- Returns a ZIP file containing:
  - `raw_data.csv` (raw input view + merge_status/merge_color)
  - `updated_data.csv` (final updated view + agent summaries + merge_status/merge_color)

### Excel download
- Endpoint: `/api/export/excel?job_id=...`
- Returns an `.xlsx` file with exactly 2 sheets:
  - `Raw Data`
  - `Updated Data`
- Both sheets apply row highlighting from `merge_color` (green/white/red/yellow).

---

## NEW TABLES (added in this structural update)

### `agent2_results` — Geocoding typed columns
Written by Agent 2 alongside `agent_results` (both written together for backward compat).

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK | |
| `job_id` | UUID FK | |
| `address_id` | INTEGER FK (UNIQUE) | One row per address |
| `geocoded_address` | TEXT | Full formatted address |
| `geocoded_street` | TEXT | Parsed street |
| `geocoded_city` | VARCHAR(255) | |
| `geocoded_state` | VARCHAR(32) | |
| `geocoded_country` | VARCHAR(64) | |
| `geocoded_zip` | VARCHAR(32) | 5-digit |
| `geocoded_zip_plus4` | VARCHAR(20) | ZIP+4 |
| `geocoded_latitude` | FLOAT | |
| `geocoded_longitude` | FLOAT | |
| `location_type` | VARCHAR(32) | ROOFTOP / RANGE_INTERPOLATED / etc |
| `geocoding_source` | VARCHAR(32) | google / osm / interpolation |
| `confidence` | INTEGER | |
| `status` | VARCHAR(32) | geocoded / fallback / failed |
| `data` | **JSON** | Full Agent 2 output |

---

### `agent3_results` — Parcel typed columns

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK | |
| `job_id` | UUID FK | |
| `address_id` | INTEGER FK (UNIQUE) | |
| `parcel_id` | VARCHAR(128) | |
| `owner` | TEXT | |
| `land_use` | TEXT | |
| `land_use_code` | VARCHAR(32) | |
| `county_name` | VARCHAR(255) | |
| `state` | VARCHAR(32) | |
| `state_fips` | VARCHAR(8) | |
| `county_fips` | VARCHAR(8) | |
| `parcel_source` | VARCHAR(32) | regrid / tigerline / census / nominatim |
| `match_type` | VARCHAR(32) | |
| `confidence` | INTEGER | |
| `status` | VARCHAR(32) | |
| `data` | **JSON** | Full Agent 3 output |

---

### `agent4_results` — Building typed columns

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK | |
| `job_id` | UUID FK | |
| `address_id` | INTEGER FK (UNIQUE) | |
| `structure_type` | VARCHAR(64) | SFH / MDU / Commercial / Unknown |
| `structure_type_detail` | VARCHAR(64) | |
| `is_mdu` | BOOLEAN | |
| `unit_count` | INTEGER | |
| `building_matched` | BOOLEAN | |
| `footprint_area_m2` | FLOAT | |
| `floor_count_est` | INTEGER | |
| `confidence` | INTEGER | |
| `imagery_source` | VARCHAR(32) | |
| `latitude` | FLOAT | Matched building centroid |
| `longitude` | FLOAT | |
| `status` | VARCHAR(32) | |
| `data` | **JSON** | Full Agent 4 output |

---

### `agent5_results` — Street View typed columns

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK | |
| `job_id` | UUID FK | |
| `address_id` | INTEGER FK (UNIQUE) | |
| `structure_type` | VARCHAR(64) | |
| `structure_type_detail` | VARCHAR(64) | |
| `is_mdu` | BOOLEAN | |
| `visible_units_min` | INTEGER | |
| `visible_units_max` | INTEGER | |
| `floor_count` | INTEGER | |
| `multiple_entrances` | BOOLEAN | |
| `multiple_mailboxes` | BOOLEAN | |
| `commercial_signage` | BOOLEAN | |
| `under_construction` | BOOLEAN | |
| `imagery_source` | VARCHAR(32) | street_view / satellite / esri / none |
| `image_quality` | VARCHAR(32) | clear / blurry / blocked |
| `confidence` | INTEGER | |
| `ocr_house_number` | VARCHAR(32) | OCR'd house number |
| `ocr_match` | BOOLEAN | |
| `status` | VARCHAR(32) | |
| `data` | **JSON** | Full Agent 5 output |

---

### `final_address_results` — 20-Column Flat UI Table
Written by Agent 6.  One row per address.  The canonical UI validation and export table.

| Column | Type | Group | Source |
|--------|------|-------|--------|
| `id` | INTEGER PK | | |
| `job_id` | UUID FK | | |
| `address_id` | INTEGER FK (UNIQUE) | | |
| `original_address` | TEXT | **Original** | Uploaded raw_address |
| `original_latitude` | FLOAT | **Original** | Uploaded coordinates |
| `original_longitude` | FLOAT | **Original** | Uploaded coordinates |
| `original_street` | TEXT | **Original** | Parsed first comma segment |
| `original_city` | VARCHAR(255) | **Original** | addresses.city |
| `original_country` | VARCHAR(64) | **Original** | Source country column |
| `original_zip` | VARCHAR(32) | **Original** | 5-digit ZIP from upload |
| `original_zip_code` | VARCHAR(20) | **Original** | ZIP+4 from Smarty/Melissa |
| `ai_address` | TEXT | **AI** | Best validated address |
| `ai_latitude` | FLOAT | **AI** | Final geocoded lat |
| `ai_longitude` | FLOAT | **AI** | Final geocoded lon |
| `ai_street` | TEXT | **AI** | Parsed from ai_address |
| `ai_city` | VARCHAR(255) | **AI** | Parsed from ai_address |
| `ai_country` | VARCHAR(64) | **AI** | Parsed from ai_address |
| `ai_zip` | VARCHAR(32) | **AI** | Parsed 5-digit ZIP |
| `ai_zip_code` | VARCHAR(20) | **AI** | ZIP+4 from geocoder |
| `ai_confidence_score` | INTEGER | **AI** | 0–100 weighted confidence |
| `ai_remarks` | TEXT | **AI** | Human-readable summary |
| `ai_type` | VARCHAR(64) | **AI** | SFH / MDU / Commercial |
| `ai_agent_name` | VARCHAR(100) | **AI** | Winning agent name |
| `ftth_priority` | VARCHAR(16) | FTTH | HIGH / MEDIUM / LOW / SKIP |
| `final_is_mdu` | BOOLEAN | FTTH | |
| `final_unit_count` | INTEGER | FTTH | |
| `agent1_summary` | **JSON** | Snapshots | Agent 1 key fields |
| `agent2_summary` | **JSON** | Snapshots | Agent 2 key fields |
| `agent3_summary` | **JSON** | Snapshots | Agent 3 key fields |
| `agent4_summary` | **JSON** | Snapshots | Agent 4 key fields |
| `agent5_summary` | **JSON** | Snapshots | Agent 5 key fields |
| `agent6_summary` | **JSON** | Snapshots | Full Agent 6 result |
| `ingest_payload` | **JSON** | Snapshots | Copy of original ingest_payload |

---

### `pipeline_flow_templates` — Flow Builder Templates (NEW)

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID PK | |
| `name` | VARCHAR(255) | Template name |
| `description` | TEXT | |
| `is_active` | BOOLEAN | Soft-delete flag |
| `created_by` | VARCHAR(100) | Username |
| `config` | **JSON** | Full flow definition (agents array) |

`config.agents[*]` also stores `pipeline_options` (sub-checkbox selections per agent), e.g.:

```json
{
  "agent_name": "agent2_geocoding",
  "enabled": true,
  "data_source": "raw_input",
  "confidence_thresholds": {"threshold_1": 60, "threshold_2": 40},
  "order": 1,
  "pipeline_options": {
    "enabled": true,
    "reverse_geocoder": true,
    "coord_validation": true,
    "google_geocoding": true,
    "osm_geocoding": true,
    "street_interpolation": true
  }
}
```
| `created_at` | DATETIME | |
| `updated_at` | DATETIME | |

### `job_pipeline_flows` — Job-to-Flow Audit Trail (NEW)

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID PK | |
| `job_id` | UUID FK | → ingestion_jobs |
| `template_id` | UUID FK | → pipeline_flow_templates |
| `flow_config` | **JSON** | Snapshot of template.config at Process time |
| `created_at` | DATETIME | |

---

## UPDATED TABLE → AGENT MAPPING

| Table | Written by | Read by | Notes |
|-------|-----------|---------|-------|
| `ingestion_jobs` | Ingestion | All agents | Job status tracking |
| `addresses` | Ingestion + all agents | All agents | Primary pipeline table |
| `uploaded_source_tables` | Ingestion | — | File-level metadata |
| `uploaded_source_records` | Ingestion + color step | — | Row-level with merge_status |
| `agent_tables` | Each agent | UI / Agent 6 | Color rules |
| `agent1_results` | **Agent 1** | Agents 2–6 | Flat typed columns + `data` JSON blob |
| `agent_results` (agent1_address_validator) | **Agent 1** | Agents 2–6 | **NEW** — parallel write, same JSON |
| `agent_results` (agent2_geocoding) | **Agent 2** | Agents 3–6 | Backward compat |
| `agent2_results` | **Agent 2** | Agent 6, UI | **NEW** typed columns |
| `agent_results` (agent3_parcel) | **Agent 3** | Agent 6 | Backward compat |
| `agent3_results` | **Agent 3** | Agent 6, UI | **NEW** typed columns |
| `agent_results` (agent4_building) | **Agent 4** | Agent 6 | Backward compat |
| `agent4_results` | **Agent 4** | Agent 6, UI | **NEW** typed columns |
| `agent_results` (agent5_streetview) | **Agent 5** | Agent 6 | Backward compat |
| `agent5_results` | **Agent 5** | Agent 6, UI | **NEW** typed columns |
| `agent_results` (agent6_final) | **Agent 6** | UI / exports | Backward compat |
| `final_address_results` | **Agent 6** | UI, exports, API | **NEW** 20-column flat table |
| `pipeline_flow_templates` | Flow Builder UI | Pipeline runner | **NEW** |
| `job_pipeline_flows` | Flow Builder UI | Pipeline runner | **NEW** |

---

## NEW API ENDPOINTS

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/flow-templates` | List all active flow templates |
| `POST` | `/api/flow-templates` | Create a new flow template |
| `PUT` | `/api/flow-templates/{template_id}` | Update an existing template |
| `POST` | `/api/jobs/{job_id}/set-flow` | Apply a flow template to a job |
| `GET` | `/api/jobs/{job_id}/flow` | Get the flow config for a job |
| `POST` | `/api/jobs/{job_id}/process` | Starts processing using Flow Builder config only (request body toggles ignored) |

---

## UI TABLE BEHAVIOR (Flow Builder Driven)

The Data Records table in the project detail page is now Flow Builder aware.

1. Column visibility follows Flow Builder agent toggles:
  - If an agent is disabled in the applied flow, that agent's table columns are hidden.
2. Sub-checkboxes inside each enabled agent control related column families:
  - Agent 2 `coord_validation` controls match/distance validation columns.
  - Agent 2 `reverse_geocoder` controls reverse-geocode confidence columns.
  - Agent 5 `azure_vision` controls OCR/vision scoring columns.
3. The table has a Demo View mode for organized presentation:
  - Shows a curated, business-friendly column order.
  - Displays active flow chips and per-group column counts for quick review.

This keeps table output aligned with what is actually configured and executed in Flow Builder.

---

## LIVE AGENT PIPELINE PANEL (Flow Builder Driven)

The "Agent Pipeline" card now shows flow-selection state live, not just runtime progress.

1. For each pipeline row, the UI shows:
  - `Selected` when that agent is enabled in the applied Flow Builder config
  - `Not selected` when disabled in Flow Builder (displayed as skipped by flow)
2. The currently executing row is highlighted with a live `Running now…` indicator.
3. Active sub-checkboxes for selected agents are shown as chips under each row.

This makes it clear which agents are configured to run, which are actively running, and which were intentionally excluded by Flow Builder.

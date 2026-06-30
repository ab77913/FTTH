# Building Data Agent

A spatial enrichment agent that matches geocoded addresses to Microsoft Building Footprints or Google Open Buildings, extracts geometric features, and returns a rule-based structure class hint (SFU / MDU / ANCHOR / UNRESOLVED) consumed by a downstream LightGBM classifier.

---

## Architecture

```
addresses (CSV)
      |
      v
 FootprintLoader          <- loads GeoJSON / CSV footprints
      |
      v
  SpatialIndex            <- STRtree (SQLite/dev) or ST_DWithin (PostGIS/prod)
      |           match result {footprint_id, geometry, area_m2, distance_m ...}
      v
 FeatureExtractor         <- area_m2, elongation_ratio, perimeter_complexity, floor_count_est
      |
      v
StructureHintClassifier   <- rule-based: SFU / MDU / ANCHOR / UNRESOLVED + signals
      |
      v
 enrich() output dict     -> LightGBM feature vector (Stage 4)
```

---

## Quickstart

```bash
pip install -r requirements.txt

# Run zero-setup demo (uses sample data, no DB required)
python scripts/run_demo.py
```

Output is printed as a pandas DataFrame and saved to `data/sample/demo_output.csv`.

---

## Configuration

Edit `config/settings.example.yaml`:

| Key | Default | Description |
|-----|---------|-------------|
| `backend` | `sqlite` | `sqlite` (dev, STRtree) or `postgis` (prod) |
| `prefer_source` | `ms` | Preferred footprint source during deduplication |
| `data.ms_dir` | `data/ms` | Directory of Microsoft GeoJSON files |
| `data.google_dir` | `data/google` | Directory of Google CSV files |
| `data.sample_footprints` | `data/sample/sample_footprints.geojson` | Demo footprints |
| `database.postgis_conn` | — | SQLAlchemy PostGIS connection string |
| `log.level` | `INFO` | Loguru level |
| `thresholds.search_radius_m` | `15` | Primary match radius in metres |
| `thresholds.fallback_radius_m` | `30` | Fallback match radius in metres |
| `thresholds.sfu_max_area_m2` | `650` | Max footprint area classified as SFU |
| `thresholds.mdu_min_area_m2` | `300` | Min footprint area classified as MDU |
| `thresholds.anchor_min_area_m2` | `5000` | Min footprint area classified as ANCHOR |
| `thresholds.elongation_mdu_threshold` | `3.0` | L/W ratio triggering MDU classification |

---

## Data Sources

- **Microsoft Building Footprints** — US state GeoJSON files  
  Download: https://github.com/microsoft/USBuildingFootprints  
  `python scripts/download_footprints.py --source ms --state Texas --output_dir data/ms`

- **Google Open Buildings** — tile CSV files (manual download required)  
  Visit: https://sites.research.google/open-buildings/  
  Place `.csv` / `.csv.gz` files in `data/google/` then run `scripts/load_footprints.py`.

---

## Pipeline Integration

`agent.enrich_batch()` returns one dict per input record. The following keys map directly into the LightGBM feature vector for Stage 4 classification:

| Key | Type | Description |
|-----|------|-------------|
| `footprint_area_m2` | float | Footprint area in square metres |
| `elongation_ratio` | float | L/W ratio of minimum rotated bounding rect |
| `perimeter_complexity` | float | Actual perimeter / convex hull perimeter |
| `floor_count_est` | int | Heuristic floor count |
| `footprint_source` | str | `ms` or `google` |
| `footprint_match_distance_m` | float | Distance from address point to footprint edge |
| `structure_hint` | str | `SFU`, `MDU`, `ANCHOR`, or `UNRESOLVED` |
| `hint_confidence` | float | Classifier confidence 0–1 |
| `hint_signals` | list[str] | Audit trail of rules that fired |
| `building_matched` | bool | Whether a footprint was found |

---

## Running Tests

```bash
pytest tests/ -v
```

Tests cover:
- `tests/test_spatial.py` — radius matching, fallback, batch match
- `tests/test_features.py` — area, elongation, complexity, floor count
- `tests/test_classifier.py` — all classification rules, batch, signals

---

## Output Fields

All fields returned by `agent.enrich(record)`:

| Field | Description |
|-------|-------------|
| `address_id` | Passed through from input |
| `lat`, `lon` | Passed through from input |
| `footprint_area_m2` | Projected area m² (None if unmatched) |
| `elongation_ratio` | L/W of bounding rect (None if unmatched) |
| `perimeter_complexity` | Convex complexity ratio (None if unmatched) |
| `floor_count_est` | Estimated floors (None if unmatched) |
| `footprint_source` | `ms` / `google` / None |
| `footprint_match_distance_m` | Metres from address to footprint edge |
| `structure_hint` | `SFU` / `MDU` / `ANCHOR` / `UNRESOLVED` |
| `hint_confidence` | 0.0 – 0.95 |
| `hint_signals` | List of rule strings for audit |
| `building_matched` | `True` if footprint found within radius |

---

## Project Structure

```
building_agent/
├── config/
│   ├── settings.example.yaml    # main config
│   └── thresholds.yaml          # classification thresholds
├── data/
│   ├── ms/                      # Microsoft GeoJSON footprints
│   ├── google/                  # Google CSV footprints
│   └── sample/
│       ├── sample_addresses.csv
│       ├── sample_footprints.geojson
│       └── demo_output.csv      # created by run_demo.py
├── scripts/
│   ├── run_demo.py              # zero-setup demo
│   ├── run_pipeline.py          # CLI batch enrichment
│   ├── download_footprints.py   # download MS/Google data
│   └── load_footprints.py       # load into spatial backend
├── src/
│   ├── __init__.py
│   ├── utils.py                 # config, logger, haversine, timer
│   ├── loader.py                # FootprintLoader
│   ├── spatial.py               # SpatialIndex (STRtree / PostGIS)
│   ├── features.py              # FeatureExtractor
│   ├── classifier.py            # StructureHintClassifier
│   └── agent.py                 # BuildingDataAgent (orchestrator)
├── tests/
│   ├── test_spatial.py
│   ├── test_features.py
│   └── test_classifier.py
├── requirements.txt
└── README.md
```

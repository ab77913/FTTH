"""
FTTH Agent Pipeline
===================
Sequential 6-agent pipeline that enriches every ingested address record.

Agents (run in order by pipeline_runner.py):
  Agent 2 — Unified Geocoding       (agent2_geocoding.py)
             Runs first on every job: reverse geocoding for coord-only rows
             (Nominatim / Google), coord validation, then forward geocoding
             via Google → OSM → street interpolation.

  Agent 1 — Address Validation     (agent1_address_validation.py)
             Smarty Streets + Melissa APIs to validate, parse and
             standardize addresses. Writes to agent1_results table.

  Agent 3 — Parcel Lookup           (agent3_parcel.py)
             Regrid / TIGER-line parcel API to fetch land-use,
             zoning, parcel APN and owner details.

  Agent 4 — Building Classification (agent4_building.py)
             Google Maps + ML classifier to determine structure type
             (SFU / MDU / Commercial / Unknown) and unit count.

  Agent 5 — Street View Analysis    (agent5_streetview.py)
             Google Street View Static API + vision model to confirm
             building type and surface-level condition.

  Agent 6 — Finalization            (agent6_finalization.py)
             Aggregates signals from Agents 1–5, resolves conflicts,
             computes a final confidence score and structure verdict.

Support:
  reverse_geocoder.py  — Reverse-geocode implementation used by Agent 2.
  pipeline_runner.py   — Orchestrator: runs all agents in sequence with
                         progress callbacks streamed to the UI via SSE.
"""

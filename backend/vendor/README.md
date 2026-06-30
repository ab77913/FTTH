# Vendored third-party agent code

Runtime dependencies for Agents 1, 4, and 5. Paths are defined in
`data_ingestion/config/paths.py`.

| Folder | Used by | Purpose |
|--------|---------|---------|
| `address_validation_agent/` | Agent 1 | Smarty + Melissa validation (`src/` package) |
| `building_agent/` | Agent 4 | Microsoft footprint fallback classifier |
| `street_view_analysis/` | Agent 5 | Legacy cache/output paths for Street View pipeline |

Do not rename these folders without updating `paths.py` and the agent modules that import them.

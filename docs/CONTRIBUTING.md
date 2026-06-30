# Contributing

## Workflow

1. Create a feature branch from `main`.
2. Make focused changes; preserve API contracts.
3. Run tests and lint locally before pushing.
4. Open a PR â€” CI runs pytest + ruff (see `.github/workflows/ci.yml`).

## Tests

```powershell
.venv\Scripts\python.exe -m pytest tests/ -q
```

Add a regression test for every bug fix.

## Code layout

- **Backend changes** â†’ `backend/data_ingestion/` or `backend/api/server.py`
- **Frontend API/auth** â†’ `frontend/src/api/auth.js`
- **Frontend UI** â†’ `frontend/src/pages/` and `frontend/src/components/`
- **Ingestion** â†’ `extractors/`, `parsers/canonical_mapper.py`, `utils/csv_kmz_merge.py`

## Do not

- Commit `.env` or secrets
- Rename public `/api/*` routes without migration plan
- Disable tests to make CI green


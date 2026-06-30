# FTTH Backend

Python backend for the Meridian FTTH Data Ingestion platform.

## Folder structure

```
backend/
  api/
    server.py           # FastAPI app — HTTP routes, auth, uploads, SSE progress
  celery_worker.py      # Celery worker entrypoint
  vendor/               # Third-party agent code (Agents 1, 4, 5)
  data_ingestion/       # Core pipeline package
    agents/             # Pipeline agents 0–7
    config/             # Settings, logging, paths, startup validation
    database/           # SQLAlchemy models, sessions, repositories
    extractors/         # CSV, Excel, KML, KMZ parsers
    middleware/         # Error handlers, access log, security headers
    worker/             # Celery app and task definitions
    utils/              # Shared helpers (rate limits, uploads, OCR, …)
```

## Running locally

From the repo root (recommended):

```bat
python api_server.py
```

The root `api_server.py` is a thin shim that adds `backend/` to `PYTHONPATH` and imports `backend.api.server`.

Direct module entry (same behavior):

```bat
set PYTHONPATH=%CD%;%CD%\backend
python -m backend.api.server
```

Celery worker:

```bat
python celery_worker.py
```

Or directly:

```bat
python backend/celery_worker.py
```

## Import paths

| Import | Location |
|--------|----------|
| `data_ingestion.*` | `backend/data_ingestion/` (requires `backend` on `PYTHONPATH`) |
| `backend.api.server` | `backend/api/server.py` (requires repo root on `PYTHONPATH`) |

## Data directories (repo root)

| Path | Purpose |
|------|---------|
| `uploads/` | Uploaded CSV/KMZ/XLSX files |
| `data/` | SQLite account DB and local runtime files |
| `.env` | API keys and secrets (not committed) |

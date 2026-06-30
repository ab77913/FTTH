# FTTH Production â€” Architecture Map (Local Development)

This document maps the **current** system layout so each feature can be edited manually without hunting through the repo.

## Technology Stack

| Layer | Technology |
|-------|------------|
| Frontend | Modular SPA under `frontend/` â€” React 18 (CDN), Babel JSX modules, Tailwind CSS (CDN), Leaflet maps |
| Backend | Python 3.10+, FastAPI, Uvicorn |
| ORM | SQLAlchemy 2.x |
| Database | PostgreSQL 16 (local Windows service or Docker) |
| Cache / progress | Redis 7 |
| Task queue | Celery (Redis broker) |
| Agent messaging | RabbitMQ (A2A bus) |
| Reverse proxy | Nginx (port 80) â†’ FastAPI (8000) |
| Auth | JWT (python-jose), stateless Bearer tokens |
| Tests | pytest, fakeredis |

## Service & Port Map

| Service | Port | Startup | Health |
|---------|------|---------|--------|
| Nginx | 80 | `start_app.bat` or Docker | Proxy to API |
| FastAPI | 8000 | `python api_server.py` | `POST /api/login` |
| PostgreSQL | 5432 | Windows service / Docker | `pg_isready` |
| Redis | 6379 | Docker `ftth-redis` | `redis-cli ping` |
| RabbitMQ | 5672 | Docker `ftth-rabbitmq` | management UI :15672 |
| Celery worker | â€” | `celery -A data_ingestion.worker.celery_app worker` | Flower :5555 |
| Ollama (optional) | 11434 | `tools/ollama/` | local only |

## Repository Layout (Edit Here)

```
FTTH_PRODUCTION/
â”œâ”€â”€ api_server.py              # Root shim â†’ backend.api.server
â”œâ”€â”€ start_app.bat              # Local full-stack launcher (Windows)
â”œâ”€â”€ docker-compose.yml         # Postgres, Redis, RabbitMQ, Nginx, Flower
â”œâ”€â”€ nginx.conf                 # Reverse proxy, 500MB upload limit
â”œâ”€â”€ frontend/
â”‚   â”œâ”€â”€ public/
â”‚   â”‚   â”œâ”€â”€ index.html         # HTML shell + script load order
â”‚   â”‚   â””â”€â”€ images/            # Static images
â”‚   â””â”€â”€ src/
â”‚       â”œâ”€â”€ api/               # HTTP client, auth, data API
â”‚       â”œâ”€â”€ components/        # Reusable UI (modals, table, layout)
â”‚       â”œâ”€â”€ pages/             # Route screens (projects, map, login, â€¦)
â”‚       â”œâ”€â”€ app/               # Root App, routing bootstrap
â”‚       â””â”€â”€ config/            # Pipeline / flow-builder defaults
â”œâ”€â”€ backend/
â”‚   â”œâ”€â”€ api/
â”‚   â”‚   â””â”€â”€ server.py          # FastAPI routes (~5,900 lines)
â”‚   â””â”€â”€ data_ingestion/
â”‚       â”œâ”€â”€ ingestion_service.py   # Upload â†’ extract â†’ map â†’ merge â†’ save
â”‚       â”œâ”€â”€ agents/                # Agent 0â€“7 implementations
â”‚       â”œâ”€â”€ extractors/            # CSV, Excel, KML, KMZ parsers
â”‚       â”œâ”€â”€ parsers/
â”‚       â”‚   â””â”€â”€ canonical_mapper.py
â”‚       â”œâ”€â”€ utils/
â”‚       â”‚   â”œâ”€â”€ csv_kmz_merge.py
â”‚       â”‚   â””â”€â”€ address_match.py
â”‚       â”œâ”€â”€ database/
â”‚       â”‚   â”œâ”€â”€ models.py
â”‚       â”‚   â””â”€â”€ db.py
â”‚       â””â”€â”€ worker/
â”‚           â”œâ”€â”€ celery_app.py
â”‚           â””â”€â”€ tasks.py
â”‚   â””â”€â”€ vendor/                    # Vendored agent prototypes (Agents 1, 4, 5)
â”œâ”€â”€ tests/                         # pytest suite
â”œâ”€â”€ docs/                          # Architecture, deployment, runbook
â”œâ”€â”€ scripts/                       # emit_env_bat, celery dev watcher, SQL init
â”œâ”€â”€ tools/ollama/                  # Optional Ollama chat server
â”œâ”€â”€ examples/                      # Sample CSV/KML fixtures
```

## Frontend (`frontend/`)

Modular SPA â€” see [frontend/README.md](frontend/README.md). Scripts load from `/assets/` (mapped to `frontend/src/`).

| Path | Purpose |
|------|---------|
| `src/api/auth.js` | Login, JWT, `apiFetch` |
| `src/api/data-api.js` | Jobs, records, uploads |
| `src/pages/projects-page.jsx` | Job dashboard |
| `src/pages/map-page.jsx` | Leaflet map + Street View |
| `src/components/upload-modal.jsx` | File upload UI |
| `src/config/pipeline.js` | Agent flow-builder defaults |

**Routes:** hash-based (`#/`, `#/jobs/{id}`).

## Backend API (preserved contracts)

Key endpoints (all under `/api/`):

- `POST /upload` â€” CSV/KMZ/Excel ingest
- `POST /jobs/{id}/process` â€” run agent pipeline
- `GET /jobs/{id}/progress` â€” SSE progress stream
- `GET /records`, `/records/geo` â€” paginated address data
- `GET /export/csv`, `/export/kmz`, `/export/excel`

## Upload â†’ Address Flow

1. `POST /api/upload` â†’ `IngestionService.ingest_file` or `ingest_files_grouped`
2. Extractor by extension: `CSVExtractor`, `KMLExtractor`, `KMZExtractor`
3. `CanonicalMapper.map_records()` â€” column aliases â†’ `raw_address`, lat/lon
4. If multi-file: `apply_csv_kmz_merge()` â€” match CSV rows to KMZ geometry
5. `validate_and_deduplicate()` â†’ `save_addresses()` PostgreSQL

## Agent Pipeline Order

Agent 0 (reverse geocode) â†’ 1 (Smarty) â†’ 2 (geocode) â†’ 3 (parcel) â†’ 4 (building) â†’ 5-0 (offline OCR) â†’ 5 (Street View) â†’ 6 (final) â†’ 7 (neighborhood)

Orchestrated in `data_ingestion/agents/pipeline_runner.py`.

## Local Commands

```powershell
# Install
py -3.10 -m venv .venv
.venv\Scripts\pip install -r requirements.txt

# Tests
.venv\Scripts\python.exe -m pytest tests/ -q

# Lint
.venv\Scripts\python.exe -m ruff check backend tests api_server.py

# Start full stack
start_app.bat
```

## Environment Variables

See `.env.example`. Secrets belong in `.env` (never commit). Key groups: `DATABASE_URL`, `REDIS_URL`, `GOOGLE_*`, `SMARTY_*`, `AZURE_VISION_*`.


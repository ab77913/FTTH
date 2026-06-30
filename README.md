# FTTH Production Platform

Meridian FTTH address ingestion and 8-agent validation pipeline (Agents 0â€“7).

## Quick start (local Windows)

```powershell
copy .env.example .env
# Edit .env with your API keys

py -3.10 -m venv .venv
.venv\Scripts\pip install -r requirements.txt

start_app.bat
```

`start_app.bat` loads API keys from `.env` only (no secrets in the batch file). Copy `.env.example` first.

Open http://localhost â€” default users: `ftth_team` / `Meridian@2026`

## Architecture

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for service map, folder layout, and edit guide.

## Development

| Command | Purpose |
|---------|---------|
| `start_app.bat` | Full stack: API + Celery + Nginx + Docker Redis/RabbitMQ |
| `.venv\Scripts\python.exe api_server.py` | API only (hot reload when `FTTH_DEV_RELOAD=1`) |
| `.venv\Scripts\python.exe -m pytest tests/ -q` | Run tests |
| `.venv\Scripts\python.exe -m ruff check backend tests api_server.py` | Lint |

## Project layout

| Folder | Purpose |
|--------|---------|
| `frontend/` | React UI â€” see [frontend/README.md](frontend/README.md) |
| `backend/` | FastAPI + pipeline â€” see [backend/README.md](backend/README.md) |
| `api_server.py` | Root entry shim (imports `backend.api.server`) |
| `backend/celery_worker.py` | Celery worker (or root `celery_worker.py` shim) |
| `tests/` | pytest suite |
| `docs/` | Architecture, deployment, runbook |
| `scripts/` | Ops utilities (`emit_env_bat.py`, Celery dev watcher) |
| `scripts/dev/` | Ad-hoc debugging scripts |
| `tools/ollama/` | Optional Ollama chat server |

## Upload formats

- **CSV** â€” address columns auto-detected (`Address`, `Street Address`, etc.)
- **KMZ/KML** â€” point placemarks with street addresses extracted
- **CSV + KMZ together** â€” upload both in one request; addresses merge with geometry

## Health checks

- Liveness: `GET /api/health`
- Readiness: `GET /api/ready` (includes PostgreSQL check)

## Documentation

- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) â€” production deployment
- [docs/RUNBOOK.md](docs/RUNBOOK.md) â€” operations troubleshooting
- [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) â€” development workflow
- [docs/SECURITY.md](docs/SECURITY.md) â€” security notes

## Frontend structure

See [frontend/README.md](frontend/README.md). Key paths:

- `frontend/public/index.html` â€” HTML shell (loads `/assets/...` scripts)
- `frontend/src/api/auth.js` â€” auth + API client
- `frontend/src/pages/` â€” screen components (projects, map, login, â€¦)


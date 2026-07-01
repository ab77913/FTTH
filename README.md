# FTTH Production Platform

[![CI/CD](https://github.com/ab77913/FTTH/actions/workflows/ci.yml/badge.svg)](https://github.com/ab77913/FTTH/actions/workflows/ci.yml)

Meridian FTTH address ingestion and 8-agent validation pipeline (Agents 0-7).

## Quick start (local Windows)

```powershell
copy .env.example .env
# Edit .env with your API keys

py -3.10 -m venv .venv
.venv\Scripts\pip install -r requirements.txt

start_app.bat
```

`start_app.bat` loads API keys from `.env` only. Copy `.env.example` first.

Open http://localhost. Default users: `ftth_team` / `Meridian@2026`.

## Architecture

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for service map, folder layout, and edit guide.

## Team Workflow

- `main` is the production branch.
- `develop` is the shared development branch.
- Create feature branches from `develop` and open pull requests back into `develop`.
- Release changes by opening a pull request from `develop` into `main`.

See [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) and [docs/CI_CD.md](docs/CI_CD.md).

## Development

| Command | Purpose |
|---------|---------|
| `start_app.bat` | Full stack: API + Celery + Nginx + Docker Redis/RabbitMQ |
| `.venv\Scripts\python.exe api_server.py` | API only, with hot reload when `FTTH_DEV_RELOAD=1` |
| `.venv\Scripts\python.exe -m pytest tests/ -q` | Run tests |
| `.venv\Scripts\python.exe -m ruff check backend tests api_server.py` | Lint |

## Project Layout

| Folder | Purpose |
|--------|---------|
| `frontend/` | React UI. See [frontend/README.md](frontend/README.md) |
| `backend/` | FastAPI + pipeline. See [backend/README.md](backend/README.md) |
| `api_server.py` | Root entry shim that imports `backend.api.server` |
| `backend/celery_worker.py` | Celery worker, or root `celery_worker.py` shim |
| `tests/` | pytest suite |
| `docs/` | Architecture, deployment, runbook, CI/CD docs |
| `scripts/` | Ops utilities such as `emit_env_bat.py` and Celery dev watcher |
| `scripts/dev/` | Ad-hoc debugging scripts |
| `tools/ollama/` | Optional Ollama chat server |

## Upload Formats

- CSV: address columns auto-detected, such as `Address` or `Street Address`.
- KMZ/KML: point placemarks with street addresses extracted.
- CSV + KMZ together: upload both in one request; addresses merge with geometry.

## Health Checks

- Liveness: `GET /api/health`
- Readiness: `GET /api/ready`, including PostgreSQL check

## Documentation

- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) - production deployment
- [docs/RUNBOOK.md](docs/RUNBOOK.md) - operations troubleshooting
- [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) - development workflow
- [docs/CI_CD.md](docs/CI_CD.md) - GitHub Actions and release flow
- [docs/SECURITY.md](docs/SECURITY.md) - security notes

## Frontend Structure

See [frontend/README.md](frontend/README.md). Key paths:

- `frontend/public/index.html` - HTML shell that loads `/assets/...` scripts
- `frontend/src/api/auth.js` - auth and API client
- `frontend/src/pages/` - screen components for projects, map, login, and related views
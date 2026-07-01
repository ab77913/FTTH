# Contributing

## Branch Workflow

This repository uses `main` as the production branch and `develop` as the shared development branch.

1. Pull the latest `develop` branch.
2. Create a focused feature branch from `develop`.
3. Make the change and add tests for bug fixes or behavior changes.
4. Push your feature branch.
5. Open a pull request into `develop`.
6. After CI passes and review is complete, merge into `develop`.
7. Promote `develop` to `main` through a release pull request.

Recommended branch names:

```text
feature/short-description
fix/short-description
chore/short-description
docs/short-description
```

## Pull Request Rules

A pull request should be small enough to review safely and should include:

- A clear summary of what changed.
- The tests that were run.
- Screenshots or sample exports for UI/export changes when useful.
- No real secrets, API keys, passwords, or private customer data.

## Local Validation

Run the important checks before opening a PR:

```powershell
.venv\Scripts\python.exe -m ruff check backend tests api_server.py celery_worker.py scripts/emit_env_bat.py scripts/celery_dev_worker.py scripts/production_readiness_check.py
.venv\Scripts\python.exe scripts\production_readiness_check.py
.venv\Scripts\python.exe -m pytest tests/ -q --tb=short
```

## CI/CD Gates

GitHub Actions runs on pushes and pull requests for `develop` and `main`:

- Ruff lint
- Python syntax compile
- Production readiness audit
- Static frontend checks
- API and pipeline pytest suite on supported Python versions
- Dependency and secret scans
- Docker image build and smoke test

Docker images publish to GitHub Container Registry only from `main` or `v*` tags. Production deploys run only from `main` when manually requested or when `AUTO_DEPLOY_PRODUCTION=true` is configured.

## Code Layout

- Backend API: `backend/api/server.py`
- Pipeline agents: `backend/data_ingestion/agents/`
- Ingestion utilities: `backend/data_ingestion/utils/`
- Frontend UI: `frontend/src/pages/` and `frontend/src/components/`
- Tests: `tests/`
- CI/CD: `.github/workflows/`

## Do Not

- Commit real `.env` secrets or private credentials.
- Commit generated caches, local virtual environments, or machine-specific logs.
- Rename public `/api/*` routes without a migration plan.
- Disable tests to make CI pass.

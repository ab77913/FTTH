# CI/CD Pipeline

This repository uses GitHub Actions for pull-request validation, security checks, Docker image publishing, and optional production deployment.

## Branch Model

- `main` is the production branch.
- `develop` is the shared integration branch for active development.
- Developers create `feature/*`, `fix/*`, `chore/*`, or `docs/*` branches from `develop`.
- Pull requests should target `develop` first.
- Release pull requests promote `develop` into `main`.

## Workflows

- `.github/workflows/ci.yml` - quality gates, tests, security audit, Docker build/publish, optional deploy.
- `.github/workflows/codeql.yml` - CodeQL security analysis on pushes, PRs, weekly schedule, and manual runs.
- `.github/dependabot.yml` - weekly dependency updates for Python and GitHub Actions.

## Main Pipeline Stages

1. Quality gates
   - Install Python dependencies on the default CI Python version.
   - Run Ruff lint.
   - Compile Python source without writing `.pyc` files.
   - Run `scripts/production_readiness_check.py`.
   - Run static frontend and production readiness tests.

2. API and pipeline tests
   - Starts PostGIS/PostgreSQL, Redis, and RabbitMQ services.
   - Runs API contract, auth, health, security, map API, and full pytest checks.
   - Tests supported Python versions `3.10`, `3.11`, and `3.12`.
   - Uploads debug artifacts on failure.

3. Security audit
   - Runs `pip-audit` against `requirements.txt`.
   - Runs Gitleaks secret scanning.

4. Docker build and publish
   - Builds the production Docker image for every PR and branch validation.
   - Runs an import smoke test inside the image.
   - Publishes to GitHub Container Registry only from `main` and `v*` tags.

5. Optional production deployment
   - Runs only from `main`.
   - Requires manual `workflow_dispatch` with `deploy=true`, or repository/environment variable `AUTO_DEPLOY_PRODUCTION=true`.
   - Uses SSH to run `docker compose pull && docker compose up -d` on the target server.

## Required GitHub Secrets for Deploy

Configure these in the `production` GitHub Environment or repository secrets:

- `PROD_SSH_HOST` - deployment host/IP.
- `PROD_SSH_USER` - SSH username.
- `PROD_SSH_KEY` - private SSH key with access to the host.

## Required GitHub Variables for Deploy

- `PROD_APP_DIR` - directory on the server containing `docker-compose.yml`.
- `AUTO_DEPLOY_PRODUCTION` - optional. Set to `true` to deploy automatically on `main` after successful image build.

## Recommended Branch Protection

Configure branch protection in GitHub for `main` and `develop`:

- Require pull request before merging.
- Require at least one approval.
- Require status checks to pass.
- Require branches to be up to date before merging.
- Block force pushes.
- Restrict direct pushes to `main`.

Suggested required status checks:

- `Quality gates`
- `API and pipeline tests (Python 3.10)`
- `API and pipeline tests (Python 3.11)`
- `API and pipeline tests (Python 3.12)`
- `Security audit`
- `Docker build and publish`
- `Analyze (python)`
- `Analyze (javascript-typescript)`

## Container Registry

Published image tags use:

- Branch name, for example `main`.
- Git tag, for example `v1.0.0`.
- Commit SHA, for example `sha-abc1234`.
- `latest` for the default branch.

The image name is:

```text
ghcr.io/<owner>/<repo>/ftth-api
```

## Local Parity Commands

Run the important gates locally before pushing:

```powershell
python -m ruff check backend tests api_server.py celery_worker.py scripts/emit_env_bat.py scripts/celery_dev_worker.py scripts/production_readiness_check.py
python scripts/production_readiness_check.py
python -m pytest tests/ -q --tb=short
docker build -t ftth-api:local .
```

## Secret Handling

CI uses placeholder values only. Store real production values in GitHub Secrets, GitHub Environments, or server-side `.env` files. Do not commit real API keys, passwords, tokens, or private customer data.

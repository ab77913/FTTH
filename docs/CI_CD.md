# CI/CD Pipeline

This repository uses GitHub Actions for quality gates, tests, security scanning, Docker image publishing, and optional production deployment.

## Workflows

- `.github/workflows/ci.yml` - main CI/CD pipeline.
- `.github/workflows/codeql.yml` - scheduled and pull-request CodeQL security analysis.
- `.github/dependabot.yml` - weekly dependency updates for Python and GitHub Actions.

## Main Pipeline Stages

1. **Quality gates**
   - Install Python dependencies on the default minimum supported Python version (`3.10`).
   - Run Ruff lint.
   - Compile Python source without writing `.pyc` files.
   - Run `scripts/production_readiness_check.py`.
   - Run static frontend and production readiness tests.

2. **API and pipeline tests**
   - Starts PostgreSQL, Redis, and RabbitMQ services.
   - Runs API contract, auth, health, security, map API, and the full pytest suite on Python `3.10`, `3.11`, `3.12`, and `3.13`.
   - Uploads debug artifacts on failure.

3. **Security audit**
   - Runs `pip-audit` against `requirements.txt`.
   - Runs Gitleaks secret scanning.

4. **Docker build and publish**
   - Builds the production Docker image.
   - Runs an import smoke test inside the image.
   - Publishes to GitHub Container Registry on `main` and `v*` tags.

5. **Optional production deployment**
   - Runs only from `main` when manually requested with `workflow_dispatch.deploy=true` or when `AUTO_DEPLOY_PRODUCTION=true` is set as a repository/environment variable.
   - Uses SSH to run `docker compose pull && docker compose up -d` on the target server.

## Required GitHub Secrets for Deploy

Configure these in the `production` GitHub Environment or repository secrets:

- `PROD_SSH_HOST` - deployment host/IP.
- `PROD_SSH_USER` - SSH username.
- `PROD_SSH_KEY` - private SSH key with access to the host.

## Required GitHub Variables for Deploy

- `PROD_APP_DIR` - directory on the server containing `docker-compose.yml`.
- `AUTO_DEPLOY_PRODUCTION` - optional. Set to `true` to deploy automatically on `main` after successful image build.

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

The Docker build context excludes `.env` through `.dockerignore`. CI also runs Gitleaks. If you intentionally keep `.env` in a private repository, expect secret scanning to fail until you configure an explicit, reviewed allowlist. The safer practice is to store real values in GitHub Secrets or server-side `.env` files only.


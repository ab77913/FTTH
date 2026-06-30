# Production Readiness Checklist

This project already includes API health checks, Swagger/OpenAPI docs, security middleware, startup validation, Docker support, and contract tests. Before publishing or deploying, run the readiness audit and review the items below.

## One-command Audit

```powershell
python scripts\production_readiness_check.py
```

For a private deployment machine, also inspect the real `.env` values:

```powershell
python scripts\production_readiness_check.py --include-real-env
```

The audit fails with a non-zero exit code when a critical production issue is found.

## Required Runtime URLs

- Swagger UI: `/docs`
- ReDoc: `/redoc`
- OpenAPI JSON: `/openapi.json`
- Liveness: `/api/health`
- Readiness: `/api/ready`

## Security

- Use a private repository if `.env` is committed.
- Prefer not committing `.env`; commit `.env.example` and inject real secrets through the host or CI/CD.
- Set a long random `JWT_SECRET_KEY` in production.
- Set `FTTH_CORS_ORIGINS` to explicit domains, never `*`.
- Rotate any API key that has ever been committed to a public repository.

## Database

- Use a non-default database password.
- Keep `DATABASE_URL` out of public logs.
- Use `FTTH_ALLOW_LOCAL_DATABASE=1` only for intentional single-host deployments.
- Confirm `/api/ready` reports `database: ok` before traffic is sent to the app.

## Storage

`/api/ready` checks that these runtime folders are writable:

- `data/`
- `uploads/`
- `logs/`

If any storage check fails, fix filesystem permissions before starting production jobs.

## AI and Agent Operations

- Configure provider keys through `.env` or deployment secrets.
- Validate Google Maps/Street View keys using the app settings UI before running full jobs.
- For local Ollama workflows, confirm the Ollama server and model are available before enabling OCR-heavy flows.

## DevOps

- Run tests before publish/deploy:

```powershell
python -m pytest tests\test_api_contracts.py tests\test_health_endpoints.py tests\test_startup_checks.py tests\test_static_frontend.py tests\test_production_readiness.py
```

- Use `/api/ready` for load balancer readiness.
- Use `/api/health` for lightweight container liveness.
- Keep Swagger enabled for private/internal deployments; restrict network access at the proxy/firewall layer for public deployments.
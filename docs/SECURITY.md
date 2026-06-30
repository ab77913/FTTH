# Security

## Secrets

- Store in `.env` or a secret manager — never in git or startup scripts.
- `start_app.bat` loads secrets from `.env` via `scripts/emit_env_bat.py`.
- App Settings UI writes to `.env` on the server; values are masked in GET responses.
- `.env` is gitignored; copy from `.env.example` for local setup.

## Rate limiting

- Login, forgot-password, and reset-password endpoints are rate limited per client IP.
- Configure via `FTTH_LOGIN_RATE_LIMIT` and `FTTH_LOGIN_RATE_WINDOW`.

## File uploads

- Validated server-side: extension, size, content signature, ZIP entry limits.
- Configure via `FTTH_MAX_UPLOAD_BYTES` and `FTTH_MAX_ZIP_ENTRIES`.
- Max body size: 500MB (Nginx `client_max_body_size`)
- Uploads stored under `uploads/` with server-generated paths

## Authentication

- JWT Bearer tokens (`python-jose`)
- Tokens in `sessionStorage` on the frontend
- Server enforces auth on all `/api/*` routes except `/api/login`, `/api/health`, `/api/ready`

## CORS

- Configured via `FTTH_CORS_ORIGINS` (comma-separated)
- Wildcard `*` is **not** used with credentials

## Reporting

Report security issues to your team lead; do not open public issues with exploit details.

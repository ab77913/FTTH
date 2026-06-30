# Deployment Guide (Local / On-Prem)

## Prerequisites

- Windows 10/11 or Linux
- Python 3.10+
- PostgreSQL 16 (local service or Docker)
- Docker Desktop (Redis + RabbitMQ via `docker compose`)
- Nginx (optional; included in `start_app.bat` for Windows)

## Local deployment

1. Copy `.env.example` to `.env` and fill secrets.
2. Run `start_app.bat` (Windows) or manually:
   ```bash
   docker compose up -d redis rabbitmq
   python api_server.py
   python scripts/celery_dev_worker.py
   ```
3. Verify:
   - `GET http://127.0.0.1:8000/api/health` returns `{"status":"ok"}`
   - `GET http://127.0.0.1:8000/api/ready` returns `"ready": true`

## Production notes

- Set `FTTH_DEV_RELOAD=0` for production API process.
- Use a process manager (systemd, NSSM) instead of background shell starts.
- Set strong `JWT_SECRET_KEY`; never commit `.env`.
- Restrict `FTTH_CORS_ORIGINS` to your frontend origin(s).
- Enable TLS at Nginx or load balancer.

## Database

- Connection: `DATABASE_URL` in `.env`
- Schema migrations: idempotent helpers in `data_ingestion/database/db.py`
- No destructive migrations in this release â€” additive only

## Rollback

1. Stop API and Celery processes.
2. `git checkout <previous-tag>` on deployment host.
3. Restart services.
4. Database rollback only if a forward migration was applied (document per release).


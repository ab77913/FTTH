# Runbook

## Service unavailable (502 / connection refused)

1. Check API: `curl http://127.0.0.1:8000/api/health`
2. Read `logs/api_server.log`
3. Restart: stop port 8000 process, run `python api_server.py`

## Database unavailable

1. `GET /api/ready` — check `"checks.database"`
2. Verify PostgreSQL: `pg_isready -h localhost -p 5432 -U ftth`
3. Confirm `DATABASE_URL` in `.env`

## Redis / Celery jobs not running

1. `docker compose ps` — Redis container up?
2. Read `logs/celery_worker.log`
3. Restart Celery: `python scripts/celery_dev_worker.py`

## Upload / address issues

- CSV+KMZ must be uploaded **together** in one request for merge.
- Check `data_ingestion/extractors/kml_extractor.py` for KMZ point extraction.
- Check merge keys in `data_ingestion/utils/csv_kmz_merge.py`.

## Street View not loading

1. Set Google keys in App Settings or `.env`
2. Enable Maps Embed API + Street View Static API in Google Cloud
3. Map panel loads key from `GET /api/maps-key`

## Logs

| Log | Location |
|-----|----------|
| API | `logs/api_server.log` |
| Celery | `logs/celery_worker.log` |
| Agent 5 | `logs/agent5_streetview.log` |

## Safe restart order

1. Stop Celery worker
2. Stop API (port 8000)
3. Start API, wait for `/api/ready`
4. Start Celery worker

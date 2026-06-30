# FTTH Swagger and API Guide

Last updated: 2026-06-29

This file explains how the FTTH Production Platform exposes its local web app, Swagger/OpenAPI documentation, API workflow, pipeline agents, exports, and troubleshooting links.

## Local Links

Use these links after starting the project with `start_app.bat` from the repository root.

| Purpose | URL |
|---|---|
| Web application | http://localhost |
| FastAPI direct root | http://127.0.0.1:8000/ |
| Swagger UI | http://localhost/docs |
| Swagger UI direct API port | http://127.0.0.1:8000/docs |
| ReDoc documentation | http://localhost/redoc |
| Raw OpenAPI JSON | http://localhost/openapi.json |
| Health check | http://localhost/api/health |
| Readiness check, includes database | http://localhost/api/ready |
| Ollama chat UI, if enabled | http://localhost/api/ollama/ui |
| RabbitMQ management UI | http://localhost:15672 |
| Flower, if started | http://localhost:5555 |

The live OpenAPI document currently reports `FTTH Data Ingestion API`, version `1.0.0`, with 69 paths.

## Start Command

Run this from the project root:

```powershell
.\start_app.bat
```

The startup script now starts Docker PostgreSQL, Redis, and RabbitMQ, then starts the FastAPI server and Nginx.

Important local environment values:

```text
DATABASE_URL=postgresql+psycopg2://ftth:ftth@127.0.0.1:5432/ftth
REDIS_URL=redis://127.0.0.1:6379/0
CELERY_BROKER_URL=redis://127.0.0.1:6379/1
RABBITMQ_URL=amqp://ftth:ftth@127.0.0.1:5672/ftth
FTTH_DEV_RELOAD=0
```

`FTTH_DEV_RELOAD=0` is intentional for this Windows setup. It avoids Uvicorn reload subprocess/named-pipe permission issues.

## Swagger Authentication

Most `/api/*` endpoints require a bearer token.

1. Open http://localhost/docs.
2. Find `POST /api/login` under the `Auth` group.
3. Click `Try it out`.
4. Use the development credentials from the project README.
5. Copy the `token` value from the response.
6. Click `Authorize` at the top-right of Swagger.
7. Enter the token as:

```text
Bearer <token>
```

Swagger has `persistAuthorization` enabled, so it should remember the token while the browser session remains active.

## Swagger UI Settings in Code

The FastAPI app is configured in `backend/api/server.py` with:

| Setting | Value |
|---|---|
| App title | FTTH Data Ingestion API |
| Version | 1.0.0 |
| Swagger path | `/docs` |
| ReDoc path | `/redoc` |
| OpenAPI path | `/openapi.json` |
| Request duration | enabled |
| Filtering | enabled |
| Persist authorization | enabled |
| Try it out by default | enabled |
| Default expansion | none |

##  Swagger Workflow

The project includes a machine-readable workflow endpoint:

```http
GET /api/workflow/guide
```

Use this order in Swagger:

1. Authenticate: `POST /api/login`.
2. Upload data: `POST /api/upload`.
3. Copy the returned `job_id`.
4. Verify records: `GET /api/records?job_id={job_id}`.
5. Inspect agents: `GET /api/agents/catalog`.
6. Inspect options: `GET /api/pipeline/options`.
7. Choose or create a flow: `GET /api/flow-templates` or `POST /api/flow-templates`.
8. Assign flow: `POST /api/jobs/{job_id}/set-flow`.
9. Start processing: `POST /api/jobs/{job_id}/process`.
10. Monitor progress: `GET /api/jobs/{job_id}/agents` or `GET /api/jobs/{job_id}/progress`.
11. Review results: `GET /api/records?job_id={job_id}`.
12. Review map overlay: `GET /api/map/overlay?job_id={job_id}`.
13. Export outputs: `GET /api/export/csv`, `/excel`, `/kml`, or `/kmz` with `job_id`.

Supported upload types are CSV, XLS, XLSX, KML, KMZ, and ZIP. Upload may contain one record, many records, one file, or multiple files.

## Swagger Groups

Swagger groups endpoints with these tags:

| Group | What It Covers |
|---|---|
| `00 - Swagger Workflow` | End-to-end API workflow helpers. |
| `System` | Root app, health, readiness. |
| `Auth` | Login, logout, account creation, password reset. |
| `Settings` | App settings and map key resolution. |
| `Uploads` | File upload and ingestion job creation. |
| `Jobs` | Jobs, categories, source tables, processing, progress, estimates. |
| `Records` | Address records, geodata, columns, stats. |
| `Results - All Data` | Records plus raw, normalized, final, and per-agent result payloads. |
| `Agents` | Agent catalog, tables, result routes, patching, color rules. |
| `Agent 0 - House Discovery` | KML/KMZ polygon household discovery. |
| `Agent 1 - Geocoding` | Reverse/forward geocoding and coordinate matching. |
| `Agent 2 - Address Validation` | Smarty/Melissa address validation. |
| `Agent 3 - Parcel and Land Use` | Parcel and land-use enrichment. |
| `Agent 4 - Building` | Building footprint and structure classification. |
| `Agent 5-0 - Offline OCR` | Local OCR/Ollama/PaddleOCR pass. |
| `Agent 5 - Street View` | Street View imagery and house-number analysis. |
| `Agent 6 - FTTH Final` | Final address, confidence, structure, and FTTH priority. |
| `Agent 7 - Neighborhood Discovery` | Additional polygon/neighborhood address discovery. |
| `Maps` | Street View metadata, overlays, geodata. |
| `Exports` | CSV, Excel, KML, KMZ downloads. |
| `Flows` | Flow templates and job flow assignment. |
| `Project Audit` | Read-only project and job alignment checks. |
| `Ollama Chat` | Optional local Ollama chat/document helper APIs. |

## Important Endpoints

### System

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Serves the frontend app shell. |
| GET | `/api/health` | Basic API liveness. |
| GET | `/api/ready` | Readiness, including database connectivity. |
| GET | `/openapi.json` | Raw OpenAPI schema used by Swagger/ReDoc. |

### Auth

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/login` | Returns JWT bearer token. |
| POST | `/api/logout` | Logout endpoint. |
| POST | `/api/accounts` | Create account. |
| POST | `/api/accounts/forgot-password` | Begin password reset. |
| POST | `/api/accounts/reset-password` | Complete password reset. |

### Uploads and Jobs

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/upload` | Upload CSV/Excel/KML/KMZ/ZIP and create job. |
| GET | `/api/jobs` | List jobs. |
| GET | `/api/jobs/{job_id}` | Get one job. |
| DELETE | `/api/jobs/{job_id}` | Delete job. |
| POST | `/api/jobs/{job_id}/force-reset` | Reset job state. |
| GET | `/api/categories` | Get upload/category metadata. |
| GET | `/api/jobs/{job_id}/source-tables` | Uploaded source file tables. |
| GET | `/api/jobs/{job_id}/source-tables/{table_id}/records` | Raw source records for one table. |

### Records

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/records` | Paginated records with dynamic columns. |
| GET | `/api/records/geo` | Map-ready records with coordinates. |
| GET | `/api/records/{record_id}` | One record. |
| DELETE | `/api/records/{record_id}` | Delete one record. |
| GET | `/api/columns` | Dynamic table columns. |
| GET | `/api/stats` | Dashboard statistics. |

### Agents and Pipeline

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/workflow/guide` | Machine-readable Swagger workflow. |
| GET | `/api/agents/catalog` | All agent stages, inputs, outputs, providers, routes. |
| GET | `/api/agents/{agent_id}` | One agent by id or option key. |
| GET | `/api/pipeline/options` | Current/default pipeline options. |
| GET | `/api/agent2/options` | Agent 2 geocoding options. |
| POST | `/api/jobs/{job_id}/process` | Run enabled agents for the job. |
| GET | `/api/jobs/{job_id}/progress` | Progress details. |
| GET | `/api/jobs/{job_id}/agents` | Per-agent status and counts. |
| GET | `/api/jobs/{job_id}/agent-estimates` | Agent timing estimates. |
| GET | `/api/jobs/{job_id}/agent1-results` | Agent 1 results shortcut. |
| GET | `/api/agent/results/{agent_name}` | Agent result rows by agent name. |
| POST | `/api/agent/results/{agent_name}` | Store agent results. |
| PATCH | `/api/agent/results/{agent_name}/{address_id}` | Patch one agent result. |

### Agent Tables

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/agent/tables` | List agent tables. |
| POST | `/api/agent/tables` | Create/register agent table metadata. |
| GET | `/api/agent/tables/{agent_name}` | Get one agent table. |
| PUT | `/api/agent/tables/{agent_name}/color-rules` | Update table color rules. |
| GET | `/api/agent/records` | Agent-facing records. |
| PATCH | `/api/agent/records` | Patch multiple agent records. |
| PATCH | `/api/agent/records/{record_id}` | Patch one agent record. |

### Maps and Exports

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/maps-key` | Returns map key config for frontend. |
| GET | `/api/maps/streetview` | Street View metadata lookup. |
| GET | `/api/map/overlay` | Map overlay data for one job. |
| GET | `/api/export/csv` | Download CSV output. |
| GET | `/api/export/excel` | Download Excel output. |
| GET | `/api/export/kml` | Download KML output. |
| GET | `/api/export/kmz` | Download KMZ output. |

### Flows

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/flow-templates` | List flow templates. |
| POST | `/api/flow-templates` | Create flow template. |
| PUT | `/api/flow-templates/{template_id}` | Update flow template. |
| POST | `/api/jobs/{job_id}/set-flow` | Assign flow to job. |
| GET | `/api/jobs/{job_id}/flow` | Get job flow. |

### Project Audit

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/audit` | Read-only audit of system, Swagger, agents, providers, maps, exports, and optionally a job. |
| GET | `/api/audit/{section}` | Audit one section. Allowed sections include `system`, `swagger`, `agents`, `providers`, `flows`, `maps`, `exports`, `job`. |

Use this after authorization:

```text
GET /api/audit
GET /api/audit?job_id={job_id}
GET /api/audit/swagger
GET /api/audit/job?job_id={job_id}
```

## Pipeline Agent Catalog

The API exposes all agents through:

```http
GET /api/agents/catalog
```

| Agent ID | Option Key | Main Inputs | Main Outputs |
|---|---|---|---|
| `agent0_house_discovery` | `agent0` | KML/KMZ polygons, coordinates | discovered addresses, coordinates, confidence |
| `agent2_geocoding` | `agent2` | raw address, city/state/ZIP, coordinates | formatted address, lat/lon, match distance, confidence |
| `agent1_address_validator` | `agent1` | raw/geocoded address, city/state/ZIP | validation status, standardized address, provider, confidence |
| `agent3_parcel` | `agent3` | validated address, coordinates | parcel attributes, land use, provider, confidence |
| `agent4_building` | `agent4` | parcel coordinates, address | building footprint, structure type, confidence |
| `agent5_0_offline_ocr` | `agent5_0` | low-confidence records, imagery | OCR text, house number, engine, confidence |
| `agent5_streetview` | `agent5` | coordinates, address, imagery | imagery status, house number, observations, confidence |
| `agent6_final` | `agent6` | upstream agent results | final address, final coordinates, FTTH priority |
| `agent7_neighborhood_discovery` | `agent7` | polygon geometry, existing final addresses | new addresses, dedup status, building enrichment |

Agents are not independent services in Swagger. They run inside a job through:

```http
POST /api/jobs/{job_id}/process
```

Then results are retrieved through:

```http
GET /api/agent/results/{agent_name}?job_id={job_id}
```

## Example Swagger Session

1. `POST /api/login`.
2. Authorize with `Bearer <token>`.
3. `POST /api/upload` with one CSV or a CSV + KMZ group.
4. Copy `job_id` from the response.
5. `GET /api/records?job_id={job_id}&limit=25`.
6. `GET /api/agents/catalog`.
7. `GET /api/flow-templates`.
8. `POST /api/jobs/{job_id}/set-flow` if a flow needs to be assigned.
9. `POST /api/jobs/{job_id}/process`.
10. `GET /api/jobs/{job_id}/agents` until complete.
11. `GET /api/map/overlay?job_id={job_id}`.
12. `GET /api/export/excel?job_id={job_id}`.

## Troubleshooting Swagger and Localhost

| Symptom | Check | Fix |
|---|---|---|
| `http://localhost` loads nothing | Nginx or API not running | Run `start_app.bat`. |
| `localhost` returns 502/connection refused | Nginx cannot reach API on 8000 | Check `http://127.0.0.1:8000/api/health`. |
| API does not start | Database unavailable | Run `docker compose up -d postgres redis rabbitmq`. |
| Login fails | Account DB or credentials issue | Use README development user or create/reset account. |
| Swagger endpoints return 401/403 | Missing bearer token | Run `/api/login`, then click `Authorize`. |
| `/api/ready` fails | Postgres/Redis dependency issue | Check Docker containers and `.env`. |
| Processing does not run | No flow assigned or workers unavailable | Check `/api/jobs/{job_id}/flow`, `/api/jobs/{job_id}/agents`, and service logs. |

Useful commands:

```powershell
docker compose ps postgres redis rabbitmq
Invoke-WebRequest http://localhost/api/health -UseBasicParsing
Invoke-WebRequest http://localhost/api/ready -UseBasicParsing
Get-Content logs\services\api_server.log -Tail 120
Get-Content logs\services\api_server.stderr.log -Tail 120
```

## Important Files

| File | Purpose |
|---|---|
| `api_server.py` | Root entry shim. Imports `backend.api.server`. |
| `backend/api/server.py` | Main FastAPI app, Swagger config, routes, auth, uploads, exports. |
| `backend/data_ingestion/` | Pipeline, extractors, agents, database, utilities. |
| `frontend/` | Frontend source loaded by the root app. |
| `docker-compose.yml` | Local PostgreSQL, Redis, RabbitMQ, optional pgAdmin/Flower/Nginx containers. |
| `start_app.bat` | Local Windows full-stack startup script. |
| `.env` | Local configuration and API keys. Do not commit real secrets. |
| `k8s/` | Kubernetes deployment manifests. |
| `docs/` | Project documentation. |

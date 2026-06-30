"""API response contract tests (shape compatibility)."""

from __future__ import annotations

from fastapi.testclient import TestClient

import api_server


def test_health_contract():
    client = TestClient(api_server.app)
    res = client.get("/api/health")
    assert res.status_code == 200
    body = res.json()
    assert body.get("status") == "ok"
    assert body.get("service") == "ftth-api"


def test_ready_contract():
    client = TestClient(api_server.app)
    res = client.get("/api/ready")
    body = res.json()
    assert "checks" in body
    assert "ready" in body
    assert "database" in body["checks"]


def test_login_error_contract():
    client = TestClient(api_server.app)
    res = client.post("/api/login", json={"username": "ftth_team", "password": "wrong"})
    assert res.status_code == 401
    body = res.json()
    assert "detail" in body
    assert "request_id" in body


def test_records_pagination_params_enforced():
    client = TestClient(api_server.app)
    token_res = client.post(
        "/api/login",
        json={"username": "ftth_team", "password": "Meridian@2026"},
    )
    token = token_res.json()["token"]
    res = client.get(
        "/api/records?page_size=9999",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 422

def test_openapi_document_available_and_grouped():
    client = TestClient(api_server.app)
    res = client.get("/openapi.json")
    assert res.status_code == 200
    schema = res.json()

    assert schema["info"]["title"] == "FTTH Data Ingestion API"
    assert "/api/login" in schema["paths"]
    assert "/api/upload" in schema["paths"]
    assert "/api/records" in schema["paths"]
    assert "/api/export/csv" in schema["paths"]

    tag_names = {tag["name"] for tag in schema.get("tags", [])}
    assert {"Auth", "Uploads", "Jobs", "Records", "Agents", "Maps", "Exports", "Flows"}.issubset(tag_names)

    api_operations = [
        operation
        for path, methods in schema["paths"].items()
        if path.startswith("/api/")
        for operation in methods.values()
        if isinstance(operation, dict)
    ]
    assert api_operations
    assert all(operation.get("tags") for operation in api_operations)


def test_swagger_and_redoc_available():
    client = TestClient(api_server.app)
    docs = client.get("/docs")
    redoc = client.get("/redoc")

    assert docs.status_code == 200
    assert "swagger" in docs.text.lower()
    assert redoc.status_code == 200
    assert "redoc" in redoc.text.lower()


def test_complete_agent_and_workflow_swagger_contract():
    schema = api_server.app.openapi()

    required_paths = {
        "/api/workflow/guide",
        "/api/agents/catalog",
        "/api/agents/{agent_id}",
        "/api/upload",
        "/api/jobs/{job_id}/process",
        "/api/jobs/{job_id}/agents",
        "/api/jobs/{job_id}/agent-estimates",
        "/api/maps/streetview",
        "/api/map/overlay",
        "/api/records",
        "/api/agent/results/{agent_name}",
    }
    assert required_paths.issubset(schema["paths"])

    expected_agent_tags = {
        "Agent 0 - House Discovery",
        "Agent 1 - Geocoding",
        "Agent 2 - Address Validation",
        "Agent 3 - Parcel and Land Use",
        "Agent 4 - Building",
        "Agent 5-0 - Offline OCR",
        "Agent 5 - Street View",
        "Agent 6 - FTTH Final",
        "Agent 7 - Neighborhood Discovery",
    }
    declared_tags = {tag["name"] for tag in schema["tags"]}
    assert {"00 - Swagger Workflow", "Results - All Data", *expected_agent_tags}.issubset(declared_tags)

    process_tags = set(schema["paths"]["/api/jobs/{job_id}/process"]["post"]["tags"])
    assert expected_agent_tags.issubset(process_tags)

    upload = schema["paths"]["/api/upload"]["post"]
    body_ref = upload["requestBody"]["content"]["multipart/form-data"]["schema"]["$ref"]
    body_schema = schema["components"]["schemas"][body_ref.rsplit("/", 1)[-1]]
    files_schema = body_schema["properties"]["file"]
    assert files_schema["type"] == "array"
    assert files_schema["items"].get("contentMediaType") == "application/octet-stream" or files_schema["items"].get("format") == "binary"

    assert schema["paths"]["/api/workflow/guide"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/SwaggerWorkflowGuide")
    assert schema["paths"]["/api/agents/catalog"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/AgentCatalogResponse")
    assert schema["paths"]["/api/maps/streetview"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/StreetViewMetadataResponse")


def test_agent_catalog_contains_every_pipeline_stage():
    catalog = api_server._swagger_agent_catalog()
    assert len(catalog) == 9
    assert {item.id for item in catalog} == {
        "agent0_house_discovery",
        "agent2_geocoding",
        "agent1_address_validator",
        "agent3_parcel",
        "agent4_building",
        "agent5_0_offline_ocr",
        "agent5_streetview",
        "agent6_final",
        "agent7_neighborhood_discovery",
    }
    assert all(item.inputs and item.outputs and item.results_endpoint for item in catalog)


def test_project_audit_openapi_contract():
    schema = api_server.app.openapi()
    assert "/api/audit" in schema["paths"]
    assert "/api/audit/{section}" in schema["paths"]
    assert any(tag["name"] == "Project Audit" for tag in schema["tags"])
    full_schema = schema["paths"]["/api/audit"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert full_schema["$ref"].endswith("/ProjectAuditResponse")


def test_project_audit_agents_section_is_individual_and_complete(monkeypatch):
    monkeypatch.setattr(
        "data_ingestion.health.readiness_payload",
        lambda: {
            "ready": True,
            "status": "ready",
            "checks": {
                "database": "ok",
                "redis": "skipped",
                "rabbitmq": "skipped",
                "storage:data": "ok",
                "storage:uploads": "ok",
                "storage:logs": "ok",
            },
        },
    )
    client = TestClient(api_server.app)
    token = client.post(
        "/api/login",
        json={"username": "ftth_team", "password": "Meridian@2026"},
    ).json()["token"]
    response = client.get(
        "/api/audit/agents",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["aligned"] is True
    assert body["summary"] == {"total": 9, "passed": 9, "warnings": 0, "failed": 0}
    assert [section["name"] for section in body["sections"]] == ["agents"]
    assert len(body["sections"][0]["checks"]) == 9


def test_project_audit_requires_authentication():
    client = TestClient(api_server.app)
    response = client.get("/api/audit")
    assert response.status_code == 401

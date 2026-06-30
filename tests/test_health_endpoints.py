"""Health endpoint smoke tests."""

from fastapi.testclient import TestClient

import api_server


def test_health_liveness():
    client = TestClient(api_server.app)
    res = client.get("/api/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"
    assert res.headers.get("X-Request-ID")
    assert res.headers.get("X-Content-Type-Options") == "nosniff"


def test_ready_includes_checks():
    client = TestClient(api_server.app)
    res = client.get("/api/ready")
    body = res.json()
    assert "checks" in body
    assert "database" in body["checks"]
    assert "storage:data" in body["checks"]
    assert "storage:uploads" in body["checks"]
    assert "storage:logs" in body["checks"]
    assert "rabbitmq" in body["checks"]

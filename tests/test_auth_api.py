"""Authentication and authorization API tests."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import api_server


@pytest.fixture
def client() -> TestClient:
    return TestClient(api_server.app)


def test_health_is_public(client: TestClient) -> None:
    res = client.get("/api/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_ready_is_public(client: TestClient) -> None:
    res = client.get("/api/ready")
    assert res.status_code in {200, 503}
    assert "checks" in res.json()


def test_jobs_requires_auth(client: TestClient) -> None:
    res = client.get("/api/jobs")
    assert res.status_code == 401
    body = res.json()
    assert "detail" in body
    assert body.get("request_id")


def test_upload_requires_auth(client: TestClient) -> None:
    res = client.post("/api/upload")
    assert res.status_code == 401


def test_login_rejects_invalid_credentials(client: TestClient) -> None:
    res = client.post(
        "/api/login",
        json={"username": "ftth_team", "password": "wrong-password-value"},
    )
    assert res.status_code == 401


def test_login_returns_token_for_bootstrap_user(client: TestClient) -> None:
    res = client.post(
        "/api/login",
        json={"username": "ftth_team", "password": "Meridian@2026"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body.get("token")
    assert body.get("username") == "ftth_team"


def test_authenticated_jobs_list(client: TestClient) -> None:
    login = client.post(
        "/api/login",
        json={"username": "ftth_team", "password": "Meridian@2026"},
    )
    token = login.json()["token"]
    mock_session = MagicMock()
    mock_session.execute.return_value.scalars.return_value.all.return_value = []
    with patch("backend.api.server.get_session_factory") as mock_sf:
        mock_sf.return_value.return_value = mock_session
        res = client.get("/api/jobs", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    assert res.json() == []


def test_invalid_token_rejected(client: TestClient) -> None:
    res = client.get(
        "/api/jobs",
        headers={"Authorization": "Bearer not-a-valid-jwt"},
    )
    assert res.status_code == 401

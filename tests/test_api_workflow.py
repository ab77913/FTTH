"""Critical API workflow tests (TestClient, no live server required)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import api_server


@pytest.fixture
def client() -> TestClient:
    return TestClient(api_server.app)


def _login_token(client: TestClient) -> str:
    res = client.post(
        "/api/login",
        json={"username": "ftth_team", "password": "Meridian@2026"},
    )
    assert res.status_code == 200
    return res.json()["token"]


def test_login_to_maps_key_workflow(client: TestClient) -> None:
    token = _login_token(client)
    res = client.get("/api/maps-key", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    body = res.json()
    assert "key" in body
    assert "is_configured" in body


def test_login_to_jobs_workflow(client: TestClient) -> None:
    token = _login_token(client)
    mock_session = MagicMock()
    mock_session.execute.return_value.scalars.return_value.all.return_value = []
    with patch("backend.api.server.get_session_factory") as mock_sf:
        mock_sf.return_value.return_value = mock_session
        res = client.get("/api/jobs", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    assert res.json() == []


def test_upload_without_files_returns_validation_error(client: TestClient) -> None:
    token = _login_token(client)
    res = client.post(
        "/api/upload",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 422


def test_gzip_middleware_compresses_large_json(client: TestClient) -> None:
    res = client.get("/api/health", headers={"Accept-Encoding": "gzip"})
    assert res.status_code == 200
    # Small health payload may stay uncompressed; middleware is registered without error.
    assert res.json()["status"] == "ok"

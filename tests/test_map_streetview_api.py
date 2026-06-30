"""Tests for map Street View metadata API."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import api_server


@pytest.fixture
def client() -> TestClient:
    return TestClient(api_server.app)


@pytest.fixture
def auth_headers(client: TestClient):
    res = client.post("/api/login", json={"username": "ftth_team", "password": "Meridian@2026"})
    assert res.status_code == 200
    token = res.json()["token"]
    return {"Authorization": f"Bearer {token}"}


def test_extract_agent5_heading_from_trace():
    from api.server import _extract_agent5_heading

    data = {"trace": [{"step": "primary", "heading": 127.4}]}
    assert _extract_agent5_heading(data) == pytest.approx(127.4)


def test_streetview_metadata_shape(client, auth_headers):
    res = client.get(
        "/api/maps/streetview",
        params={"lat": 29.6516, "lon": -82.3248},
        headers=auth_headers,
    )
    assert res.status_code == 200
    data = res.json()
    assert "available" in data
    assert "lat" in data
    assert "lon" in data
    assert "heading" in data


def test_streetview_metadata_requires_auth(client):
    res = client.get("/api/maps/streetview", params={"lat": 29.65, "lon": -82.32})
    assert res.status_code == 401

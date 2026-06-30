"""Optional smoke check for a running local API server."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest


def _request_json(url: str, token: str | None = None) -> dict | list:
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=3) as resp:
        return json.loads(resp.read())


def test_running_api_jobs_endpoint_accepts_auth() -> None:
    try:
        login_req = urllib.request.Request(
            "http://localhost:8000/api/login",
            data=json.dumps({"username": "ftth_team", "password": "Meridian@2026"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(login_req, timeout=3) as resp:
            token = json.loads(resp.read())["token"]
        jobs = _request_json("http://localhost:8000/api/jobs", token=token)
    except (urllib.error.URLError, TimeoutError) as exc:
        pytest.skip(f"Local API server is not running: {exc}")

    assert isinstance(jobs, list)

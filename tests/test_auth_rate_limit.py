"""Tests for auth endpoint rate limiting."""

from fastapi.testclient import TestClient

import api_server
from data_ingestion.utils.rate_limit import SlidingWindowRateLimiter


def test_reset_password_rate_limit_returns_429(monkeypatch):
    limiter = SlidingWindowRateLimiter(max_requests=1, window_seconds=60)

    def _fake_check(action: str, client_key: str):
        allowed = limiter.allow(f"{action}:{client_key}")
        return allowed, 0 if allowed else 5

    monkeypatch.setattr(
        "data_ingestion.utils.rate_limit.check_auth_action_rate_limit",
        _fake_check,
    )
    client = TestClient(api_server.app)
    payload = {
        "username": "ftth_team",
        "reset_token": "invalid-token",
        "new_password": "NewPassword1!",
    }
    first = client.post("/api/accounts/reset-password", json=payload)
    second = client.post("/api/accounts/reset-password", json=payload)
    assert first.status_code in {400, 429}
    assert second.status_code == 429

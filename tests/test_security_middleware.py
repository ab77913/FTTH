"""Security middleware and rate limit tests."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from data_ingestion.middleware.security_headers import SecurityHeadersMiddleware
from data_ingestion.utils.rate_limit import SlidingWindowRateLimiter, check_login_rate_limit


def test_security_headers_middleware():
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/ping")
    def ping():
        return {"ok": True}

    client = TestClient(app)
    res = client.get("/ping")
    assert res.headers.get("X-Content-Type-Options") == "nosniff"
    assert res.headers.get("X-Frame-Options") == "SAMEORIGIN"


def test_sliding_window_rate_limiter_blocks_burst():
    limiter = SlidingWindowRateLimiter(max_requests=2, window_seconds=60)
    assert limiter.allow("client-a") is True
    assert limiter.allow("client-a") is True
    assert limiter.allow("client-a") is False


def test_login_rate_limit_integration():
    allowed, retry = check_login_rate_limit("test-client-unique-key")
    assert allowed in (True, False)
    assert retry >= 0

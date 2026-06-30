"""Structured access logging with request duration."""

from __future__ import annotations

import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger("ftth.access")


class AccessLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        start = time.perf_counter()
        status_code = 500
        response = await call_next(request)
        status_code = response.status_code
        if not request.url.path.startswith(("/static/", "/assets/", "/images/")):
            duration_ms = (time.perf_counter() - start) * 1000
            request_id = getattr(request.state, "request_id", None)
            logger.info(
                "request completed",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": status_code,
                    "duration_ms": round(duration_ms, 2),
                },
            )
        return response

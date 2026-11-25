"""Request ID propagation and structured access logging."""

import time
import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

REQUEST_ID_HEADER = "x-request-id"


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[override]
        request_id = request.headers.get(REQUEST_ID_HEADER) or str(uuid.uuid4())
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            http_path=request.url.path,
            http_method=request.method,
        )
        start = time.perf_counter()
        log = structlog.get_logger("http")
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = int((time.perf_counter() - start) * 1000)
            log.info("http.request", status=500, duration_ms=duration_ms)
            structlog.contextvars.clear_contextvars()
            raise
        duration_ms = int((time.perf_counter() - start) * 1000)
        log.info("http.request", status=response.status_code, duration_ms=duration_ms)
        structlog.contextvars.clear_contextvars()
        response.headers[REQUEST_ID_HEADER] = request_id
        return response

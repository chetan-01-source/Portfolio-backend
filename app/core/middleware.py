import time
import uuid
from datetime import datetime, timezone

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from app.core.logging import logger, request_id_ctx
from app.core.mongo import get_db


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "-"


class RequestTrackingMiddleware(BaseHTTPMiddleware):
    """Generates request_id, times the request, persists a row to request_logs.

    Only logs /api/* paths to keep the collection focused.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        token = request_id_ctx.set(request_id)
        request.state.request_id = request_id
        request.state.client_ip = _client_ip(request)
        request.state.user_agent = request.headers.get("user-agent", "-")

        started = time.perf_counter()
        status = 500
        error: str | None = None
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["x-request-id"] = request_id
            return response
        except Exception as e:
            error = repr(e)
            raise
        finally:
            latency_ms = int((time.perf_counter() - started) * 1000)
            if request.url.path.startswith("/api/"):
                try:
                    db = get_db()
                    await db.request_logs.insert_one(
                        {
                            "request_id": request_id,
                            "route": request.url.path,
                            "method": request.method,
                            "status": status,
                            "latency_ms": latency_ms,
                            "ip": request.state.client_ip,
                            "user_agent": request.state.user_agent,
                            "error": error,
                            "created_at": datetime.now(timezone.utc),
                        }
                    )
                except Exception as log_err:
                    logger.warning(f"request_logs insert failed: {log_err}")
            request_id_ctx.reset(token)

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.config import get_settings
from app.core.logging import configure_logging, logger
from app.core.middleware import RequestTrackingMiddleware
from app.core.mongo import close_client, ensure_indexes
from app.core.qdrant import close_qdrant, ensure_collection
from app.core.redis import close_redis
from app.modules.chat.controller import router as chat_router
from app.modules.eval.controller import router as eval_router
from app.modules.health.controller import router as health_router
from app.modules.hire.controller import router as hire_router
from app.modules.ingest.controller import router as ingest_router

limiter = Limiter(key_func=get_remote_address)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    settings = get_settings()
    logger.info(f"Starting CHET.ai backend env={settings.app_env}")
    await ensure_indexes(settings)
    try:
        await ensure_collection()
    except Exception as e:
        logger.warning(f"Qdrant collection bootstrap skipped: {e}")
    yield
    await close_client()
    await close_redis()
    await close_qdrant()
    logger.info("Shutdown complete")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="CHET.ai", version="0.1.0", lifespan=lifespan)

    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    is_wildcard = settings.cors_origin_list == ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=not is_wildcard,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["x-request-id"],
    )
    app.add_middleware(RequestTrackingMiddleware)

    app.include_router(health_router, prefix="/api")
    app.include_router(chat_router, prefix="/api")
    app.include_router(hire_router, prefix="/api")
    app.include_router(eval_router, prefix="/api")
    app.include_router(ingest_router, prefix="/api")

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        logger.exception(f"Unhandled error on {request.url.path}: {exc}")
        return JSONResponse(
            status_code=500,
            content={"error": "internal_error", "request_id": getattr(request.state, "request_id", None)},
        )

    return app


app = create_app()

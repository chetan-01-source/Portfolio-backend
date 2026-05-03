import sys
from contextvars import ContextVar

from loguru import logger

from app.config import get_settings

request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")


def configure_logging() -> None:
    settings = get_settings()
    logger.remove()
    logger.add(
        sys.stdout,
        level=settings.log_level,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> "
            "<level>{level: <8}</level> "
            "<cyan>{extra[request_id]}</cyan> "
            "<cyan>{name}:{function}:{line}</cyan> - <level>{message}</level>"
        ),
        backtrace=False,
        diagnose=False,
    )

    def _patcher(record):
        record["extra"].setdefault("request_id", request_id_ctx.get())

    logger.configure(patcher=_patcher)


__all__ = ["configure_logging", "logger", "request_id_ctx"]

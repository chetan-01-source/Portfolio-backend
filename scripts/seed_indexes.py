"""Idempotent index bootstrap. Useful when not running through the FastAPI lifespan.

Run: python scripts/seed_indexes.py
"""

import asyncio

from app.core.logging import configure_logging, logger
from app.core.mongo import close_client, ensure_indexes
from app.core.qdrant import close_qdrant, ensure_collection


async def main() -> None:
    configure_logging()
    await ensure_indexes()
    try:
        await ensure_collection()
    except Exception as e:
        logger.warning(f"Qdrant skipped: {e}")
    await close_client()
    await close_qdrant()


if __name__ == "__main__":
    asyncio.run(main())

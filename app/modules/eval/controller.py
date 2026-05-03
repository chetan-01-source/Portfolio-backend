"""Admin-ish endpoints for inspecting eval state. Not exposed publicly in prod."""

from fastapi import APIRouter

from app.deps import DbDep
from app.modules.eval.repository import EvalRepository

router = APIRouter(prefix="/eval", tags=["eval"])


@router.get("/means")
async def recent_means(db: DbDep, limit: int = 100):
    repo = EvalRepository(db)
    return {"means": await repo.recent_means(limit), "limit": limit}

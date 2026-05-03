from fastapi import APIRouter

from app.deps import DbDep, QdrantDep, RedisDep, SettingsDep
from app.modules.health.service import HealthService

router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
async def health(
    db: DbDep,
    redis: RedisDep,
    qdrant: QdrantDep,
    settings: SettingsDep,
):
    service = HealthService(db, redis, qdrant, settings)
    return await service.status()

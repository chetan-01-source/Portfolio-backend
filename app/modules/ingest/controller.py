from fastapi import APIRouter, File, Header, HTTPException, UploadFile

from app.deps import QdrantDep, RedisDep, SettingsDep
from app.modules.ingest.schemas import EmbeddedSourcesResponse, IngestResponse, UrlIngestRequest
from app.modules.ingest.service import IngestService

router = APIRouter(prefix="/ingest", tags=["ingest"])
RESUME_FILE = File(...)
INGEST_API_KEY_HEADER = Header(default=None)


def _authorize(settings, ingest_api_key: str | None) -> None:
    if settings.ingest_api_key and ingest_api_key != settings.ingest_api_key:
        raise HTTPException(status_code=401, detail="invalid ingest api key")


@router.post("/resume", response_model=IngestResponse)
async def ingest_resume(
    qdrant: QdrantDep,
    redis: RedisDep,
    settings: SettingsDep,
    file: UploadFile = RESUME_FILE,
    x_ingest_api_key: str | None = INGEST_API_KEY_HEADER,
) -> IngestResponse:
    _authorize(settings, x_ingest_api_key)
    if file.content_type not in {"application/pdf", "application/octet-stream"}:
        raise HTTPException(status_code=400, detail="resume must be a PDF file")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="resume file is empty")
    service = IngestService(qdrant=qdrant, redis=redis, settings=settings)
    return await service.ingest_resume(filename=file.filename or "resume.pdf", pdf_bytes=data)


@router.get("/sources", response_model=EmbeddedSourcesResponse)
async def embedded_sources(
    qdrant: QdrantDep,
    redis: RedisDep,
    settings: SettingsDep,
    max_points: int = 1000,
    x_ingest_api_key: str | None = INGEST_API_KEY_HEADER,
) -> EmbeddedSourcesResponse:
    _authorize(settings, x_ingest_api_key)
    service = IngestService(qdrant=qdrant, redis=redis, settings=settings)
    return await service.list_sources(max_points=max_points)


@router.post("/urls", response_model=IngestResponse)
async def ingest_urls(
    body: UrlIngestRequest,
    qdrant: QdrantDep,
    redis: RedisDep,
    settings: SettingsDep,
    x_ingest_api_key: str | None = INGEST_API_KEY_HEADER,
) -> IngestResponse:
    _authorize(settings, x_ingest_api_key)
    service = IngestService(qdrant=qdrant, redis=redis, settings=settings)
    return await service.ingest_urls(
        urls=[str(url) for url in body.urls],
        max_pages=body.max_pages or settings.ingest_default_max_pages,
        max_depth=body.max_depth if body.max_depth is not None else settings.ingest_default_max_depth,
        same_domain_only=body.same_domain_only,
        source_label=body.source_label,
    )

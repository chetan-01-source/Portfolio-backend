from pydantic import BaseModel, Field, HttpUrl


class UrlIngestRequest(BaseModel):
    urls: list[HttpUrl] = Field(min_length=1, max_length=20)
    max_pages: int | None = Field(default=None, ge=1, le=50)
    max_depth: int | None = Field(default=None, ge=0, le=3)
    same_domain_only: bool = True
    source_label: str = "api:url"


class IngestedSource(BaseModel):
    source: str
    chunks: int


class IngestResponse(BaseModel):
    ok: bool
    collection: str
    embedded_chunks: int
    points_upserted: int
    sources: list[IngestedSource]
    skipped: list[str] = Field(default_factory=list)


class EmbeddedSourceSummary(BaseModel):
    source: str
    kind: str | None = None
    points: int


class EmbeddedSourcesResponse(BaseModel):
    ok: bool
    collection: str
    scanned_points: int
    sources: list[EmbeddedSourceSummary]

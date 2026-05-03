from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: str = "dev"
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_app_url: str = "https://chetanmarathe.dev"
    openrouter_app_name: str = "CHET.ai"
    llm_cheap_model: str = "google/gemini-2.5-flash"
    llm_strong_model: str = "anthropic/claude-haiku-4.5"
    embed_model: str = "openai/text-embedding-3-small"
    embed_dim: int = 1536

    mongodb_uri: str = "mongodb://dev:dev@localhost:27017/?authSource=admin"
    mongodb_db: str = "chet_ai"

    redis_url: str = "redis://localhost:6379/0"
    semantic_cache_threshold: float = 0.93
    semantic_cache_ttl_seconds: int = 86400
    exact_cache_ttl_seconds: int = 604800

    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str = ""
    qdrant_collection: str = "chet_kb"

    reranker_url: str = ""
    reranker_top_n: int = 8

    ingest_api_key: str = ""
    ingest_default_max_pages: int = 10
    ingest_default_max_depth: int = 1
    ingest_fetch_timeout_seconds: float = 15.0
    ingest_max_url_bytes: int = 2_000_000

    mail_username: str = ""
    mail_password: str = ""
    mail_from: str = ""
    mail_port: int = 587
    mail_server: str = "smtp.gmail.com"
    mail_from_name: str = "CHET.ai"
    mail_starttls: bool = True
    mail_ssl_tls: bool = False
    use_credentials: bool = True
    validate_certs: bool = True

    chetan_email: str = "chetanmarathe0412@gmail.com"
    chetan_phone: str = ""
    include_phone_in_email: bool = False
    chetan_resume_url: str = "https://chetanmarathe.dev/resume.pdf"
    chetan_resume_attachment_path: str = "data/resume.pdf"
    chetan_portfolio_url: str = "https://chetanmarathe.dev"
    chetan_linkedin_url: str = "https://linkedin.com/in/chetanmarathe"
    chetan_github_url: str = "https://github.com/chetanmarathe"
    chetan_leetcode_url: str = "https://leetcode.com/chetanmarathe"

    eval_sample_rate: float = 0.01
    notify_chetan_on_lead: bool = True

    hire_session_ttl_seconds: int = 86400
    request_log_ttl_seconds: int = Field(default=60 * 60 * 24 * 30)

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

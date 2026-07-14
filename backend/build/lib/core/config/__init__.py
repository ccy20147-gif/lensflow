"""
ToonFlow Backend — Core Config
"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # App
    app_name: str = "ToonFlow API"
    app_version: str = "0.1.0"
    debug: bool = False

    # Database
    database_url: str = "postgresql+asyncpg://toonflow:toonflow@localhost:5432/toonflow"
    database_url_sync: str = "postgresql+psycopg2://toonflow:toonflow@localhost:5432/toonflow"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # JWT
    jwt_public_key: str = ""
    jwt_private_key: str = ""
    jwt_algorithm: str = "RS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7

    # Security
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]
    rate_limit_per_minute: int = 120

    # Blob
    blob_storage_path: str = "/data/blobs"
    blob_signed_url_ttl_seconds: int = 300

    # Bootstrap
    bootstrap_completed: bool = False
    bootstrap_owner_email: str = ""

    # Observability
    sentry_dsn: str = ""
    otlp_endpoint: str = ""
    log_level: str = "INFO"


settings = Settings()

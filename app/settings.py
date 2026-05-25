"""Application-wide settings, override via environment variables (RUNWAY_*)."""
from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RUNWAY_", env_file=".env", extra="ignore")

    api_base: str = "https://api.runwayml.com"
    user_agent: str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    request_timeout_seconds: float = 30.0

    # Polling
    poll_interval_seconds: float = 5.0
    poll_max_seconds: float = 60 * 60 * 2  # 2h ceiling per task
    download_timeout_seconds: float = 60 * 10

    # Concurrency
    max_concurrent_jobs: int = 2

    # Logging / retention — keep api_log lean (it bloats fast from polling)
    api_log_retention_days: int = 3


settings = Settings()

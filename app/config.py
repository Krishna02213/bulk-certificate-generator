from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CERTIFLOW_", env_file=".env")

    app_name: str = "CertiFlow"
    database_url: str = "sqlite:///./data/certiflow.db"
    storage_dir: Path = Path("./data/certificates")
    max_recipients: int = Field(default=5000, ge=1, le=100_000)
    worker_threads: int = Field(default=4, ge=1, le=32)
    public_base_url: str = "http://localhost:8000"


@lru_cache
def get_settings() -> Settings:
    return Settings()


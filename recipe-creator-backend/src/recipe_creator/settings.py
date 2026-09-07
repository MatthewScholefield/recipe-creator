"""Environment settings. All variables use the RECIPE_ prefix."""
from pathlib import Path
from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RECIPE_", env_file=".env", extra="ignore")

    db_url: str = "http://127.0.0.1:8000"
    db_namespace: str = Field(default="recipe_creator", pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    db_database: str = Field(default="recipe_creator", pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    db_user: str = "root"
    db_password: SecretStr = SecretStr("")
    db_path: Path = Path.home() / ".local/share/recipe-creator/db"
    media_root: Path = Path.home() / ".local/share/recipe-creator/media"
    frontend_dist: Path = Path(__file__).resolve().parents[3] / "recipe-creator-frontend/dist"
    public_origin: str = "http://localhost:5173"
    allowed_origins: list[str] = ["http://localhost:5173"]
    secure_cookies: bool = True
    admin_password_hash: SecretStr = SecretStr("")
    session_secret: SecretStr = SecretStr("")
    device_ttl_seconds: int = Field(default=365 * 86400, gt=0)
    admin_ttl_seconds: int = Field(default=8 * 3600, gt=0)
    pairing_ttl_seconds: int = Field(default=300, gt=0)
    ai_base_url: str = "https://crof.ai/v1"
    ai_api_key: SecretStr = SecretStr("")
    ai_model: str = "openai:deepseek-v4-flash-0731"
    ai_timeout_seconds: float = Field(default=45, gt=0)
    ai_concurrency: int = Field(default=1, ge=1, le=8)
    ai_daily_limit: int = Field(default=50, ge=0)
    ai_global_daily_limit: int = Field(default=200, ge=0)
    upload_daily_limit: int = Field(default=5, ge=0)
    upload_ip_daily_limit: int = Field(default=60, ge=0)
    upload_global_daily_limit: int = Field(default=200, ge=0)
    upload_pending_limit: int = Field(default=3, ge=0)
    upload_max_bytes: int = Field(default=512 * 1024, gt=0, le=512 * 1024)
    ai_ip_daily_limit: int = Field(default=50, ge=0)
    image_max_pixels: int = Field(default=24_000_000, gt=0)
    image_concurrency: int = Field(default=1, ge=1, le=4)
    storage_max_bytes: int = Field(default=5 * 1024**3, gt=0)
    pairing_attempt_limit: int = Field(default=10, gt=0)
    login_attempt_limit: int = Field(default=5, gt=0)
    job_lease_seconds: int = Field(default=120, gt=0)
    job_max_attempts: int = Field(default=4, gt=0)
    job_poll_seconds: float = Field(default=2, gt=0)
    migrations_dir: Path = (
        Path(__file__).resolve().parent / "migrations"
        if (Path(__file__).resolve().parent / "migrations").is_dir()
        else Path(__file__).resolve().parents[2] / "migrations"
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()

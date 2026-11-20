"""Application settings, loaded from environment variables (prefix ORBIT_) and the root `.env` file."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/core/config.py -> project root is three levels above app/
ROOT_DIR = Path(__file__).resolve().parents[3]

DEV_JWT_SECRET = "dev-only-insecure-secret-change-me"
PLACEHOLDER = "CHANGE_ME"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(ROOT_DIR / ".env",), env_prefix="ORBIT_", extra="ignore")

    env: str = "development"
    app_name: str = "ORBIT AI"
    api_prefix: str = "/api/v1"
    log_level: str = "INFO"

    # security
    jwt_secret: str = DEV_JWT_SECRET  # refused outside development/test
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 480
    demo_password: str = "orbit-demo-2026"
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    # metadata database (PostgreSQL in Docker; SQLite is only used by the tests)
    database_url: str = "sqlite:///./orbit.db"

    # background jobs: Redis broker for the Celery worker. tasks_eager=true runs jobs inside the API process
    # (used by the tests, or to run without a worker).
    redis_url: str = "redis://localhost:6380/0"
    tasks_eager: bool = False
    max_run_attempts: int = 3

    # object storage: s3 = MinIO (default), local = folder on disk (tests / no Docker)
    storage_backend: str = "s3"
    s3_endpoint: str = "http://localhost:9000"
    s3_bucket: str = "orbit-lakehouse"
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_region: str = "us-east-1"
    data_dir: Path = Path("data/runtime")

    # pipeline automation and destinations
    smtp_host: str = ""  # empty = e-mail reports and notifications are disabled
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "orbit@localhost"
    smtp_tls: bool = True
    export_dir: str = ""  # optional shared folder that also receives a copy of every exported file
    python_transform_enabled: bool = True
    python_timeout_s: int = 60
    python_memory_mb: int = 2048
    public_url: str = "http://localhost:3000"  # links in e-mails and notifications

    # limits
    upload_max_mb: int = 100
    preview_rows: int = 50
    pipeline_preview_rows: int = 1000  # rows read from each input when previewing a pipeline step

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def resolved_data_dir(self) -> Path:
        p = Path(self.data_dir)
        if not p.is_absolute():
            p = (ROOT_DIR / p).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()


def check_secrets(s: Settings | None = None) -> list[str]:
    """Refuse unsafe secrets at startup. Returns warnings; raises RuntimeError on a blocking problem."""
    s = s or get_settings()
    problems: list[str] = []
    for name in ("jwt_secret", "demo_password"):
        if PLACEHOLDER in str(getattr(s, name) or ""):
            problems.append(f"ORBIT_{name.upper()} still contains the {PLACEHOLDER} placeholder from .env.example")
    weak: list[str] = []
    if s.jwt_secret == DEV_JWT_SECRET:
        weak.append("ORBIT_JWT_SECRET is the built-in development secret")
    elif len(s.jwt_secret) < 32:
        weak.append("ORBIT_JWT_SECRET is shorter than 32 characters")
    production = s.env not in ("development", "test")
    if production:
        problems.extend(weak)
    if problems:
        raise RuntimeError("Unsafe configuration — fix .env and restart: " + "; ".join(problems))
    return weak

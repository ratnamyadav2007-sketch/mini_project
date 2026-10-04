from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Backend API"
    database_url: str = "sqlite:///./var/backend.sqlite3"
    auth_session_ttl_seconds: int = Field(default=604800, gt=0)
    auth_csrf_challenge_ttl_seconds: int = Field(default=600, gt=0)
    auth_login_max_failures: int = Field(default=5, gt=0)
    auth_login_lockout_seconds: int = Field(default=900, gt=0)
    auth_cookie_secure: bool = False
    master_encryption_key: SecretStr | None = None
    master_encryption_key_version: int = Field(default=1, gt=0)
    sos_signing_key: SecretStr | None = None
    sos_link_ttl_seconds: int = Field(default=3600, gt=0, le=86400)
    sos_public_rate_limit_per_minute: int = Field(default=30, gt=0)
    notification_log_path: str = "var/notifications.log"
    public_base_url: str = "http://127.0.0.1:8000"
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str | None = None
    demo_password: SecretStr | None = None
    chat_model_url: str | None = None
    chat_model_api_key: SecretStr | None = None
    attachment_storage_path: str = "var/attachments"
    attachment_max_size_bytes: int = Field(default=10 * 1024 * 1024, gt=0)
    old_master_encryption_key: SecretStr | None = None
    new_master_encryption_key: SecretStr | None = None
    new_master_encryption_key_version: int | None = Field(default=None, gt=0)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()

"""Application config via pydantic-settings.

Phase-01 stub: minimal fields needed for `init` to connect to Postgres.
Phase-02 will add nested HaravanSettings + TelegramSettings + rate-limit fields.
"""

from __future__ import annotations

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Top-level app settings.

    Reads from environment + .env file in CWD. Secrets wrapped in SecretStr to keep
    them out of repr/log output.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    database_url: SecretStr
    log_level: str = "INFO"
    log_format: str = "console"
    timezone: str = "Asia/Ho_Chi_Minh"


def load_settings() -> Settings:
    """Factory used by CLI / pipeline; isolates pydantic call for easier mocking."""
    return Settings()  # type: ignore[call-arg]  # fields populated via env

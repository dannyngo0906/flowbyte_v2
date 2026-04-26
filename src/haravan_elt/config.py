"""Application config via pydantic-settings.

Three nested groups: HaravanSettings, DatabaseSettings, TelegramSettings.
All secrets wrapped in SecretStr to keep them out of repr/log output.
"""

from __future__ import annotations

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class HaravanSettings(BaseSettings):
    """Haravan API credentials + rate-limit knobs."""

    model_config = SettingsConfigDict(
        env_prefix="HARAVAN_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    shop_domain: str
    access_token: SecretStr
    refresh_token: SecretStr
    client_id: str
    client_secret: SecretStr
    rate_limit_per_sec: int = 4
    rate_limit_burst: int = 80


class DatabaseSettings(BaseSettings):
    """Postgres DSN. Wrapped in SecretStr; full DSN can leak password if logged."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: SecretStr


class TelegramSettings(BaseSettings):
    """Telegram bot credentials. Optional: empty values disable notifications."""

    model_config = SettingsConfigDict(
        env_prefix="TELEGRAM_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bot_token: SecretStr = SecretStr("")
    chat_id: str = ""


# pydantic-settings populates required fields from the environment, but mypy
# can't model that → wrap construction in factories with a single targeted ignore.
def _haravan_factory() -> HaravanSettings:
    return HaravanSettings()  # type: ignore[call-arg]


def _database_factory() -> DatabaseSettings:
    return DatabaseSettings()  # type: ignore[call-arg]


def _telegram_factory() -> TelegramSettings:
    return TelegramSettings()


class Settings(BaseSettings):
    """Top-level settings. Sub-models are populated from env on instantiation."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    haravan: HaravanSettings = Field(default_factory=_haravan_factory)
    database: DatabaseSettings = Field(default_factory=_database_factory)
    telegram: TelegramSettings = Field(default_factory=_telegram_factory)
    log_level: str = "INFO"
    log_format: str = "console"
    extract_batch_size: int = 250
    load_batch_size: int = 500
    timezone: str = "Asia/Ho_Chi_Minh"


def load_settings() -> Settings:
    """Factory used by CLI / pipeline; isolates pydantic call for easier mocking."""
    return Settings()

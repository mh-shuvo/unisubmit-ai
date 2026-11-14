"""Settings for the web app, the worker and the CLI, read from the environment.

Every variable carries the `UNISUBMIT_` prefix, e.g. `UNISUBMIT_SECRET_KEY`.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import PostgresDsn, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_PREFIX = "UNISUBMIT_"
MIN_SECRET_LENGTH = 32
# Values that have shipped in this repository's examples. A secret anyone can
# read is no secret (DEF-027).
PUBLISHED_PLACEHOLDERS = {
    "change-this-secret-in-production",
    "replace-with-a-long-random-secret",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix=ENV_PREFIX, env_file=".env", extra="ignore")

    # Required, no default: signs session cookies (DEF-027).
    secret_key: SecretStr
    # Required, no default: shared with the AI service (DEF-029).
    ai_service_token: SecretStr
    database_url: PostgresDsn
    # Uploads and reports. Relative paths resolve against the working directory.
    storage_dir: Path = Path("storage")
    ai_service_url: str = "http://127.0.0.1:8001"
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3:32b"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: SecretStr = SecretStr("")
    smtp_from: str = "unisubmit@example.edu"
    smtp_use_tls: bool = True

    @field_validator("secret_key", "ai_service_token")
    @classmethod
    def _random_and_long(cls, value: SecretStr) -> SecretStr:
        secret = value.get_secret_value()
        if secret in PUBLISHED_PLACEHOLDERS or len(secret) < MIN_SECRET_LENGTH:
            raise ValueError(
                f"must be a random value of at least {MIN_SECRET_LENGTH} characters; "
                "generate one with: openssl rand -hex 32"
            )
        return value

    @field_validator("database_url")
    @classmethod
    def _psycopg_driver(cls, value: PostgresDsn) -> PostgresDsn:
        """`postgresql://` would select psycopg2, which is not installed."""
        if value.scheme in {"postgres", "postgresql"}:
            return PostgresDsn(str(value).replace(f"{value.scheme}://", "postgresql+psycopg://", 1))
        return value


@lru_cache
def get_settings() -> Settings:
    """The process-wide settings; exits with every problem listed if any is invalid."""
    try:
        return Settings()  # required values come from the environment
    except ValidationError as exc:
        problems = "\n".join(
            f"  {ENV_PREFIX}{'_'.join(map(str, error['loc'])).upper()}: {error['msg']}"
            for error in exc.errors()
        )
        raise SystemExit(
            f"UniSubmit cannot start; set these in .env or the environment:\n{problems}"
        ) from None

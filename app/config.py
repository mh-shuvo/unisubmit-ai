from pydantic import ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Values that have shipped in this repository's examples. A secret anyone can
# read is no secret (DEF-027).
PUBLISHED_PLACEHOLDERS = {
    'change-this-secret-in-production',
    'replace-with-a-long-random-secret',
}


class Settings(BaseSettings):
    # Required, no default: signs session cookies (DEF-027).
    app_secret: str
    # Required, no default: shared with the AI service (DEF-029).
    ai_service_token: str
    database_url: str = 'sqlite:///./unisubmit.db'
    ai_service_url: str = 'http://127.0.0.1:8001'
    ollama_url: str = 'http://127.0.0.1:11434'
    ollama_model: str = 'qwen3:32b'
    smtp_host: str = ''
    smtp_port: int = 587
    smtp_username: str = ''
    smtp_password: str = ''
    smtp_from: str = 'unisubmit@example.edu'
    smtp_use_tls: bool = True
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')

    @field_validator('app_secret', 'ai_service_token')
    @classmethod
    def _random_and_long(cls, value: str, info: ValidationInfo) -> str:
        if value in PUBLISHED_PLACEHOLDERS or len(value) < 32:
            raise ValueError(
                f'{info.field_name.upper()} must be a random value of at least 32 '
                'characters; generate one with: openssl rand -hex 32'
            )
        return value


settings = Settings()

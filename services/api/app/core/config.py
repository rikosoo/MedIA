from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://financemed:financemed@localhost:5432/financemed"
    session_cookie_name: str = "fm_session"
    session_ttl_hours: int = 12
    cookie_secure: bool = False  # True em staging/prod (HTTPS)
    cors_origins: str = "http://localhost:3000"
    environment: str = "dev"
    billing_webhook_secret: str = ""  # vazio = webhook desligado (503)


@lru_cache
def get_settings() -> Settings:
    return Settings()

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
    # Chave que cifra segredos em repouso (ex.: TOTP). Em staging/prod é obrigatória e diferente do padrão.
    secret_key: str = "dev-only-insecure-secret-key-change-me"
    login_max_failures: int = 5
    login_lock_minutes: int = 15
    rate_limit_per_minute: int = 60  # chamadas de autenticação por IP
    trust_proxy_headers: bool = (
        False  # true só atrás de proxy confiável que sobrescreve X-Forwarded-For
    )
    max_body_bytes: int = (
        3_000_000  # corpo máximo aceito (o import de CSV tem teto próprio de 2 MB de texto)
    )
    billing_webhook_secret: str = ""  # vazio = webhook desligado (503)


DEV_SECRET = Settings.model_fields["secret_key"].default


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    if s.environment != "dev" and (s.secret_key == DEV_SECRET or not s.cookie_secure):
        raise RuntimeError("Fora de dev: defina SECRET_KEY próprio e COOKIE_SECURE=true")
    if "*" in s.cors_origins:
        raise RuntimeError("CORS_ORIGINS não pode conter '*' (as requisições usam cookies)")
    return s

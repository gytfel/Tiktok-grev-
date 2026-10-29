from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://tiktok:tiktok@localhost:5432/tiktok"
    redis_url: str = "redis://localhost:6379/0"

    # Подпись cookie сессии панели.
    secret_key: str
    # Ключ Fernet для шифрования токенов TikTok в базе.
    token_encryption_key: str
    # Адрес, по которому панель доступна из браузера. Из него строится redirect URI для OAuth.
    public_base_url: str = "http://localhost:8000"
    session_https_only: bool = False

    media_dir: str = "./data/media"
    max_upload_bytes: int = 4 * 1024**3
    frontend_dist: str = ""

    tiktok_client_key: str = ""
    tiktok_client_secret: str = ""
    tiktok_auth_url: str = "https://www.tiktok.com/v2/auth/authorize/"
    tiktok_api_base: str = "https://open.tiktokapis.com"
    tiktok_scopes: str = "user.info.basic,video.publish"
    # Включается только вместе с эмулятором TikTok из docker-compose (профиль dev).
    tiktok_emulator: bool = False

    upload_chunk_size: int = 10 * 1024 * 1024
    dispatch_batch_size: int = 50
    max_auto_attempts: int = 4
    status_poll_interval_s: int = 10
    # Сколько ждать завершения обработки в TikTok, прежде чем отправить задание на ручную проверку.
    processing_timeout_s: int = 2 * 3600
    # Сколько подтверждённо незавершённая загрузка должна оставаться такой, чтобы считать её неудачной.
    incomplete_upload_grace_s: int = 60
    job_lease_s: int = 600

    admin_username: str = ""
    admin_password: str = ""

    # Контакт для push-сервисов (mailto: или https:). По умолчанию — PUBLIC_BASE_URL, если он на HTTPS.
    vapid_subject: str = ""
    notify_interval_s: int = 20

    @property
    def oauth_redirect_uri(self) -> str:
        return self.public_base_url.rstrip("/") + "/api/oauth/tiktok/callback"

    @property
    def tiktok_configured(self) -> bool:
        return bool(self.tiktok_client_key and self.tiktok_client_secret)


@lru_cache
def get_settings() -> Settings:
    return Settings()

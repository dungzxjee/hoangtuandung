from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(case_sensitive=True)

    DB_HOST: str = "db"
    DB_PORT: int = 3306
    DB_NAME: str = "crm"
    DB_USER: str = "crm_app"
    DB_PASSWORD: str
    JWT_SECRET: str = Field(min_length=32)
    JWT_EXPIRE_MINUTES: int = Field(default=60, gt=0, le=1440)
    ADMIN_EMAIL: str
    ADMIN_PASSWORD: str = Field(min_length=12, max_length=128)
    CORS_ORIGINS: str = ""
    SMTP_HOST: str | None = None
    SMTP_PORT: int = Field(default=587, gt=0, le=65535)
    SMTP_USERNAME: str | None = None
    SMTP_PASSWORD: str | None = None
    SMTP_FROM_EMAIL: str | None = None
    SMTP_STARTTLS: bool = True
    SMTP_USE_SSL: bool = False
    PASSWORD_RESET_TTL_MINUTES: int = Field(default=20, ge=15, le=30)
    PUBLIC_APP_URL: str = "http://127.0.0.1:8080"

    @property
    def database_url(self) -> URL:
        return URL.create(
            "mysql+pymysql",
            username=self.DB_USER,
            password=self.DB_PASSWORD,
            host=self.DB_HOST,
            port=self.DB_PORT,
            database=self.DB_NAME,
            query={"charset": "utf8mb4"},
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()

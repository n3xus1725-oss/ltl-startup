"""Application settings and configuration management."""

from functools import lru_cache
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Environment
    ENVIRONMENT: str = Field(default="development", description="Runtime environment")
    LOG_LEVEL: str = Field(default="INFO", description="Logging level")

    # API
    API_HOST: str = Field(default="0.0.0.0", description="API host")
    API_PORT: int = Field(default=8000, description="API port")
    API_V1_PREFIX: str = Field(default="/api/v1", description="API prefix")

    # Supabase / Database
    SUPABASE_URL: Optional[str] = Field(default=None, description="Supabase project URL")
    SUPABASE_SERVICE_ROLE_KEY: Optional[str] = Field(default=None, description="Supabase service role key")
    DATABASE_URL: Optional[str] = Field(
        default=None,
        description="SQLAlchemy database connection URL"
    )
    DIRECT_URL: Optional[str] = Field(
        default=None,
        description="Direct database connection URL for migrations"
    )
    DB_HOST: Optional[str] = Field(default=None)
    DB_PORT: int = Field(default=5432)
    DB_USER: Optional[str] = Field(default=None)
    DB_PASSWORD: Optional[str] = Field(default=None)
    DB_NAME: Optional[str] = Field(default=None)

    # LLM Settings
    OPENAI_API_KEY: Optional[str] = Field(default=None)
    LITELLM_API_KEY: Optional[str] = Field(default=None)
    DEFAULT_MODEL: str = Field(default="gpt-4o-mini")
    REASONING_MODEL: str = Field(default="gpt-4o")

    # Connector / Mailbox Settings
    GMAIL_CLIENT_ID: Optional[str] = Field(default=None)
    GMAIL_CLIENT_SECRET: Optional[str] = Field(default=None)
    GMAIL_REFRESH_TOKEN: Optional[str] = Field(default=None)
    MOCK_EMAIL_CONNECTOR: bool = Field(default=True, description="Use test/mock mailbox connector for tests/local dev")

    @field_validator("API_PORT", mode="before")
    @classmethod
    def parse_api_port(cls, v):
        if v is None or v == "" or (isinstance(v, str) and not v.strip()):
            return 8000
        return int(v)

    @field_validator("DB_PORT", mode="before")
    @classmethod
    def parse_db_port(cls, v):
        if v is None or v == "" or (isinstance(v, str) and not v.strip()):
            return 5432
        return int(v)

    @field_validator(
        "DATABASE_URL",
        "DIRECT_URL",
        "DB_HOST",
        "DB_USER",
        "DB_PASSWORD",
        "DB_NAME",
        "SUPABASE_URL",
        "SUPABASE_SERVICE_ROLE_KEY",
        "OPENAI_API_KEY",
        "LITELLM_API_KEY",
        "GMAIL_CLIENT_ID",
        "GMAIL_CLIENT_SECRET",
        "GMAIL_REFRESH_TOKEN",
        mode="before",
    )
    @classmethod
    def empty_str_to_none(cls, v):
        if isinstance(v, str) and not v.strip():
            return None
        return v

    def get_database_url(self) -> str:
        """Return a valid SQLAlchemy connection URL. If using Supabase direct url with postgresql:// or postgres://,
        normalize to postgresql+psycopg:// for SQLAlchemy 2 with psycopg3 if needed, or fallback to sqlite for local tests.
        """
        url: Optional[str] = None
        if self.DATABASE_URL and self.DATABASE_URL.strip():
            url = self.DATABASE_URL.strip()
        elif self.DB_HOST and self.DB_USER and self.DB_PASSWORD and self.DB_NAME:
            port = self.DB_PORT or 5432
            url = f"postgresql://{self.DB_USER}:{self.DB_PASSWORD}@{self.DB_HOST}:{port}/{self.DB_NAME}"

        if url:
            if url.startswith("postgres://"):
                url = url.replace("postgres://", "postgresql://", 1)
            # Pick installed PostgreSQL DBAPI driver (psycopg 3 or psycopg2)
            if url.startswith("postgresql://") and not url.startswith("postgresql+"):
                try:
                    import psycopg  # noqa: F401
                    url = url.replace("postgresql://", "postgresql+psycopg://", 1)
                except ImportError:
                    try:
                        import psycopg2  # noqa: F401
                        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
                    except ImportError:
                        pass
            return url

        return "sqlite+pysqlite:///:memory:"


@lru_cache()
def get_settings() -> Settings:
    """Return cached application settings singleton."""
    return Settings()

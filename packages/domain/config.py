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

    # Cryptography
    CREDENTIALS_ENCRYPTION_KEY: Optional[str] = Field(default=None, description="32-byte base64 Fernet key for encrypting stored OAuth credentials. Generate with: python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'")

    # LLM Settings
    OPENAI_API_KEY: Optional[str] = Field(default=None)
    LITELLM_API_KEY: Optional[str] = Field(default=None)
    GEMINI_API_KEY: Optional[str] = Field(default=None)
    DEFAULT_MODEL: str = Field(default="gemini/gemini-1.5-flash")
    REASONING_MODEL: str = Field(default="gemini/gemini-1.5-pro")

    # Connector / Mailbox Settings
    GMAIL_CLIENT_ID: Optional[str] = Field(default=None)
    GMAIL_CLIENT_SECRET: Optional[str] = Field(default=None)
    GMAIL_REFRESH_TOKEN: Optional[str] = Field(default=None)
    MOCK_EMAIL_CONNECTOR: bool = Field(default=True, description="Use test/mock mailbox connector for tests/local dev")

    # Email dispatch settings for dispute sending
    SMTP_HOST: Optional[str] = Field(default=None, description="SMTP server hostname")
    SMTP_PORT: int = Field(default=587, description="SMTP server port")
    SMTP_USER: Optional[str] = Field(default=None, description="SMTP username/email")
    SMTP_PASSWORD: Optional[str] = Field(default=None, description="SMTP password")
    DISPUTE_SENDER_EMAIL: Optional[str] = Field(default=None, description="From address for dispute emails")
    DISPUTE_EMAIL_ENABLED: bool = Field(default=False, description="Master switch - must be True to send real emails")

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
        "GEMINI_API_KEY",
        "GMAIL_CLIENT_ID",
        "GMAIL_CLIENT_SECRET",
        "GMAIL_REFRESH_TOKEN",
        "SMTP_HOST",
        "SMTP_PORT",
        "SMTP_USER",
        "SMTP_PASSWORD",
        "DISPUTE_SENDER_EMAIL",
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
            import urllib.parse
            port = self.DB_PORT or 5432
            # Handle if user didn't URL-encode the password in the env variable
            pwd = urllib.parse.quote_plus(urllib.parse.unquote_plus(self.DB_PASSWORD))
            url = f"postgresql://{self.DB_USER}:{pwd}@{self.DB_HOST}:{port}/{self.DB_NAME}"
            
        # Vercel Serverless doesn't support IPv6 outbound.
        # If the user provided the direct IPv6 Supabase URL, auto-upgrade it to the IPv4 pooler.
        if url and "db.jtbjhoikomcvnenfucjt.supabase.co" in url:
            url = url.replace("db.jtbjhoikomcvnenfucjt.supabase.co:5432", "aws-0-ap-northeast-1.pooler.supabase.com:6543")
            url = url.replace("db.jtbjhoikomcvnenfucjt.supabase.co", "aws-0-ap-northeast-1.pooler.supabase.com")
            if "postgres.xxx:" in url:
                url = url.replace("postgres.xxx:", "postgres.jtbjhoikomcvnenfucjt:", 1)
            elif "postgres:" in url and "postgres.jtbjhoikomcvnenfucjt" not in url:
                url = url.replace("postgres:", "postgres.jtbjhoikomcvnenfucjt:", 1)
            
        if url and "aws-0-region.pooler.supabase.com" in url:
            url = url.replace("aws-0-region.pooler.supabase.com", "aws-0-ap-northeast-1.pooler.supabase.com")
            if "postgres.xxx:" in url:
                url = url.replace("postgres.xxx:", "postgres.jtbjhoikomcvnenfucjt:", 1)
            elif "postgres:" in url and "postgres.jtbjhoikomcvnenfucjt" not in url:
                url = url.replace("postgres:", "postgres.jtbjhoikomcvnenfucjt:", 1)

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

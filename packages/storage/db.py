"""Database session and connection management."""

from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, declarative_base, sessionmaker
from sqlalchemy.pool import StaticPool

from packages.domain.config import get_settings
from packages.domain.logging import logger

settings = get_settings()
db_url = settings.get_database_url()

# Dialect-specific engine configuration: SQLite does not accept pool_size/max_overflow
if "sqlite" in db_url:
    engine = create_engine(
        db_url,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
else:
    from sqlalchemy.pool import NullPool
    engine = create_engine(
        db_url,
        poolclass=NullPool,
        future=True,
    )

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
    expire_on_commit=False,
)

Base = declarative_base()

_sqlite_tables_created = False


def init_db() -> None:
    """Ensure database schema tables are created."""
    try:
        from packages.domain import models  # noqa: F401
        Base.metadata.create_all(bind=engine)
        logger.info("Database schema verified/created successfully.")
    except Exception as e:
        logger.warning(f"Database schema initialization skipped or failed: {e}")


def _ensure_sqlite_tables() -> None:
    """Ensure in-memory SQLite database has tables initialized when running without external migration."""
    global _sqlite_tables_created
    if not _sqlite_tables_created and "sqlite" in db_url:
        init_db()
        _sqlite_tables_created = True


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency for yielding database session."""
    _ensure_sqlite_tables()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def get_db_context() -> Generator[Session, None, None]:
    """Context manager for standalone database session usage."""
    _ensure_sqlite_tables()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def check_db_connection() -> bool:
    """Check if the database is reachable."""
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception as e:
        logger.error(f"Database connection check failed: {e}")
        return False

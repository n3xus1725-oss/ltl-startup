"""Database migration script to apply all schema tables safely."""

import sys

from packages.domain.config import get_settings
from packages.domain.logging import logger
from packages.storage.db import Base, engine


def apply_migrations():
    """Create all tables in the connected database if they do not exist."""
    settings = get_settings()
    logger.info("Applying database migrations...", extra={"db_host": settings.DB_HOST})
    try:
        Base.metadata.create_all(bind=engine)
        logger.info("All 12 tables created or already exist successfully.")
        return True
    except Exception as e:
        logger.error(f"Migration failed: {e}", exc_info=True)
        return False


if __name__ == "__main__":
    success = apply_migrations()
    sys.exit(0 if success else 1)

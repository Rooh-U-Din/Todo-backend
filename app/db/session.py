"""Database session management for Neon PostgreSQL."""

from collections.abc import Generator

from sqlmodel import Session, create_engine

from app.config import get_settings

settings = get_settings()

# Convert postgresql:// to postgresql+psycopg:// for psycopg v3 driver
database_url = settings.DATABASE_URL
if database_url.startswith("postgresql://"):
    database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)

engine = create_engine(
    database_url,
    echo=False,
    pool_pre_ping=True,
    connect_args={"sslmode": "require"} if settings.DATABASE_URL else {},
)


def get_session() -> Generator[Session, None, None]:
    """Get database session with automatic cleanup."""
    with Session(engine) as session:
        yield session

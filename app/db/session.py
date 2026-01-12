"""Database session management for Neon PostgreSQL."""

from collections.abc import Generator
from urllib.parse import urlparse, parse_qs

from sqlmodel import Session, create_engine

from app.config import get_settings

settings = get_settings()

# Convert postgresql:// to postgresql+psycopg:// for psycopg v3 driver
database_url = settings.DATABASE_URL
if database_url.startswith("postgresql://"):
    database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)

# Build connect_args from URL parameters if present
connect_args: dict = {}
if settings.DATABASE_URL:
    parsed = urlparse(settings.DATABASE_URL)
    query_params = parse_qs(parsed.query)

    # Extract SSL and connection parameters from URL
    if "sslmode" in query_params:
        connect_args["sslmode"] = query_params["sslmode"][0]
    else:
        connect_args["sslmode"] = "require"

    # Handle channel_binding if present (Neon PostgreSQL)
    if "channel_binding" in query_params:
        connect_args["channel_binding"] = query_params["channel_binding"][0]

engine = create_engine(
    database_url,
    echo=False,
    pool_pre_ping=True,
    connect_args=connect_args,
)


def get_session() -> Generator[Session, None, None]:
    """Get database session with automatic cleanup."""
    with Session(engine) as session:
        yield session

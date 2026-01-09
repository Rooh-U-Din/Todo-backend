"""Alembic environment configuration for Phase V migrations."""

from logging.config import fileConfig

from sqlalchemy import pool
from sqlmodel import SQLModel, create_engine

from alembic import context

# Import all models so SQLModel knows about them
from app.models import (  # noqa: F401
    User, Task, Conversation, Message,
    TaskReminder, TaskTag, TaskTagAssociation,
    TaskEvent, AuditLog, NotificationDelivery,
)
from app.config import get_settings

# This is the Alembic Config object
config = context.config

# Set up logging from alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Target metadata for 'autogenerate'
target_metadata = SQLModel.metadata

# Get database URL from settings
settings = get_settings()
database_url = settings.DATABASE_URL
if database_url.startswith("postgresql://"):
    database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL and not an Engine.
    Calls to context.execute() here emit the given string to the script output.
    """
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine and associate a connection
    with the context.
    """
    connectable = create_engine(
        database_url,
        poolclass=pool.NullPool,
        connect_args={"sslmode": "require"} if database_url else {},
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

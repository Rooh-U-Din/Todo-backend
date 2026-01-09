"""TaskEvent entity model for Phase V event-driven architecture."""

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from sqlmodel import Field, SQLModel, Column
from sqlalchemy.dialects.postgresql import JSONB


class ProcessingStatus(str, Enum):
    """Processing status for background workers."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class TaskEvent(SQLModel, table=True):
    """Task event database model for outbox pattern.

    Phase V Step 4: Extended with processing state for background workers.
    """

    __tablename__ = "task_events"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    event_type: str = Field(max_length=50, index=True)
    task_id: UUID | None = Field(default=None, foreign_key="tasks.id", index=True)
    user_id: UUID = Field(foreign_key="users.id", index=True)
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB))
    correlation_id: UUID | None = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    published_at: datetime | None = Field(default=None)
    published: bool = Field(default=False, index=True)

    # Phase V Step 4: Worker processing fields
    processing_status: ProcessingStatus = Field(
        default=ProcessingStatus.PENDING, index=True
    )
    processed_at: datetime | None = Field(default=None)
    retry_count: int = Field(default=0)
    last_error: str | None = Field(default=None, max_length=1000)


class TaskEventCreate(SQLModel):
    """Schema for task event creation."""

    event_type: str = Field(max_length=50)
    task_id: UUID | None = None
    user_id: UUID
    payload: dict[str, Any] = Field(default_factory=dict)
    correlation_id: UUID | None = None


class TaskEventResponse(SQLModel):
    """Schema for task event response."""

    id: UUID
    event_type: str
    task_id: UUID | None
    user_id: UUID
    payload: dict[str, Any]
    correlation_id: UUID | None
    created_at: datetime
    published_at: datetime | None
    published: bool
    # Phase V Step 4: Worker processing fields
    processing_status: ProcessingStatus
    processed_at: datetime | None
    retry_count: int
    last_error: str | None

    model_config = {"from_attributes": True}

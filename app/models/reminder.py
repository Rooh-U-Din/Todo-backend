"""TaskReminder entity model for Phase V."""

from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlmodel import Field, Relationship, SQLModel

if TYPE_CHECKING:
    from app.models.task import Task
    from app.models.user import User


class ReminderStatus(str, Enum):
    """Reminder status values."""
    PENDING = "pending"
    SENT = "sent"
    CANCELLED = "cancelled"
    FAILED = "failed"


class TaskReminder(SQLModel, table=True):
    """Task reminder database model."""

    __tablename__ = "task_reminders"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    task_id: UUID = Field(foreign_key="tasks.id", index=True)
    user_id: UUID = Field(foreign_key="users.id", index=True)
    remind_at: datetime = Field(index=True)
    status: ReminderStatus = Field(default=ReminderStatus.PENDING)
    dapr_job_id: str | None = Field(default=None, max_length=255)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    sent_at: datetime | None = Field(default=None)


class ReminderCreate(SQLModel):
    """Schema for reminder creation."""

    remind_at: datetime


class ReminderResponse(SQLModel):
    """Schema for reminder response."""

    id: UUID
    task_id: UUID
    remind_at: datetime
    status: ReminderStatus
    created_at: datetime
    sent_at: datetime | None

    model_config = {"from_attributes": True}

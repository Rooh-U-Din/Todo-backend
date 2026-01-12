"""Task entity model."""

from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlmodel import Field, Relationship, SQLModel

if TYPE_CHECKING:
    from app.models.user import User


# Phase V: Enumerations
class RecurrenceType(str, Enum):
    """Task recurrence types."""
    NONE = "none"
    DAILY = "daily"
    WEEKLY = "weekly"
    CUSTOM = "custom"


class Priority(str, Enum):
    """Task priority levels."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class TaskBase(SQLModel):
    """Base Task schema."""

    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)


class Task(TaskBase, table=True):
    """Task database model."""

    __tablename__ = "tasks"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="users.id", index=True)
    is_completed: bool = Field(default=False)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # Phase V: Extended fields (commented out until migrations are run)
    # Uncomment after running: alembic upgrade head
    # recurrence_type: RecurrenceType | None = Field(default=RecurrenceType.NONE, nullable=True)
    # recurrence_interval: int | None = Field(default=None, nullable=True)  # Days for custom recurrence
    # next_occurrence_at: datetime | None = Field(default=None, nullable=True)
    # due_at: datetime | None = Field(default=None, index=True, nullable=True)
    # priority: Priority | None = Field(default=Priority.MEDIUM, nullable=True)
    # parent_task_id: UUID | None = Field(default=None, foreign_key="tasks.id", nullable=True)

    user: "User" = Relationship(back_populates="tasks")


class TaskCreate(SQLModel):
    """Schema for task creation."""

    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    # Phase V: Extended fields (optional for backward compatibility)
    recurrence_type: RecurrenceType | None = Field(default=None)
    recurrence_interval: int | None = Field(default=None, ge=1, le=365)
    due_at: datetime | None = Field(default=None)
    priority: Priority | None = Field(default=None)


class TaskUpdate(SQLModel):
    """Schema for task update."""

    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    is_completed: bool | None = None
    # Phase V: Extended fields
    recurrence_type: RecurrenceType | None = None
    recurrence_interval: int | None = Field(default=None, ge=1, le=365)
    due_at: datetime | None = None
    priority: Priority | None = None


class TaskResponse(SQLModel):
    """Schema for task response."""

    id: UUID
    title: str
    description: str | None
    is_completed: bool
    created_at: datetime
    updated_at: datetime
    # Phase V: Extended fields (commented out until migrations are run)
    # recurrence_type: RecurrenceType
    # recurrence_interval: int | None
    # next_occurrence_at: datetime | None
    # due_at: datetime | None
    # priority: Priority
    # parent_task_id: UUID | None

    model_config = {"from_attributes": True}


class TaskListResponse(SQLModel):
    """Schema for task list response."""

    tasks: list[TaskResponse]
    total: int

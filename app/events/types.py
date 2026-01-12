"""Event type definitions for Phase V event-driven architecture."""

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class EventType(str, Enum):
    """Versioned event types for task lifecycle."""

    TASK_CREATED = "task.created.v1"
    TASK_UPDATED = "task.updated.v1"
    TASK_COMPLETED = "task.completed.v1"
    TASK_DELETED = "task.deleted.v1"
    TASK_RECURRED = "task.recurred.v1"  # Phase V Step 3: Recurring task generated

    # Phase V Layer 2: Reminder events
    REMINDER_SCHEDULED = "reminder.scheduled.v1"
    REMINDER_CANCELLED = "reminder.cancelled.v1"
    REMINDER_SENT = "reminder.sent.v1"


class TaskEventData(BaseModel):
    """CloudEvents-compatible event payload for task events.

    Follows CloudEvents specification with custom task data.
    """

    # CloudEvents required fields
    event_id: UUID = Field(description="Unique event identifier")
    event_type: EventType = Field(description="Event type (versioned)")

    # Domain-specific fields
    aggregate_type: str = Field(default="task", description="Aggregate type")
    aggregate_id: UUID = Field(description="Task ID (aggregate root)")
    user_id: UUID = Field(description="User who triggered the event")
    timestamp: datetime = Field(
        default_factory=datetime.utcnow,
        description="Event timestamp (UTC ISO format)",
    )

    # Event metadata
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional event metadata",
    )

    # Task-specific data (varies by event type)
    data: dict[str, Any] = Field(
        default_factory=dict,
        description="Event-specific payload data",
    )

    model_config = {"json_encoders": {UUID: str, datetime: lambda v: v.isoformat()}}

    def to_cloudevents_dict(self) -> dict[str, Any]:
        """Convert to CloudEvents JSON format."""
        return {
            "specversion": "1.0",
            "type": self.event_type.value,
            "source": "/backend/tasks",
            "id": str(self.event_id),
            "time": self.timestamp.isoformat() + "Z",
            "datacontenttype": "application/json",
            "data": {
                "aggregate_type": self.aggregate_type,
                "aggregate_id": str(self.aggregate_id),
                "user_id": str(self.user_id),
                "metadata": self.metadata,
                **self.data,
            },
        }

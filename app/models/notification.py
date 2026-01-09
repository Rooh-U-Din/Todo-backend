"""NotificationDelivery entity model for Phase V notifications."""

from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from sqlmodel import Field, SQLModel


class NotificationChannel(str, Enum):
    """Notification delivery channels."""
    EMAIL = "email"
    WEB_PUSH = "web_push"


class DeliveryStatus(str, Enum):
    """Notification delivery status.

    Phase V Step 4: Extended with PROCESSING state for worker idempotency.
    """

    PENDING = "pending"
    PROCESSING = "processing"  # Phase V Step 4: In-progress state
    SENT = "sent"
    FAILED = "failed"


class NotificationDelivery(SQLModel, table=True):
    """Notification delivery database model.

    Phase V Step 4: Extended with retry tracking for background workers.
    """

    __tablename__ = "notification_deliveries"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="users.id", index=True)
    reminder_id: UUID | None = Field(default=None, foreign_key="task_reminders.id")
    channel: NotificationChannel
    recipient: str = Field(max_length=255)
    subject: str | None = Field(default=None, max_length=200)
    message: str
    status: DeliveryStatus = Field(default=DeliveryStatus.PENDING, index=True)
    sent_at: datetime | None = Field(default=None)
    error_message: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    # Phase V Step 4: Worker retry tracking
    retry_count: int = Field(default=0)
    next_retry_at: datetime | None = Field(default=None, index=True)


class NotificationCreate(SQLModel):
    """Schema for notification creation."""

    user_id: UUID
    reminder_id: UUID | None = None
    channel: NotificationChannel
    recipient: str = Field(max_length=255)
    subject: str | None = Field(default=None, max_length=200)
    message: str


class NotificationResponse(SQLModel):
    """Schema for notification response."""

    id: UUID
    user_id: UUID
    reminder_id: UUID | None
    channel: NotificationChannel
    recipient: str
    subject: str | None
    message: str
    status: DeliveryStatus
    sent_at: datetime | None
    error_message: str | None
    created_at: datetime
    # Phase V Step 4: Worker retry tracking
    retry_count: int
    next_retry_at: datetime | None

    model_config = {"from_attributes": True}


class NotificationListResponse(SQLModel):
    """Schema for notification list response."""

    notifications: list[NotificationResponse]
    total: int

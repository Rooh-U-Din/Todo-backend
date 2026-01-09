"""SQLModel entities for the Todo application."""

from app.models.conversation import Conversation, ConversationResponse
from app.models.message import Message, MessageCreate, MessageResponse
from app.models.task import Task, RecurrenceType, Priority
from app.models.user import User
# Phase V: New models
from app.models.reminder import TaskReminder, ReminderStatus
from app.models.tag import TaskTag, TaskTagAssociation
from app.models.task_event import TaskEvent
from app.models.audit_log import AuditLog
from app.models.notification import NotificationDelivery, NotificationChannel, DeliveryStatus

__all__ = [
    "User",
    "Task",
    "RecurrenceType",
    "Priority",
    "Conversation",
    "ConversationResponse",
    "Message",
    "MessageCreate",
    "MessageResponse",
    # Phase V
    "TaskReminder",
    "ReminderStatus",
    "TaskTag",
    "TaskTagAssociation",
    "TaskEvent",
    "AuditLog",
    "NotificationDelivery",
    "NotificationChannel",
    "DeliveryStatus",
]

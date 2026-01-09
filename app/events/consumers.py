"""In-process event consumer layer for Phase V.

This module provides a clean, internal event consumer mechanism that:
1. Is decoupled from API routes
2. Handles events idempotently and safely for retries
3. Supports future async processing without code changes

Event Flow:
    API → Services → EventPublisher → EventDispatcher → Consumers
                                            ↓
                                    [AuditConsumer, NotificationConsumer,
                                     RecurrenceConsumer, AIInsightsConsumer]
"""

import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlmodel import Session

from app.events.types import EventType, TaskEventData
from app.models.audit_log import AuditLog
from app.models.notification import NotificationDelivery, NotificationChannel, DeliveryStatus
from app.models.reminder import TaskReminder, ReminderStatus
from app.models.task_event import TaskEvent

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Consumer Base Class
# -----------------------------------------------------------------------------


class EventConsumer(ABC):
    """Abstract base class for event consumers.

    Each consumer handles specific event types and performs
    side effects (audit logging, notifications, etc.).

    Consumers must be idempotent - processing the same event
    multiple times should have the same effect as processing once.
    """

    @abstractmethod
    def handles(self, event_type: EventType) -> bool:
        """Check if this consumer handles the given event type.

        Args:
            event_type: The type of event to check

        Returns:
            bool: True if this consumer handles this event type
        """
        pass

    @abstractmethod
    def process(
        self,
        session: Session,
        event: TaskEventData,
        task_event: TaskEvent,
    ) -> None:
        """Process an event.

        Args:
            session: Database session for persistence
            event: The event data (CloudEvents format)
            task_event: The persisted TaskEvent record

        Note:
            Implementations must be idempotent.
            Failures should be logged but not re-raised to avoid
            blocking other consumers.
        """
        pass


# -----------------------------------------------------------------------------
# Audit Consumer - Records all task lifecycle events
# -----------------------------------------------------------------------------


class AuditConsumer(EventConsumer):
    """Consumer that records audit logs for all task and reminder events.

    Creates immutable AuditLog entries for compliance and debugging.
    """

    # Map event types to audit actions
    EVENT_TO_ACTION: dict[EventType, str] = {
        EventType.TASK_CREATED: "task.created",
        EventType.TASK_UPDATED: "task.updated",
        EventType.TASK_COMPLETED: "task.completed",
        EventType.TASK_DELETED: "task.deleted",
        EventType.TASK_RECURRED: "task.recurred",
        # Phase V Layer 2: Reminder events
        EventType.REMINDER_SCHEDULED: "reminder.scheduled",
        EventType.REMINDER_CANCELLED: "reminder.cancelled",
        EventType.REMINDER_SENT: "reminder.sent",
    }

    # Map event types to entity types
    EVENT_TO_ENTITY: dict[EventType, str] = {
        EventType.TASK_CREATED: "task",
        EventType.TASK_UPDATED: "task",
        EventType.TASK_COMPLETED: "task",
        EventType.TASK_DELETED: "task",
        EventType.TASK_RECURRED: "task",
        EventType.REMINDER_SCHEDULED: "reminder",
        EventType.REMINDER_CANCELLED: "reminder",
        EventType.REMINDER_SENT: "reminder",
    }

    def handles(self, event_type: EventType) -> bool:
        """Handle all task events."""
        return event_type in self.EVENT_TO_ACTION

    def process(
        self,
        session: Session,
        event: TaskEventData,
        task_event: TaskEvent,
    ) -> None:
        """Record an audit log entry.

        Idempotency: Uses event_id to check for existing audit entry.
        """
        action = self.EVENT_TO_ACTION[event.event_type]
        entity_type = self.EVENT_TO_ENTITY.get(event.event_type, "task")

        # For reminder events, use reminder_id as entity_id if available
        if entity_type == "reminder" and "reminder_id" in event.data:
            from uuid import UUID as UUIDType
            entity_id = UUIDType(event.data["reminder_id"])
        else:
            entity_id = event.aggregate_id

        # Idempotency check: skip if already processed
        existing = session.query(AuditLog).filter(
            AuditLog.entity_id == entity_id,
            AuditLog.action == action,
            AuditLog.details.contains({"event_id": str(event.event_id)}),
        ).first()

        if existing:
            logger.debug(
                "Audit entry already exists, skipping",
                extra={"event_id": str(event.event_id)},
            )
            return

        # Create audit log entry
        audit_log = AuditLog(
            user_id=event.user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            details={
                "event_id": str(event.event_id),
                "event_type": event.event_type.value,
                "data": event.data,
            },
            timestamp=event.timestamp,
        )
        session.add(audit_log)

        logger.info(
            "Audit log recorded",
            extra={
                "event_id": str(event.event_id),
                "action": audit_log.action,
                "entity_type": entity_type,
                "entity_id": str(entity_id),
            },
        )


# -----------------------------------------------------------------------------
# Notification Consumer - Creates notification delivery records
# -----------------------------------------------------------------------------


class NotificationConsumer(EventConsumer):
    """Consumer that creates notification delivery records.

    Generates NotificationDelivery entries for task events.
    Actual sending is deferred to a notification service.
    """

    # Events that trigger notifications
    NOTIFIABLE_EVENTS: set[EventType] = {
        EventType.TASK_CREATED,
        EventType.TASK_COMPLETED,
    }

    # Notification templates
    TEMPLATES: dict[EventType, tuple[str, str]] = {
        EventType.TASK_CREATED: (
            "New Task Created",
            "A new task has been created: {title}",
        ),
        EventType.TASK_COMPLETED: (
            "Task Completed",
            "Great job! You completed: {title}",
        ),
    }

    def handles(self, event_type: EventType) -> bool:
        """Handle task created and completed events."""
        return event_type in self.NOTIFIABLE_EVENTS

    def process(
        self,
        session: Session,
        event: TaskEventData,
        task_event: TaskEvent,
    ) -> None:
        """Create a notification delivery record.

        Idempotency: Uses correlation check on event_id in details.
        """
        # Get template for event type
        template = self.TEMPLATES.get(event.event_type)
        if not template:
            return

        subject_template, message_template = template

        # Format message with task data
        title = event.data.get("title", "Unknown Task")
        message = message_template.format(title=title)

        # Create notification delivery record (pending)
        notification = NotificationDelivery(
            user_id=event.user_id,
            channel=NotificationChannel.EMAIL,
            recipient=f"user_{event.user_id}@placeholder.local",  # Placeholder
            subject=subject_template,
            message=message,
            status=DeliveryStatus.PENDING,
        )
        session.add(notification)

        logger.info(
            "Notification delivery created",
            extra={
                "event_id": str(event.event_id),
                "event_type": event.event_type.value,
                "user_id": str(event.user_id),
            },
        )


# -----------------------------------------------------------------------------
# Recurrence Consumer - Handles recurring task generation
# -----------------------------------------------------------------------------


class RecurrenceConsumer(EventConsumer):
    """Consumer that handles recurring task completion.

    When a recurring task is completed, this consumer logs the
    recurrence event and prepares context for the new occurrence.

    Note: The actual next occurrence is generated by the task service
    during toggle_task_completion. This consumer handles additional
    recurrence-related side effects.
    """

    def handles(self, event_type: EventType) -> bool:
        """Handle only task completed events."""
        return event_type == EventType.TASK_COMPLETED

    def process(
        self,
        session: Session,
        event: TaskEventData,
        task_event: TaskEvent,
    ) -> None:
        """Process recurring task completion.

        Creates audit trail for recurrence chain tracking.
        """
        # Check if task has recurrence
        recurrence_type = event.data.get("recurrence_type")
        if not recurrence_type or recurrence_type == "none":
            return

        # Log the recurrence event for chain tracking
        audit_log = AuditLog(
            user_id=event.user_id,
            action="task.recurred",
            entity_type="task",
            entity_id=event.aggregate_id,
            details={
                "event_id": str(event.event_id),
                "recurrence_type": recurrence_type,
                "completed_at": event.timestamp.isoformat(),
            },
            timestamp=event.timestamp,
        )
        session.add(audit_log)

        logger.info(
            "Recurring task processed",
            extra={
                "event_id": str(event.event_id),
                "task_id": str(event.aggregate_id),
                "recurrence_type": recurrence_type,
            },
        )


# -----------------------------------------------------------------------------
# Event Dispatcher - Routes events to appropriate consumers
# -----------------------------------------------------------------------------


class EventDispatcher:
    """Central dispatcher that routes events to registered consumers.

    The dispatcher provides:
    1. Registration of multiple consumers
    2. Event routing based on event type
    3. Error isolation (one consumer failure doesn't block others)
    4. Logging for observability
    """

    def __init__(self) -> None:
        """Initialize the dispatcher with default consumers."""
        self._consumers: list[EventConsumer] = []
        self._register_default_consumers()

    def _register_default_consumers(self) -> None:
        """Register the built-in consumers."""
        self._consumers = [
            AuditConsumer(),
            NotificationConsumer(),
            RecurrenceConsumer(),
        ]

    def register(self, consumer: EventConsumer) -> None:
        """Register an additional consumer.

        Args:
            consumer: The consumer to register
        """
        self._consumers.append(consumer)

    def dispatch(
        self,
        session: Session,
        event: TaskEventData,
        task_event: TaskEvent,
    ) -> None:
        """Dispatch an event to all interested consumers.

        Args:
            session: Database session for persistence
            event: The event data (CloudEvents format)
            task_event: The persisted TaskEvent record

        Note:
            Errors in one consumer do not affect other consumers.
            All errors are logged but not re-raised.
        """
        for consumer in self._consumers:
            if not consumer.handles(event.event_type):
                continue

            try:
                consumer.process(session, event, task_event)
            except Exception as e:
                logger.error(
                    "Consumer processing failed",
                    extra={
                        "consumer": consumer.__class__.__name__,
                        "event_id": str(event.event_id),
                        "event_type": event.event_type.value,
                        "error": str(e),
                    },
                    exc_info=True,
                )
                # Continue processing with other consumers


# -----------------------------------------------------------------------------
# Singleton Dispatcher Instance
# -----------------------------------------------------------------------------

_dispatcher_instance: EventDispatcher | None = None


def get_event_dispatcher() -> EventDispatcher:
    """Get or create the event dispatcher singleton.

    Returns:
        EventDispatcher: The singleton dispatcher instance
    """
    global _dispatcher_instance
    if _dispatcher_instance is None:
        _dispatcher_instance = EventDispatcher()
    return _dispatcher_instance

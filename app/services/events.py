"""Unified event emission service for Phase V Layer 2.

This module provides a clean, high-level API for emitting events
throughout the application. It wraps the EventPublisher with
convenience methods for common event patterns.

Usage:
    from app.services.events import emit_event, emit_audit_log

    # Emit a task event
    emit_event(session, EventType.TASK_CREATED, task.id, user_id, {"title": task.title})

    # Emit an audit log
    emit_audit_log(session, user_id, "task.created", "task", task.id, {"title": task.title})
"""

import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlmodel import Session

from app.config import get_settings
from app.events.publisher import get_event_publisher
from app.events.types import EventType
from app.models.audit_log import AuditLog
from app.models.task_event import TaskEvent

logger = logging.getLogger(__name__)


def emit_event(
    session: Session,
    event_type: EventType,
    aggregate_id: UUID | None,
    user_id: UUID,
    data: dict[str, Any] | None = None,
    correlation_id: UUID | None = None,
) -> TaskEvent | None:
    """Emit an event using the outbox pattern.

    This is the primary entry point for event emission.
    Events are persisted atomically with the current transaction.

    Args:
        session: Database session (must be in active transaction)
        event_type: Type of event to emit
        aggregate_id: ID of the aggregate (task_id, reminder_id, etc.)
        user_id: ID of the user triggering the event
        data: Event-specific payload data
        correlation_id: Optional correlation ID for tracing

    Returns:
        TaskEvent if events are enabled, None otherwise
    """
    settings = get_settings()
    if not settings.EVENTS_ENABLED:
        return None

    publisher = get_event_publisher()

    task_event = publisher.emit(
        session=session,
        event_type=event_type,
        task_id=aggregate_id,
        user_id=user_id,
        data=data or {},
        correlation_id=correlation_id,
    )

    logger.debug(
        "Event emitted",
        extra={
            "event_id": str(task_event.id),
            "event_type": event_type.value,
            "aggregate_id": str(aggregate_id) if aggregate_id else None,
        },
    )

    return task_event


def emit_audit_log(
    session: Session,
    user_id: UUID | str,
    action: str,
    entity_type: str,
    entity_id: UUID | None = None,
    details: dict[str, Any] | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> AuditLog:
    """Emit an immutable audit log entry.

    Audit logs are always written regardless of EVENTS_ENABLED setting.
    They provide an immutable record of key actions in the system.

    Args:
        session: Database session
        user_id: ID of the actor (user UUID or "system")
        action: Action performed (e.g., "task.created", "reminder.scheduled")
        entity_type: Type of entity affected (e.g., "task", "reminder")
        entity_id: ID of the affected entity
        details: Additional context about the action
        ip_address: Client IP address (optional)
        user_agent: Client user agent (optional)

    Returns:
        AuditLog: The created audit log entry
    """
    # Handle system actor
    if isinstance(user_id, str) and user_id == "system":
        # Use a nil UUID for system actions
        from uuid import UUID as UUIDType
        actor_id = UUIDType("00000000-0000-0000-0000-000000000000")
    else:
        actor_id = user_id if isinstance(user_id, UUID) else UUID(user_id)

    audit_log = AuditLog(
        user_id=actor_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        details=details,
        ip_address=ip_address,
        user_agent=user_agent,
        timestamp=datetime.utcnow(),
    )
    session.add(audit_log)

    logger.info(
        "Audit log recorded",
        extra={
            "action": action,
            "entity_type": entity_type,
            "entity_id": str(entity_id) if entity_id else None,
            "user_id": str(actor_id),
        },
    )

    return audit_log


def emit_reminder_scheduled(
    session: Session,
    reminder_id: UUID,
    task_id: UUID,
    user_id: UUID,
    remind_at: datetime,
) -> TaskEvent | None:
    """Emit a reminder.scheduled event.

    Args:
        session: Database session
        reminder_id: ID of the scheduled reminder
        task_id: ID of the associated task
        user_id: ID of the user
        remind_at: When the reminder is scheduled for

    Returns:
        TaskEvent if events are enabled
    """
    return emit_event(
        session=session,
        event_type=EventType.REMINDER_SCHEDULED,
        aggregate_id=task_id,
        user_id=user_id,
        data={
            "reminder_id": str(reminder_id),
            "task_id": str(task_id),
            "remind_at": remind_at.isoformat(),
        },
    )


def emit_reminder_cancelled(
    session: Session,
    reminder_id: UUID,
    task_id: UUID,
    user_id: UUID,
    reason: str = "user_cancelled",
) -> TaskEvent | None:
    """Emit a reminder.cancelled event.

    Args:
        session: Database session
        reminder_id: ID of the cancelled reminder
        task_id: ID of the associated task
        user_id: ID of the user
        reason: Reason for cancellation

    Returns:
        TaskEvent if events are enabled
    """
    return emit_event(
        session=session,
        event_type=EventType.REMINDER_CANCELLED,
        aggregate_id=task_id,
        user_id=user_id,
        data={
            "reminder_id": str(reminder_id),
            "task_id": str(task_id),
            "reason": reason,
        },
    )


def emit_reminder_sent(
    session: Session,
    reminder_id: UUID,
    task_id: UUID,
    user_id: UUID,
) -> TaskEvent | None:
    """Emit a reminder.sent event.

    Args:
        session: Database session
        reminder_id: ID of the sent reminder
        task_id: ID of the associated task
        user_id: ID of the user

    Returns:
        TaskEvent if events are enabled
    """
    return emit_event(
        session=session,
        event_type=EventType.REMINDER_SENT,
        aggregate_id=task_id,
        user_id=user_id,
        data={
            "reminder_id": str(reminder_id),
            "task_id": str(task_id),
            "sent_at": datetime.utcnow().isoformat(),
        },
    )

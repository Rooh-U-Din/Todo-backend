"""Event-driven architecture module for Phase V.

Phase V Step 3: Extended with in-process event consumers.

Components:
- types.py: Event type definitions and CloudEvents schema
- publisher.py: Outbox pattern event publishing to Dapr
- consumers.py: In-process event handlers for immediate reactions
"""

from app.events.types import EventType, TaskEventData
from app.events.publisher import EventPublisher, get_event_publisher
from app.events.consumers import (
    EventConsumer,
    EventDispatcher,
    AuditConsumer,
    NotificationConsumer,
    RecurrenceConsumer,
    get_event_dispatcher,
)

__all__ = [
    # Types
    "EventType",
    "TaskEventData",
    # Publisher
    "EventPublisher",
    "get_event_publisher",
    # Consumers (Phase V Step 3)
    "EventConsumer",
    "EventDispatcher",
    "AuditConsumer",
    "NotificationConsumer",
    "RecurrenceConsumer",
    "get_event_dispatcher",
]

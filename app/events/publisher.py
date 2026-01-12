"""Event publisher module for Phase V event-driven architecture.

Implements the outbox pattern:
1. Persist event to TaskEvent table FIRST (transactional)
2. Attempt to publish via Dapr HTTP API
3. On publish failure, event remains unpublished for later retry
4. Publishing failures do NOT break API requests
"""

import logging
from datetime import datetime
from functools import lru_cache
from typing import Any
from uuid import UUID, uuid4

import httpx
from sqlmodel import Session

from app.events.types import EventType, TaskEventData
from app.models.task_event import TaskEvent

logger = logging.getLogger(__name__)

# Dapr HTTP API configuration
DAPR_HTTP_PORT = 3500
DAPR_PUBSUB_NAME = "taskpubsub"
DAPR_TOPIC_NAME = "task-events"


class EventPublisher:
    """Event publisher with outbox pattern support.

    The publisher follows the outbox pattern:
    1. Events are persisted to database first (guaranteed)
    2. Publishing to Dapr is attempted (best-effort)
    3. Failed publishes remain in outbox for retry
    """

    def __init__(self, dapr_port: int = DAPR_HTTP_PORT) -> None:
        """Initialize the event publisher.

        Args:
            dapr_port: Dapr sidecar HTTP port (default: 3500)
        """
        self.dapr_port = dapr_port
        self.dapr_url = f"http://localhost:{dapr_port}/v1.0/publish/{DAPR_PUBSUB_NAME}/{DAPR_TOPIC_NAME}"
        self._client: httpx.Client | None = None

    @property
    def client(self) -> httpx.Client:
        """Lazy-initialize HTTP client."""
        if self._client is None:
            self._client = httpx.Client(timeout=5.0)
        return self._client

    def create_event(
        self,
        event_type: EventType,
        task_id: UUID,
        user_id: UUID,
        data: dict[str, Any] | None = None,
        correlation_id: UUID | None = None,
    ) -> TaskEventData:
        """Create a new task event.

        Args:
            event_type: Type of event (task.created.v1, etc.)
            task_id: ID of the task (aggregate root)
            user_id: ID of the user who triggered the event
            data: Event-specific payload data
            correlation_id: Optional correlation ID for event tracing

        Returns:
            TaskEventData: The created event data
        """
        return TaskEventData(
            event_id=uuid4(),
            event_type=event_type,
            aggregate_id=task_id,
            user_id=user_id,
            timestamp=datetime.utcnow(),
            metadata={"correlation_id": str(correlation_id)} if correlation_id else {},
            data=data or {},
        )

    def persist_event(
        self,
        session: Session,
        event: TaskEventData,
    ) -> TaskEvent:
        """Persist event to database (outbox pattern step 1).

        This MUST be called within the same transaction as the
        business operation to ensure atomicity.

        Args:
            session: Database session (should be same as business operation)
            event: Event data to persist

        Returns:
            TaskEvent: The persisted database record
        """
        task_event = TaskEvent(
            id=event.event_id,
            event_type=event.event_type.value,
            task_id=event.aggregate_id,
            user_id=event.user_id,
            payload=event.to_cloudevents_dict(),
            correlation_id=event.metadata.get("correlation_id"),
            created_at=event.timestamp,
            published=False,
            published_at=None,
        )
        session.add(task_event)
        # Note: Do NOT commit here - let caller manage transaction
        return task_event

    def publish_event(
        self,
        session: Session,
        task_event: TaskEvent,
    ) -> bool:
        """Publish event to Dapr (outbox pattern step 2).

        This is called AFTER the transaction commits.
        Failures are logged but do NOT raise exceptions.

        Args:
            session: Database session for marking event as published
            task_event: The persisted event record

        Returns:
            bool: True if published successfully, False otherwise
        """
        try:
            response = self.client.post(
                self.dapr_url,
                json=task_event.payload,
                headers={"Content-Type": "application/cloudevents+json"},
            )
            response.raise_for_status()

            # Mark event as published
            task_event.published = True
            task_event.published_at = datetime.utcnow()
            session.add(task_event)
            session.commit()

            logger.info(
                "Event published successfully",
                extra={
                    "event_id": str(task_event.id),
                    "event_type": task_event.event_type,
                    "task_id": str(task_event.task_id),
                },
            )
            return True

        except httpx.ConnectError:
            # Dapr not running - this is expected in development without Dapr
            logger.warning(
                "Dapr not available, event stored in outbox",
                extra={
                    "event_id": str(task_event.id),
                    "event_type": task_event.event_type,
                },
            )
            return False

        except httpx.HTTPStatusError as e:
            logger.error(
                "Dapr publish failed with HTTP error",
                extra={
                    "event_id": str(task_event.id),
                    "event_type": task_event.event_type,
                    "status_code": e.response.status_code,
                    "response": e.response.text,
                },
            )
            return False

        except Exception as e:
            logger.error(
                "Unexpected error publishing event",
                extra={
                    "event_id": str(task_event.id),
                    "event_type": task_event.event_type,
                    "error": str(e),
                },
            )
            return False

    def emit(
        self,
        session: Session,
        event_type: EventType,
        task_id: UUID,
        user_id: UUID,
        data: dict[str, Any] | None = None,
        correlation_id: UUID | None = None,
    ) -> TaskEvent:
        """Emit an event using the outbox pattern.

        This is the main entry point for emitting events.
        It persists the event first, then attempts to publish.

        Args:
            session: Database session
            event_type: Type of event
            task_id: Task ID (aggregate root)
            user_id: User ID
            data: Event-specific data
            correlation_id: Optional correlation ID

        Returns:
            TaskEvent: The persisted event record
        """
        # Step 1: Create event data
        event = self.create_event(
            event_type=event_type,
            task_id=task_id,
            user_id=user_id,
            data=data,
            correlation_id=correlation_id,
        )

        # Step 2: Persist to database (within transaction)
        task_event = self.persist_event(session, event)

        # Note: Publishing happens AFTER commit in the service layer
        # This ensures the event is persisted even if publish fails

        return task_event

    def close(self) -> None:
        """Close the HTTP client."""
        if self._client is not None:
            self._client.close()
            self._client = None


# Singleton instance for dependency injection
_publisher_instance: EventPublisher | None = None


@lru_cache
def get_event_publisher() -> EventPublisher:
    """Get or create the event publisher singleton.

    Returns:
        EventPublisher: The publisher instance
    """
    global _publisher_instance
    if _publisher_instance is None:
        _publisher_instance = EventPublisher()
    return _publisher_instance

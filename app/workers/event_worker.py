"""Event processing worker for Phase V Step 4.

Processes TaskEvent records from the outbox:
1. Fetches unpublished events
2. Dispatches to in-process consumers
3. Attempts external publishing (Dapr/Kafka)
4. Marks events as completed or failed with retry
"""

import logging
from datetime import datetime, timedelta
from uuid import UUID

from sqlmodel import Session, select

from app.config import get_settings
from app.events.consumers import get_event_dispatcher
from app.events.publisher import get_event_publisher
from app.events.types import EventType, TaskEventData
from app.models.task_event import TaskEvent, ProcessingStatus
from app.workers.base import WorkerBase

logger = logging.getLogger(__name__)


class EventWorker(WorkerBase[TaskEvent]):
    """Worker for processing TaskEvent outbox.

    Processes events in two phases:
    1. In-process consumer dispatch (immediate side effects)
    2. External publishing to Dapr/Kafka (external systems)
    """

    @property
    def worker_name(self) -> str:
        return "EventWorker"

    def fetch_pending(self, session: Session) -> list[TaskEvent]:
        """Fetch pending events to process.

        Fetches events that are:
        - Not yet processed (PENDING status)
        - Or failed but eligible for retry

        Args:
            session: Database session

        Returns:
            List of TaskEvent records
        """
        settings = get_settings()
        now = datetime.utcnow()

        # Fetch PENDING events or FAILED events past retry delay
        retry_cutoff = now - timedelta(seconds=settings.WORKER_RETRY_DELAY_SECONDS)

        events = session.exec(
            select(TaskEvent)
            .where(
                (TaskEvent.processing_status == ProcessingStatus.PENDING)
                | (
                    (TaskEvent.processing_status == ProcessingStatus.FAILED)
                    & (TaskEvent.retry_count < self.max_retries)
                    & (TaskEvent.processed_at < retry_cutoff)
                )
            )
            .order_by(TaskEvent.created_at)
            .limit(self.batch_size)
        ).all()

        return list(events)

    def mark_processing(self, session: Session, item: TaskEvent) -> bool:
        """Mark event as processing with optimistic locking.

        Args:
            session: Database session
            item: The event to mark

        Returns:
            True if successfully marked, False if already processing
        """
        # Check current status
        if item.processing_status == ProcessingStatus.PROCESSING:
            return False

        item.processing_status = ProcessingStatus.PROCESSING
        session.add(item)
        session.flush()
        return True

    def process_item(self, session: Session, item: TaskEvent) -> None:
        """Process a single event.

        1. Dispatch to in-process consumers
        2. Attempt external publishing

        Args:
            session: Database session
            item: The event to process
        """
        settings = get_settings()

        # Build TaskEventData for dispatcher
        try:
            event_type = EventType(item.event_type)
        except ValueError:
            logger.warning(
                f"Unknown event type: {item.event_type}",
                extra={"event_id": str(item.id)},
            )
            # Still mark as completed to avoid infinite loop
            return

        event_data = TaskEventData(
            event_id=item.id,
            event_type=event_type,
            aggregate_id=item.task_id,
            user_id=item.user_id,
            timestamp=item.created_at,
            data=item.payload.get("data", {}),
            metadata=item.payload.get("metadata", {}),
        )

        # Phase 1: Dispatch to in-process consumers
        dispatcher = get_event_dispatcher()
        try:
            dispatcher.dispatch(session, event_data, item)
            logger.debug(
                f"Dispatched event {item.id} to consumers",
                extra={"event_id": str(item.id), "event_type": item.event_type},
            )
        except Exception as e:
            logger.error(
                f"Consumer dispatch failed for event {item.id}",
                extra={"event_id": str(item.id), "error": str(e)},
                exc_info=True,
            )
            # Continue to external publishing even if consumers fail

        # Phase 2: External publishing (if enabled)
        if settings.EVENTS_ENABLED:
            publisher = get_event_publisher()
            try:
                published = publisher.publish_event(session, item)
                if published:
                    logger.info(
                        f"Published event {item.id} to external broker",
                        extra={"event_id": str(item.id)},
                    )
            except Exception as e:
                logger.warning(
                    f"External publish failed for event {item.id}",
                    extra={"event_id": str(item.id), "error": str(e)},
                )
                # Don't fail the whole item if external publish fails

    def mark_completed(self, session: Session, item: TaskEvent) -> None:
        """Mark event as completed.

        Args:
            session: Database session
            item: The completed event
        """
        item.processing_status = ProcessingStatus.COMPLETED
        item.processed_at = datetime.utcnow()
        item.last_error = None
        session.add(item)

    def mark_failed(
        self, session: Session, item: TaskEvent, error: str, can_retry: bool
    ) -> None:
        """Mark event as failed.

        Args:
            session: Database session
            item: The failed event
            error: Error message
            can_retry: Whether to schedule for retry
        """
        item.retry_count += 1
        item.last_error = error[:1000] if error else None
        item.processed_at = datetime.utcnow()

        if can_retry:
            item.processing_status = ProcessingStatus.FAILED
        else:
            # Max retries reached, mark as permanently failed
            item.processing_status = ProcessingStatus.FAILED

        session.add(item)

    def get_item_id(self, item: TaskEvent) -> UUID:
        """Get the event ID.

        Args:
            item: The event

        Returns:
            UUID of the event
        """
        return item.id

    def should_retry(self, item: TaskEvent) -> bool:
        """Check if event should be retried.

        Args:
            item: The event to check

        Returns:
            True if should retry
        """
        return item.retry_count < self.max_retries

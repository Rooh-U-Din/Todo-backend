"""Base worker abstraction for Phase V Step 4.

Provides a clean interface for background workers that:
1. Poll for work items (events, notifications, reminders)
2. Process items with idempotency guarantees
3. Handle failures with retry logic
4. Provide structured logging and observability

Design Principles:
- No external infrastructure dependencies
- In-process execution only
- Logic-first, infra-later design
- Testable via direct function calls
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Generic, TypeVar
from uuid import UUID

from sqlmodel import Session

logger = logging.getLogger(__name__)


class WorkerStatus(str, Enum):
    """Status of a worker run."""

    SUCCESS = "success"
    PARTIAL = "partial"  # Some items processed, some failed
    FAILED = "failed"
    NO_WORK = "no_work"


@dataclass
class WorkerResult:
    """Result of a worker processing cycle.

    Attributes:
        status: Overall status of the worker run
        processed_count: Number of items successfully processed
        failed_count: Number of items that failed
        duration_ms: Time taken for the processing cycle
        errors: List of error details for failed items
        metadata: Additional worker-specific metadata
    """

    status: WorkerStatus
    processed_count: int = 0
    failed_count: int = 0
    duration_ms: float = 0.0
    errors: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for logging."""
        return {
            "status": self.status.value,
            "processed_count": self.processed_count,
            "failed_count": self.failed_count,
            "duration_ms": self.duration_ms,
            "errors": self.errors,
            "metadata": self.metadata,
        }


# Generic type for work items
T = TypeVar("T")


class WorkerBase(ABC, Generic[T]):
    """Abstract base class for background workers.

    Workers follow this lifecycle:
    1. fetch_pending() - Get items to process
    2. mark_processing() - Mark item as in-progress (idempotency)
    3. process_item() - Do the actual work
    4. mark_completed() or mark_failed() - Update final status

    Subclasses must implement all abstract methods.
    """

    def __init__(self, batch_size: int = 50, max_retries: int = 3) -> None:
        """Initialize the worker.

        Args:
            batch_size: Maximum items to process per cycle
            max_retries: Maximum retry attempts for failed items
        """
        self.batch_size = batch_size
        self.max_retries = max_retries
        self._logger = logging.getLogger(self.__class__.__name__)

    @property
    @abstractmethod
    def worker_name(self) -> str:
        """Return the worker name for logging."""
        pass

    @abstractmethod
    def fetch_pending(self, session: Session) -> list[T]:
        """Fetch pending items to process.

        Args:
            session: Database session

        Returns:
            List of items to process (up to batch_size)
        """
        pass

    @abstractmethod
    def mark_processing(self, session: Session, item: T) -> bool:
        """Mark an item as being processed (for idempotency).

        This should use optimistic locking or similar to prevent
        duplicate processing.

        Args:
            session: Database session
            item: The item to mark

        Returns:
            True if successfully marked, False if already being processed
        """
        pass

    @abstractmethod
    def process_item(self, session: Session, item: T) -> None:
        """Process a single item.

        Args:
            session: Database session
            item: The item to process

        Raises:
            Exception: If processing fails
        """
        pass

    @abstractmethod
    def mark_completed(self, session: Session, item: T) -> None:
        """Mark an item as successfully completed.

        Args:
            session: Database session
            item: The completed item
        """
        pass

    @abstractmethod
    def mark_failed(
        self, session: Session, item: T, error: str, can_retry: bool
    ) -> None:
        """Mark an item as failed.

        Args:
            session: Database session
            item: The failed item
            error: Error message
            can_retry: Whether the item can be retried
        """
        pass

    @abstractmethod
    def get_item_id(self, item: T) -> UUID:
        """Get the unique identifier for an item.

        Args:
            item: The item

        Returns:
            UUID identifier
        """
        pass

    def should_retry(self, item: T) -> bool:
        """Check if an item should be retried.

        Default implementation checks retry_count against max_retries.
        Override for custom logic.

        Args:
            item: The item to check

        Returns:
            True if should retry, False otherwise
        """
        if hasattr(item, "retry_count"):
            return item.retry_count < self.max_retries
        return False

    def run(self, session: Session) -> WorkerResult:
        """Execute one processing cycle.

        This is the main entry point for worker execution.

        Args:
            session: Database session

        Returns:
            WorkerResult with processing statistics
        """
        start_time = datetime.utcnow()
        processed = 0
        failed = 0
        errors: list[dict[str, Any]] = []

        self._logger.info(
            f"[{self.worker_name}] Starting processing cycle",
            extra={"batch_size": self.batch_size},
        )

        try:
            # Fetch pending items
            items = self.fetch_pending(session)

            if not items:
                self._logger.debug(f"[{self.worker_name}] No pending items")
                return WorkerResult(
                    status=WorkerStatus.NO_WORK,
                    duration_ms=self._elapsed_ms(start_time),
                )

            self._logger.info(
                f"[{self.worker_name}] Found {len(items)} items to process"
            )

            # Process each item
            for item in items:
                item_id = self.get_item_id(item)

                try:
                    # Mark as processing (idempotency check)
                    if not self.mark_processing(session, item):
                        self._logger.debug(
                            f"[{self.worker_name}] Item {item_id} already processing"
                        )
                        continue

                    # Process the item
                    self.process_item(session, item)

                    # Mark completed
                    self.mark_completed(session, item)
                    session.commit()

                    processed += 1
                    self._logger.info(
                        f"[{self.worker_name}] Processed item {item_id}",
                        extra={"item_id": str(item_id)},
                    )

                except Exception as e:
                    session.rollback()
                    failed += 1
                    error_msg = str(e)[:500]  # Truncate long errors

                    can_retry = self.should_retry(item)
                    self.mark_failed(session, item, error_msg, can_retry)
                    session.commit()

                    errors.append({
                        "item_id": str(item_id),
                        "error": error_msg,
                        "can_retry": can_retry,
                    })

                    self._logger.error(
                        f"[{self.worker_name}] Failed to process item {item_id}",
                        extra={
                            "item_id": str(item_id),
                            "error": error_msg,
                            "can_retry": can_retry,
                        },
                        exc_info=True,
                    )

        except Exception as e:
            self._logger.error(
                f"[{self.worker_name}] Worker cycle failed",
                extra={"error": str(e)},
                exc_info=True,
            )
            return WorkerResult(
                status=WorkerStatus.FAILED,
                duration_ms=self._elapsed_ms(start_time),
                errors=[{"error": str(e)}],
            )

        # Determine overall status
        if failed == 0 and processed > 0:
            status = WorkerStatus.SUCCESS
        elif processed > 0 and failed > 0:
            status = WorkerStatus.PARTIAL
        elif failed > 0:
            status = WorkerStatus.FAILED
        else:
            status = WorkerStatus.NO_WORK

        result = WorkerResult(
            status=status,
            processed_count=processed,
            failed_count=failed,
            duration_ms=self._elapsed_ms(start_time),
            errors=errors,
        )

        self._logger.info(
            f"[{self.worker_name}] Cycle complete",
            extra=result.to_dict(),
        )

        return result

    def _elapsed_ms(self, start: datetime) -> float:
        """Calculate elapsed time in milliseconds."""
        return (datetime.utcnow() - start).total_seconds() * 1000

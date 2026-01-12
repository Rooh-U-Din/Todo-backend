"""Worker runner for Phase V Step 4.

Provides easy-to-use entry points for running workers:
- run_worker_once(): Single processing cycle
- run_worker_loop(): Continuous processing with interval

Design Principles:
- Works in normal terminal (no special runtime)
- Structured logging for observability
- No silent failures
- Clean shutdown handling
"""

import logging
import signal
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlmodel import Session

from app.config import get_settings
from app.db.session import get_session, engine
from app.workers.base import WorkerBase, WorkerResult, WorkerStatus
from app.workers.event_worker import EventWorker
from app.workers.notification_worker import NotificationWorker
from app.workers.reminder_worker import ReminderWorker

logger = logging.getLogger(__name__)


@dataclass
class RunnerResult:
    """Result of a complete worker runner cycle.

    Attributes:
        started_at: When the run started
        completed_at: When the run completed
        workers_run: Number of workers executed
        total_processed: Total items processed across all workers
        total_failed: Total items failed across all workers
        worker_results: Individual results per worker
        errors: Top-level errors during run
    """

    started_at: datetime
    completed_at: datetime | None = None
    workers_run: int = 0
    total_processed: int = 0
    total_failed: int = 0
    worker_results: dict[str, WorkerResult] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for logging."""
        return {
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "duration_ms": (
                (self.completed_at - self.started_at).total_seconds() * 1000
                if self.completed_at
                else None
            ),
            "workers_run": self.workers_run,
            "total_processed": self.total_processed,
            "total_failed": self.total_failed,
            "worker_results": {
                name: result.to_dict()
                for name, result in self.worker_results.items()
            },
            "errors": self.errors,
        }


class WorkerRunner:
    """Orchestrates multiple background workers.

    Provides:
    - Sequential worker execution
    - Aggregated results and logging
    - Clean error handling
    - Configurable worker list

    Usage:
        runner = WorkerRunner()
        result = runner.run_once()
    """

    def __init__(
        self,
        batch_size: int | None = None,
        max_retries: int | None = None,
    ) -> None:
        """Initialize the worker runner.

        Args:
            batch_size: Override default batch size
            max_retries: Override default max retries
        """
        settings = get_settings()
        self.batch_size = batch_size or settings.WORKER_BATCH_SIZE
        self.max_retries = max_retries or settings.WORKER_MAX_RETRIES

        # Initialize workers
        self._workers: list[WorkerBase] = [
            EventWorker(batch_size=self.batch_size, max_retries=self.max_retries),
            NotificationWorker(batch_size=self.batch_size, max_retries=self.max_retries),
            ReminderWorker(batch_size=self.batch_size, max_retries=self.max_retries),
        ]

        self._logger = logging.getLogger(self.__class__.__name__)
        self._shutdown_requested = False

    def run_once(self, session: Session | None = None) -> RunnerResult:
        """Execute one complete processing cycle.

        Runs all workers in sequence and aggregates results.

        Args:
            session: Optional database session (creates new if not provided)

        Returns:
            RunnerResult with aggregated statistics
        """
        result = RunnerResult(started_at=datetime.utcnow())

        self._logger.info(
            "Starting worker run",
            extra={"batch_size": self.batch_size, "max_retries": self.max_retries},
        )

        # Create session if not provided
        own_session = session is None
        if own_session:
            session = Session(engine)

        try:
            for worker in self._workers:
                try:
                    worker_result = worker.run(session)
                    result.worker_results[worker.worker_name] = worker_result
                    result.workers_run += 1
                    result.total_processed += worker_result.processed_count
                    result.total_failed += worker_result.failed_count

                except Exception as e:
                    error_msg = f"{worker.worker_name} failed: {str(e)}"
                    result.errors.append(error_msg)
                    self._logger.error(
                        error_msg,
                        extra={"worker": worker.worker_name},
                        exc_info=True,
                    )

        finally:
            if own_session:
                session.close()

        result.completed_at = datetime.utcnow()

        self._logger.info(
            "Worker run completed",
            extra=result.to_dict(),
        )

        return result

    def run_loop(
        self,
        interval_seconds: int | None = None,
        max_iterations: int | None = None,
    ) -> None:
        """Run workers continuously in a loop.

        Args:
            interval_seconds: Seconds between cycles (default from config)
            max_iterations: Max cycles to run (None for infinite)
        """
        settings = get_settings()
        interval = interval_seconds or settings.WORKER_POLL_INTERVAL_SECONDS
        iterations = 0

        # Setup signal handlers for clean shutdown
        self._setup_signal_handlers()

        self._logger.info(
            "Starting worker loop",
            extra={
                "interval_seconds": interval,
                "max_iterations": max_iterations,
            },
        )

        try:
            while not self._shutdown_requested:
                # Check iteration limit
                if max_iterations is not None and iterations >= max_iterations:
                    self._logger.info(
                        f"Reached max iterations ({max_iterations}), stopping"
                    )
                    break

                # Run workers
                result = self.run_once()
                iterations += 1

                # Log summary
                self._logger.info(
                    f"Iteration {iterations} complete",
                    extra={
                        "processed": result.total_processed,
                        "failed": result.total_failed,
                    },
                )

                # Sleep before next iteration
                if not self._shutdown_requested:
                    self._logger.debug(f"Sleeping for {interval} seconds")
                    time.sleep(interval)

        except KeyboardInterrupt:
            self._logger.info("Keyboard interrupt received, shutting down")

        self._logger.info(
            "Worker loop stopped",
            extra={"total_iterations": iterations},
        )

    def _setup_signal_handlers(self) -> None:
        """Setup signal handlers for graceful shutdown."""
        def handle_signal(signum, frame):
            self._logger.info(f"Received signal {signum}, requesting shutdown")
            self._shutdown_requested = True

        signal.signal(signal.SIGINT, handle_signal)
        signal.signal(signal.SIGTERM, handle_signal)

    def request_shutdown(self) -> None:
        """Request graceful shutdown of the loop."""
        self._shutdown_requested = True


# Convenience functions for easy usage


def run_worker_once(
    batch_size: int | None = None,
    max_retries: int | None = None,
) -> RunnerResult:
    """Run all workers once and return results.

    This is the simplest way to process pending work.

    Args:
        batch_size: Override default batch size
        max_retries: Override default max retries

    Returns:
        RunnerResult with statistics

    Example:
        >>> from app.workers import run_worker_once
        >>> result = run_worker_once()
        >>> print(f"Processed: {result.total_processed}")
    """
    runner = WorkerRunner(batch_size=batch_size, max_retries=max_retries)
    return runner.run_once()


def run_worker_loop(
    interval_seconds: int | None = None,
    max_iterations: int | None = None,
    batch_size: int | None = None,
    max_retries: int | None = None,
) -> None:
    """Run workers continuously in a loop.

    This runs until interrupted (Ctrl+C) or max_iterations reached.

    Args:
        interval_seconds: Seconds between cycles
        max_iterations: Max cycles (None for infinite)
        batch_size: Override default batch size
        max_retries: Override default max retries

    Example:
        >>> from app.workers import run_worker_loop
        >>> run_worker_loop(interval_seconds=10)  # Ctrl+C to stop
    """
    runner = WorkerRunner(batch_size=batch_size, max_retries=max_retries)
    runner.run_loop(
        interval_seconds=interval_seconds,
        max_iterations=max_iterations,
    )


# Configure logging for worker runs
def configure_worker_logging(level: int = logging.INFO) -> None:
    """Configure logging for worker processes.

    Args:
        level: Logging level (default: INFO)
    """
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Set specific loggers
    logging.getLogger("app.workers").setLevel(level)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

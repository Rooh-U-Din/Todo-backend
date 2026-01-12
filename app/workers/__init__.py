"""Background workers module for Phase V Step 4.

This module provides in-process background worker functionality:
- Event processing worker
- Notification delivery worker
- Reminder execution worker
- AI automation executor

Workers can be started via:
- run_worker_once(): Single processing cycle
- run_worker_loop(): Continuous processing with interval
"""

from app.workers.base import (
    WorkerBase,
    WorkerResult,
    WorkerStatus,
)
from app.workers.event_worker import EventWorker
from app.workers.notification_worker import NotificationWorker
from app.workers.reminder_worker import ReminderWorker
from app.workers.ai_executor import AIExecutor
from app.workers.runner import (
    WorkerRunner,
    RunnerResult,
    run_worker_once,
    run_worker_loop,
    configure_worker_logging,
)

__all__ = [
    # Base classes
    "WorkerBase",
    "WorkerResult",
    "WorkerStatus",
    # Workers
    "EventWorker",
    "NotificationWorker",
    "ReminderWorker",
    "AIExecutor",
    # Runner
    "WorkerRunner",
    "RunnerResult",
    "run_worker_once",
    "run_worker_loop",
    "configure_worker_logging",
]

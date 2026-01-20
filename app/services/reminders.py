"""Reminder service for Phase V scheduling logic.

This module provides reminder management functionality:
1. Generate reminder candidates for tasks with due dates
2. Update reminder statuses correctly
3. Prepare reminder context for future scheduler integration

Design Principles:
- No background scheduler needed (logic-only)
- Stateless operations for testability
- No external dependencies (no Dapr runtime required)
- All operations are synchronous and database-backed
"""

import logging
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlmodel import Session, select

from app.models.task import Task, RecurrenceType
from app.models.reminder import TaskReminder, ReminderStatus

# Deferred import to avoid circular dependency
_events_service = None

def _get_events_service():
    """Lazy import of events service to avoid circular imports."""
    global _events_service
    if _events_service is None:
        from app.services import events as events_module
        _events_service = events_module
    return _events_service

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Reminder Candidate Types
# -----------------------------------------------------------------------------


@dataclass
class ReminderCandidate:
    """A potential reminder to be scheduled.

    This represents a reminder that could be created but hasn't been
    persisted yet. Use create_reminder() to persist it.
    """

    task_id: UUID
    user_id: UUID
    remind_at: datetime
    reason: str
    auto_generated: bool = True

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "task_id": str(self.task_id),
            "user_id": str(self.user_id),
            "remind_at": self.remind_at.isoformat(),
            "reason": self.reason,
            "auto_generated": self.auto_generated,
        }


# -----------------------------------------------------------------------------
# Reminder Service
# -----------------------------------------------------------------------------


class ReminderService:
    """Service for managing task reminders.

    Provides logic for:
    1. Generating reminder candidates based on task due dates
    2. Creating, updating, and cancelling reminders
    3. Finding due reminders for processing
    4. Maintaining reminder status consistency

    Thread Safety: All methods are stateless and thread-safe.
    """

    # Default reminder lead times (before due date)
    DEFAULT_LEAD_HOURS = 1  # For tasks due within 24 hours
    DEFAULT_LEAD_DAYS = 1  # For tasks due after 24 hours
    HIGH_PRIORITY_LEAD_HOURS = 2  # Extra reminder for high priority

    def generate_reminder_candidate(
        self,
        task: Task,
        lead_hours: int | None = None,
    ) -> ReminderCandidate | None:
        """Generate a reminder candidate for a task.

        Args:
            task: The task to generate reminder for
            lead_hours: Hours before due date (None for auto-calculation)

        Returns:
            ReminderCandidate or None if no reminder should be created
        """
        if task.is_completed or not task.due_at:
            return None

        now = datetime.utcnow()

        # Don't create reminders for already-overdue tasks
        if task.due_at <= now:
            return None

        # Calculate lead time based on urgency
        hours_until_due = (task.due_at - now).total_seconds() / 3600

        if lead_hours is not None:
            lead = timedelta(hours=lead_hours)
        elif hours_until_due <= 24:
            # Due within 24 hours: remind 1 hour before
            lead = timedelta(hours=self.DEFAULT_LEAD_HOURS)
        else:
            # Due later: remind 1 day before
            lead = timedelta(days=self.DEFAULT_LEAD_DAYS)

        remind_at = task.due_at - lead

        # Don't create reminders in the past
        if remind_at <= now:
            # Instead, set reminder for minimum 15 minutes from now
            remind_at = now + timedelta(minutes=15)

        return ReminderCandidate(
            task_id=task.id,
            user_id=task.user_id,
            remind_at=remind_at,
            reason=f"Due date reminder for task: {task.title}",
        )

    def generate_all_candidates(
        self,
        session: Session,
        user_id: UUID,
    ) -> list[ReminderCandidate]:
        """Generate reminder candidates for all eligible tasks.

        Args:
            session: Database session
            user_id: The user to generate reminders for

        Returns:
            list[ReminderCandidate]: All reminder candidates
        """
        # Get tasks with due dates but no pending reminder
        tasks_with_due = session.exec(
            select(Task)
            .where(Task.user_id == user_id)
            .where(Task.is_completed == False)
            .where(Task.due_at != None)
            .where(Task.due_at > datetime.utcnow())
        ).all()

        candidates = []
        for task in tasks_with_due:
            # Check if task already has a pending reminder
            existing = session.exec(
                select(TaskReminder)
                .where(TaskReminder.task_id == task.id)
                .where(TaskReminder.status == ReminderStatus.PENDING)
            ).first()

            if not existing:
                candidate = self.generate_reminder_candidate(task)
                if candidate:
                    candidates.append(candidate)

        return candidates

    def create_reminder(
        self,
        session: Session,
        task_id: UUID,
        user_id: UUID,
        remind_at: datetime,
    ) -> TaskReminder:
        """Create a new reminder for a task.

        If a pending reminder already exists, it is cancelled first.

        Args:
            session: Database session
            task_id: The task ID
            user_id: The user ID
            remind_at: When to send the reminder

        Returns:
            TaskReminder: The created reminder
        """
        # Cancel any existing pending reminders
        self.cancel_task_reminders(session, task_id, reason="replaced")

        # Create new reminder
        reminder = TaskReminder(
            task_id=task_id,
            user_id=user_id,
            remind_at=remind_at,
            status=ReminderStatus.PENDING,
        )
        session.add(reminder)
        session.flush()

        # Emit reminder.scheduled event
        events = _get_events_service()
        events.emit_reminder_scheduled(
            session=session,
            reminder_id=reminder.id,
            task_id=task_id,
            user_id=user_id,
            remind_at=remind_at,
        )

        logger.info(
            "Reminder created",
            extra={
                "reminder_id": str(reminder.id),
                "task_id": str(task_id),
                "remind_at": remind_at.isoformat(),
            },
        )

        return reminder

    def create_from_candidate(
        self,
        session: Session,
        candidate: ReminderCandidate,
    ) -> TaskReminder:
        """Create a reminder from a candidate.

        Args:
            session: Database session
            candidate: The reminder candidate

        Returns:
            TaskReminder: The created reminder
        """
        return self.create_reminder(
            session=session,
            task_id=candidate.task_id,
            user_id=candidate.user_id,
            remind_at=candidate.remind_at,
        )

    def cancel_task_reminders(
        self,
        session: Session,
        task_id: UUID,
        reason: str = "user_cancelled",
    ) -> int:
        """Cancel all pending reminders for a task.

        Args:
            session: Database session
            task_id: The task ID
            reason: Reason for cancellation (user_cancelled, task_completed, task_deleted, replaced)

        Returns:
            int: Number of reminders cancelled
        """
        pending = session.exec(
            select(TaskReminder)
            .where(TaskReminder.task_id == task_id)
            .where(TaskReminder.status == ReminderStatus.PENDING)
        ).all()

        count = 0
        events = _get_events_service()

        for reminder in pending:
            reminder.status = ReminderStatus.CANCELLED
            session.add(reminder)

            # Emit reminder.cancelled event
            events.emit_reminder_cancelled(
                session=session,
                reminder_id=reminder.id,
                task_id=task_id,
                user_id=reminder.user_id,
                reason=reason,
            )
            count += 1

        if count > 0:
            logger.info(
                "Reminders cancelled",
                extra={"task_id": str(task_id), "count": count, "reason": reason},
            )

        return count

    def mark_reminder_sent(
        self,
        session: Session,
        reminder_id: UUID,
    ) -> TaskReminder | None:
        """Mark a reminder as sent.

        Args:
            session: Database session
            reminder_id: The reminder ID

        Returns:
            TaskReminder or None if not found
        """
        reminder = session.get(TaskReminder, reminder_id)
        if not reminder:
            return None

        if reminder.status != ReminderStatus.PENDING:
            logger.warning(
                "Attempted to mark non-pending reminder as sent",
                extra={
                    "reminder_id": str(reminder_id),
                    "current_status": reminder.status.value,
                },
            )
            return reminder

        reminder.status = ReminderStatus.SENT
        reminder.sent_at = datetime.utcnow()
        session.add(reminder)

        # Emit reminder.sent event
        events = _get_events_service()
        events.emit_reminder_sent(
            session=session,
            reminder_id=reminder.id,
            task_id=reminder.task_id,
            user_id=reminder.user_id,
        )

        logger.info(
            "Reminder marked as sent",
            extra={"reminder_id": str(reminder_id)},
        )

        return reminder

    def mark_reminder_failed(
        self,
        session: Session,
        reminder_id: UUID,
    ) -> TaskReminder | None:
        """Mark a reminder as failed.

        Args:
            session: Database session
            reminder_id: The reminder ID

        Returns:
            TaskReminder or None if not found
        """
        reminder = session.get(TaskReminder, reminder_id)
        if not reminder:
            return None

        reminder.status = ReminderStatus.FAILED
        session.add(reminder)

        logger.warning(
            "Reminder marked as failed",
            extra={"reminder_id": str(reminder_id)},
        )

        return reminder

    def get_due_reminders(
        self,
        session: Session,
        as_of: datetime | None = None,
        limit: int = 100,
    ) -> list[TaskReminder]:
        """Get all reminders that are due for processing.

        Args:
            session: Database session
            as_of: Check reminders due as of this time (default: now)
            limit: Maximum number of reminders to return

        Returns:
            list[TaskReminder]: Reminders that are due
        """
        check_time = as_of or datetime.utcnow()

        return list(
            session.exec(
                select(TaskReminder)
                .where(TaskReminder.status == ReminderStatus.PENDING)
                .where(TaskReminder.remind_at <= check_time)
                .order_by(TaskReminder.remind_at)
                .limit(limit)
            ).all()
        )

    def get_upcoming_reminders(
        self,
        session: Session,
        user_id: UUID,
        within_hours: int = 24,
    ) -> list[TaskReminder]:
        """Get upcoming reminders for a user.

        Args:
            session: Database session
            user_id: The user ID
            within_hours: Look ahead window in hours

        Returns:
            list[TaskReminder]: Upcoming reminders
        """
        now = datetime.utcnow()
        window_end = now + timedelta(hours=within_hours)

        return list(
            session.exec(
                select(TaskReminder)
                .where(TaskReminder.user_id == user_id)
                .where(TaskReminder.status == ReminderStatus.PENDING)
                .where(TaskReminder.remind_at <= window_end)
                .order_by(TaskReminder.remind_at)
            ).all()
        )

    def handle_task_completion(
        self,
        session: Session,
        task_id: UUID,
    ) -> int:
        """Handle reminder cleanup when a task is completed.

        Cancels all pending reminders for the completed task.

        Args:
            session: Database session
            task_id: The completed task ID

        Returns:
            int: Number of reminders cancelled
        """
        return self.cancel_task_reminders(session, task_id, reason="task_completed")

    def handle_task_deletion(
        self,
        session: Session,
        task_id: UUID,
    ) -> int:
        """Handle reminder cleanup when a task is deleted.

        Cancels all pending reminders for the deleted task.

        Args:
            session: Database session
            task_id: The deleted task ID

        Returns:
            int: Number of reminders cancelled
        """
        return self.cancel_task_reminders(session, task_id, reason="task_deleted")

    def update_reminder_for_due_change(
        self,
        session: Session,
        task: Task,
        old_due_at: datetime | None,
    ) -> TaskReminder | None:
        """Update reminder when task due date changes.

        If the due date changed, cancel existing reminders and
        optionally create a new one based on the new due date.

        Args:
            session: Database session
            task: The task with updated due date
            old_due_at: The previous due date

        Returns:
            TaskReminder or None if no reminder created
        """
        # If due date removed, cancel reminders
        if not task.due_at:
            self.cancel_task_reminders(session, task.id)
            return None

        # If due date changed, update reminder
        if old_due_at != task.due_at:
            self.cancel_task_reminders(session, task.id)

            candidate = self.generate_reminder_candidate(task)
            if candidate:
                return self.create_from_candidate(session, candidate)

        return None


# -----------------------------------------------------------------------------
# Dapr Jobs Integration (Phase V T069)
# -----------------------------------------------------------------------------

import httpx
import os

# Dapr configuration
DAPR_HTTP_PORT = int(os.getenv("DAPR_HTTP_PORT", "3500"))
DAPR_JOBS_ENABLED = os.getenv("DAPR_JOBS_ENABLED", "false").lower() == "true"
PUBSUB_NAME = os.getenv("PUBSUB_NAME", "taskpubsub")
REMINDERS_TOPIC = "reminders"


class DaprJobsClient:
    """Client for Dapr Jobs API.

    Dapr Jobs allows scheduling one-time or recurring jobs that trigger
    at specific times. Used for scheduling reminder notifications.
    """

    def __init__(self, dapr_port: int = DAPR_HTTP_PORT):
        self.base_url = f"http://localhost:{dapr_port}"
        self.jobs_url = f"{self.base_url}/v1.0-alpha1/jobs"
        self.enabled = DAPR_JOBS_ENABLED

    async def schedule_reminder_job(
        self,
        reminder_id: UUID,
        task_id: UUID,
        user_id: UUID,
        remind_at: datetime,
    ) -> str | None:
        """Schedule a Dapr job to trigger a reminder at the specified time.

        Args:
            reminder_id: The reminder ID
            task_id: The associated task ID
            user_id: The user ID
            remind_at: When to trigger the reminder

        Returns:
            str: The Dapr job ID, or None if scheduling failed
        """
        if not self.enabled:
            logger.debug("Dapr Jobs disabled, skipping schedule")
            return None

        job_id = f"reminder-{reminder_id}"

        # Calculate schedule time in RFC3339 format
        schedule_time = remind_at.strftime("%Y-%m-%dT%H:%M:%SZ")

        # Job payload
        job_data = {
            "reminder_id": str(reminder_id),
            "task_id": str(task_id),
            "user_id": str(user_id),
            "type": "reminder.due",
        }

        job_spec = {
            "schedule": f"@once({schedule_time})",
            "data": job_data,
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.jobs_url}/{job_id}",
                    json=job_spec,
                    timeout=10.0,
                )
                response.raise_for_status()

                logger.info(
                    "Dapr job scheduled",
                    extra={
                        "job_id": job_id,
                        "reminder_id": str(reminder_id),
                        "schedule_time": schedule_time,
                    },
                )
                return job_id

        except httpx.HTTPError as e:
            logger.error(
                "Failed to schedule Dapr job",
                extra={"error": str(e), "reminder_id": str(reminder_id)},
            )
            return None

    async def cancel_reminder_job(
        self,
        reminder_id: UUID,
    ) -> bool:
        """Cancel a scheduled Dapr job for a reminder.

        Args:
            reminder_id: The reminder ID

        Returns:
            bool: True if cancelled successfully
        """
        if not self.enabled:
            logger.debug("Dapr Jobs disabled, skipping cancel")
            return True

        job_id = f"reminder-{reminder_id}"

        try:
            async with httpx.AsyncClient() as client:
                response = await client.delete(
                    f"{self.jobs_url}/{job_id}",
                    timeout=10.0,
                )
                # 404 is OK - job may have already been triggered
                if response.status_code == 404:
                    logger.debug(
                        "Dapr job not found (may have already triggered)",
                        extra={"job_id": job_id},
                    )
                    return True

                response.raise_for_status()

                logger.info(
                    "Dapr job cancelled",
                    extra={"job_id": job_id, "reminder_id": str(reminder_id)},
                )
                return True

        except httpx.HTTPError as e:
            logger.error(
                "Failed to cancel Dapr job",
                extra={"error": str(e), "reminder_id": str(reminder_id)},
            )
            return False

    async def get_job_status(
        self,
        reminder_id: UUID,
    ) -> dict | None:
        """Get the status of a scheduled Dapr job.

        Args:
            reminder_id: The reminder ID

        Returns:
            dict: Job status or None if not found
        """
        if not self.enabled:
            return None

        job_id = f"reminder-{reminder_id}"

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.jobs_url}/{job_id}",
                    timeout=10.0,
                )
                if response.status_code == 404:
                    return None

                response.raise_for_status()
                return response.json()

        except httpx.HTTPError as e:
            logger.error(
                "Failed to get Dapr job status",
                extra={"error": str(e), "reminder_id": str(reminder_id)},
            )
            return None


# Singleton Dapr Jobs client
_dapr_jobs_client: DaprJobsClient | None = None


def get_dapr_jobs_client() -> DaprJobsClient:
    """Get or create the Dapr Jobs client singleton."""
    global _dapr_jobs_client
    if _dapr_jobs_client is None:
        _dapr_jobs_client = DaprJobsClient()
    return _dapr_jobs_client


# -----------------------------------------------------------------------------
# Singleton Service Instance
# -----------------------------------------------------------------------------

_service_instance: ReminderService | None = None


def get_reminder_service() -> ReminderService:
    """Get or create the reminder service singleton.

    Returns:
        ReminderService: The singleton service instance
    """
    global _service_instance
    if _service_instance is None:
        _service_instance = ReminderService()
    return _service_instance

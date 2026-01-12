"""Reminder execution worker for Phase V Step 4.

Processes TaskReminder records:
1. Evaluates reminders that are due (remind_at <= now)
2. Creates NotificationDelivery records
3. Marks reminders as SENT or EXPIRED
4. Handles recurrence-generated task reminders
"""

import logging
from datetime import datetime
from uuid import UUID

from sqlmodel import Session, select

from app.config import get_settings
from app.models.reminder import TaskReminder, ReminderStatus
from app.models.notification import NotificationDelivery, NotificationChannel, DeliveryStatus
from app.models.task import Task
from app.models.audit_log import AuditLog
from app.workers.base import WorkerBase

logger = logging.getLogger(__name__)


class ReminderWorker(WorkerBase[TaskReminder]):
    """Worker for processing due reminders.

    Evaluates reminders and triggers notification delivery.
    Handles both regular and recurrence-generated reminders.
    """

    @property
    def worker_name(self) -> str:
        return "ReminderWorker"

    def fetch_pending(self, session: Session) -> list[TaskReminder]:
        """Fetch reminders that are due for processing.

        Fetches reminders where:
        - Status is PENDING
        - remind_at is in the past or now

        Args:
            session: Database session

        Returns:
            List of TaskReminder records
        """
        now = datetime.utcnow()

        reminders = session.exec(
            select(TaskReminder)
            .where(TaskReminder.status == ReminderStatus.PENDING)
            .where(TaskReminder.remind_at <= now)
            .order_by(TaskReminder.remind_at)
            .limit(self.batch_size)
        ).all()

        return list(reminders)

    def mark_processing(self, session: Session, item: TaskReminder) -> bool:
        """Mark reminder as being processed.

        Uses status check for idempotency.

        Args:
            session: Database session
            item: The reminder to mark

        Returns:
            True if successfully marked
        """
        # Only process PENDING reminders
        if item.status != ReminderStatus.PENDING:
            return False

        # We don't have a PROCESSING status for reminders,
        # so we'll just check PENDING above
        return True

    def process_item(self, session: Session, item: TaskReminder) -> None:
        """Process a due reminder.

        1. Verify the task still exists and is not completed
        2. Create a NotificationDelivery record
        3. Log the reminder execution

        Args:
            session: Database session
            item: The reminder to process
        """
        # Fetch the associated task
        task = session.get(Task, item.task_id)

        if not task:
            logger.warning(
                f"Reminder {item.id} references non-existent task {item.task_id}",
                extra={"reminder_id": str(item.id), "task_id": str(item.task_id)},
            )
            # Mark as expired since task doesn't exist
            self._mark_expired(session, item, reason="task_not_found")
            return

        if task.is_completed:
            logger.info(
                f"Skipping reminder for completed task {task.id}",
                extra={"reminder_id": str(item.id), "task_id": str(task.id)},
            )
            # Mark as cancelled (task already done)
            self._mark_cancelled(session, item, reason="task_completed")
            return

        # Create notification delivery
        notification = self._create_notification(session, item, task)

        logger.info(
            f"Created notification {notification.id} for reminder {item.id}",
            extra={
                "reminder_id": str(item.id),
                "notification_id": str(notification.id),
                "task_id": str(task.id),
            },
        )

        # Log the reminder execution
        self._log_reminder_execution(session, item, task, notification)

    def _create_notification(
        self,
        session: Session,
        reminder: TaskReminder,
        task: Task,
    ) -> NotificationDelivery:
        """Create a notification delivery for the reminder.

        Args:
            session: Database session
            reminder: The reminder
            task: The associated task

        Returns:
            Created NotificationDelivery
        """
        # Format notification message
        if task.due_at:
            time_str = task.due_at.strftime("%Y-%m-%d %H:%M")
            message = f"Reminder: '{task.title}' is due at {time_str}"
        else:
            message = f"Reminder: Don't forget to complete '{task.title}'"

        notification = NotificationDelivery(
            user_id=reminder.user_id,
            reminder_id=reminder.id,
            channel=NotificationChannel.EMAIL,
            recipient=f"user_{reminder.user_id}@placeholder.local",  # Placeholder
            subject=f"Task Reminder: {task.title[:50]}",
            message=message,
            status=DeliveryStatus.PENDING,
        )
        session.add(notification)
        session.flush()

        return notification

    def _log_reminder_execution(
        self,
        session: Session,
        reminder: TaskReminder,
        task: Task,
        notification: NotificationDelivery,
    ) -> None:
        """Log reminder execution to audit log.

        Args:
            session: Database session
            reminder: The reminder
            task: The associated task
            notification: The created notification
        """
        audit = AuditLog(
            user_id=reminder.user_id,
            action="reminder.triggered",
            entity_type="reminder",
            entity_id=reminder.id,
            details={
                "task_id": str(task.id),
                "task_title": task.title,
                "notification_id": str(notification.id),
                "remind_at": reminder.remind_at.isoformat(),
                "triggered_at": datetime.utcnow().isoformat(),
            },
        )
        session.add(audit)

    def _mark_expired(self, session: Session, reminder: TaskReminder, reason: str) -> None:
        """Mark reminder as expired (task gone).

        Args:
            session: Database session
            reminder: The reminder
            reason: Expiration reason
        """
        reminder.status = ReminderStatus.FAILED
        reminder.sent_at = datetime.utcnow()
        session.add(reminder)

        # Log expiration
        audit = AuditLog(
            user_id=reminder.user_id,
            action="reminder.expired",
            entity_type="reminder",
            entity_id=reminder.id,
            details={"reason": reason},
        )
        session.add(audit)

    def _mark_cancelled(self, session: Session, reminder: TaskReminder, reason: str) -> None:
        """Mark reminder as cancelled (task completed).

        Args:
            session: Database session
            reminder: The reminder
            reason: Cancellation reason
        """
        reminder.status = ReminderStatus.CANCELLED
        reminder.sent_at = datetime.utcnow()
        session.add(reminder)

        # Log cancellation
        audit = AuditLog(
            user_id=reminder.user_id,
            action="reminder.cancelled",
            entity_type="reminder",
            entity_id=reminder.id,
            details={"reason": reason},
        )
        session.add(audit)

    def mark_completed(self, session: Session, item: TaskReminder) -> None:
        """Mark reminder as sent.

        Args:
            session: Database session
            item: The completed reminder
        """
        item.status = ReminderStatus.SENT
        item.sent_at = datetime.utcnow()
        session.add(item)

    def mark_failed(
        self, session: Session, item: TaskReminder, error: str, can_retry: bool
    ) -> None:
        """Mark reminder as failed.

        Note: Reminders don't retry - they either work or fail.

        Args:
            session: Database session
            item: The failed reminder
            error: Error message
            can_retry: Ignored for reminders
        """
        item.status = ReminderStatus.FAILED
        item.sent_at = datetime.utcnow()
        session.add(item)

        # Log failure
        audit = AuditLog(
            user_id=item.user_id,
            action="reminder.failed",
            entity_type="reminder",
            entity_id=item.id,
            details={"error": error[:500] if error else None},
        )
        session.add(audit)

    def get_item_id(self, item: TaskReminder) -> UUID:
        """Get the reminder ID.

        Args:
            item: The reminder

        Returns:
            UUID of the reminder
        """
        return item.id

    def should_retry(self, item: TaskReminder) -> bool:
        """Reminders don't retry.

        Args:
            item: The reminder

        Returns:
            Always False for reminders
        """
        return False

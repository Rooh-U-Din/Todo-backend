"""Notification delivery worker for Phase V Step 4.

Processes NotificationDelivery records:
1. Fetches pending notifications
2. Simulates delivery (no real email/push yet)
3. Updates delivery status
4. Handles retry logic for failures
"""

import logging
from datetime import datetime, timedelta
from uuid import UUID

from sqlmodel import Session, select

from app.config import get_settings
from app.models.notification import NotificationDelivery, DeliveryStatus
from app.models.audit_log import AuditLog
from app.workers.base import WorkerBase

logger = logging.getLogger(__name__)


class NotificationWorker(WorkerBase[NotificationDelivery]):
    """Worker for processing notification deliveries.

    Processes pending notifications and simulates delivery.
    Real email/push integration will be added in Phase V Step 5.
    """

    @property
    def worker_name(self) -> str:
        return "NotificationWorker"

    def fetch_pending(self, session: Session) -> list[NotificationDelivery]:
        """Fetch pending notifications to process.

        Fetches notifications that are:
        - PENDING status
        - Or FAILED but eligible for retry (past next_retry_at)

        Args:
            session: Database session

        Returns:
            List of NotificationDelivery records
        """
        now = datetime.utcnow()

        notifications = session.exec(
            select(NotificationDelivery)
            .where(
                (NotificationDelivery.status == DeliveryStatus.PENDING)
                | (
                    (NotificationDelivery.status == DeliveryStatus.FAILED)
                    & (NotificationDelivery.retry_count < self.max_retries)
                    & (
                        (NotificationDelivery.next_retry_at == None)
                        | (NotificationDelivery.next_retry_at <= now)
                    )
                )
            )
            .order_by(NotificationDelivery.created_at)
            .limit(self.batch_size)
        ).all()

        return list(notifications)

    def mark_processing(self, session: Session, item: NotificationDelivery) -> bool:
        """Mark notification as processing.

        Args:
            session: Database session
            item: The notification to mark

        Returns:
            True if successfully marked
        """
        if item.status == DeliveryStatus.PROCESSING:
            return False

        item.status = DeliveryStatus.PROCESSING
        session.add(item)
        session.flush()
        return True

    def process_item(self, session: Session, item: NotificationDelivery) -> None:
        """Process a notification delivery.

        Currently simulates delivery by logging.
        Real delivery will be implemented in Phase V Step 5.

        Args:
            session: Database session
            item: The notification to process
        """
        logger.info(
            f"[SIMULATED] Delivering notification",
            extra={
                "notification_id": str(item.id),
                "channel": item.channel.value,
                "recipient": item.recipient,
                "subject": item.subject,
            },
        )

        # Simulate delivery (always succeeds for now)
        # In Phase V Step 5, this will integrate with:
        # - Email service (SMTP/Resend/Mailgun)
        # - Web push API

        # Log the simulated delivery
        self._log_delivery(session, item, success=True)

    def _log_delivery(
        self,
        session: Session,
        notification: NotificationDelivery,
        success: bool,
        error: str | None = None,
    ) -> None:
        """Log notification delivery to audit log.

        Args:
            session: Database session
            notification: The notification
            success: Whether delivery succeeded
            error: Error message if failed
        """
        audit = AuditLog(
            user_id=notification.user_id,
            action="notification.delivered" if success else "notification.failed",
            entity_type="notification",
            entity_id=notification.id,
            details={
                "channel": notification.channel.value,
                "recipient": notification.recipient,
                "subject": notification.subject,
                "success": success,
                "error": error,
                "simulated": True,  # Mark as simulated until real delivery
            },
        )
        session.add(audit)

    def mark_completed(self, session: Session, item: NotificationDelivery) -> None:
        """Mark notification as sent.

        Args:
            session: Database session
            item: The completed notification
        """
        item.status = DeliveryStatus.SENT
        item.sent_at = datetime.utcnow()
        item.error_message = None
        session.add(item)

    def mark_failed(
        self, session: Session, item: NotificationDelivery, error: str, can_retry: bool
    ) -> None:
        """Mark notification as failed.

        Args:
            session: Database session
            item: The failed notification
            error: Error message
            can_retry: Whether to schedule for retry
        """
        settings = get_settings()

        item.retry_count += 1
        item.error_message = error[:500] if error else None

        if can_retry:
            item.status = DeliveryStatus.FAILED
            # Schedule next retry with exponential backoff
            backoff = settings.WORKER_RETRY_DELAY_SECONDS * (2 ** item.retry_count)
            item.next_retry_at = datetime.utcnow() + timedelta(seconds=backoff)
        else:
            item.status = DeliveryStatus.FAILED
            item.next_retry_at = None

        # Log the failure
        self._log_delivery(session, item, success=False, error=error)
        session.add(item)

    def get_item_id(self, item: NotificationDelivery) -> UUID:
        """Get the notification ID.

        Args:
            item: The notification

        Returns:
            UUID of the notification
        """
        return item.id

    def should_retry(self, item: NotificationDelivery) -> bool:
        """Check if notification should be retried.

        Args:
            item: The notification to check

        Returns:
            True if should retry
        """
        return item.retry_count < self.max_retries

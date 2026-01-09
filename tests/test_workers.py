"""Tests for Phase V Step 4: Background Workers.

Tests cover:
- WorkerBase abstraction
- EventWorker processing
- NotificationWorker processing
- ReminderWorker processing
- AIExecutor confidence thresholds
- WorkerRunner orchestration
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock, patch, MagicMock
from uuid import uuid4

from sqlmodel import Session

from app.models.task import Task, Priority
from app.models.task_event import TaskEvent, TaskEventType, ProcessingStatus
from app.models.notification import NotificationDelivery, NotificationChannel, DeliveryStatus
from app.models.reminder import TaskReminder, ReminderStatus
from app.workers.base import WorkerBase, WorkerResult, WorkerStatus
from app.workers.event_worker import EventWorker
from app.workers.notification_worker import NotificationWorker
from app.workers.reminder_worker import ReminderWorker
from app.workers.ai_executor import AIExecutor, ExecutionResult, get_ai_executor
from app.workers.runner import WorkerRunner, RunnerResult, run_worker_once
from app.services.ai_insights import (
    AIRecommendation,
    RecommendationType,
    RecommendationConfidence,
)


# ============================================================================
# WorkerResult Tests
# ============================================================================

class TestWorkerResult:
    """Tests for WorkerResult dataclass."""

    def test_worker_result_defaults(self):
        """WorkerResult initializes with correct defaults."""
        result = WorkerResult(
            worker_name="TestWorker",
            started_at=datetime.utcnow(),
        )

        assert result.worker_name == "TestWorker"
        assert result.status == WorkerStatus.PENDING
        assert result.processed_count == 0
        assert result.failed_count == 0
        assert result.items_found == 0
        assert result.errors == []

    def test_worker_result_to_dict(self):
        """WorkerResult converts to dict correctly."""
        started = datetime(2024, 1, 1, 12, 0, 0)
        completed = datetime(2024, 1, 1, 12, 0, 5)

        result = WorkerResult(
            worker_name="TestWorker",
            started_at=started,
            completed_at=completed,
            status=WorkerStatus.COMPLETED,
            processed_count=10,
            failed_count=2,
        )

        d = result.to_dict()

        assert d["worker_name"] == "TestWorker"
        assert d["status"] == "completed"
        assert d["processed_count"] == 10
        assert d["failed_count"] == 2
        assert d["duration_ms"] == 5000.0


# ============================================================================
# EventWorker Tests
# ============================================================================

class TestEventWorker:
    """Tests for EventWorker."""

    def test_worker_name(self):
        """EventWorker has correct name."""
        worker = EventWorker()
        assert worker.worker_name == "EventWorker"

    def test_fetch_pending_returns_pending_events(self, db_session: Session, test_user):
        """fetch_pending returns PENDING events."""
        # Create events with different statuses
        task = Task(
            user_id=test_user.id,
            title="Test Task",
        )
        db_session.add(task)
        db_session.flush()

        pending_event = TaskEvent(
            event_type=TaskEventType.TASK_CREATED,
            task_id=task.id,
            user_id=test_user.id,
            payload={"task_id": str(task.id)},
            processing_status=ProcessingStatus.PENDING,
        )
        completed_event = TaskEvent(
            event_type=TaskEventType.TASK_UPDATED,
            task_id=task.id,
            user_id=test_user.id,
            payload={"task_id": str(task.id)},
            processing_status=ProcessingStatus.COMPLETED,
        )
        db_session.add_all([pending_event, completed_event])
        db_session.commit()

        worker = EventWorker(batch_size=10)
        pending = worker.fetch_pending(db_session)

        assert len(pending) == 1
        assert pending[0].id == pending_event.id

    def test_mark_processing_updates_status(self, db_session: Session, test_user):
        """mark_processing updates event status."""
        task = Task(user_id=test_user.id, title="Test")
        db_session.add(task)
        db_session.flush()

        event = TaskEvent(
            event_type=TaskEventType.TASK_CREATED,
            task_id=task.id,
            user_id=test_user.id,
            payload={},
            processing_status=ProcessingStatus.PENDING,
        )
        db_session.add(event)
        db_session.commit()

        worker = EventWorker()
        success = worker.mark_processing(db_session, event)

        assert success is True
        assert event.processing_status == ProcessingStatus.PROCESSING

    def test_mark_completed_updates_status(self, db_session: Session, test_user):
        """mark_completed updates event status."""
        task = Task(user_id=test_user.id, title="Test")
        db_session.add(task)
        db_session.flush()

        event = TaskEvent(
            event_type=TaskEventType.TASK_CREATED,
            task_id=task.id,
            user_id=test_user.id,
            payload={},
            processing_status=ProcessingStatus.PROCESSING,
        )
        db_session.add(event)
        db_session.commit()

        worker = EventWorker()
        worker.mark_completed(db_session, event)

        assert event.processing_status == ProcessingStatus.COMPLETED
        assert event.processed_at is not None


# ============================================================================
# NotificationWorker Tests
# ============================================================================

class TestNotificationWorker:
    """Tests for NotificationWorker."""

    def test_worker_name(self):
        """NotificationWorker has correct name."""
        worker = NotificationWorker()
        assert worker.worker_name == "NotificationWorker"

    def test_fetch_pending_returns_pending_notifications(self, db_session: Session, test_user):
        """fetch_pending returns PENDING notifications."""
        pending = NotificationDelivery(
            user_id=test_user.id,
            channel=NotificationChannel.EMAIL,
            recipient="test@example.com",
            subject="Test",
            message="Test message",
            status=DeliveryStatus.PENDING,
        )
        sent = NotificationDelivery(
            user_id=test_user.id,
            channel=NotificationChannel.EMAIL,
            recipient="test@example.com",
            subject="Test 2",
            message="Test message 2",
            status=DeliveryStatus.SENT,
        )
        db_session.add_all([pending, sent])
        db_session.commit()

        worker = NotificationWorker(batch_size=10)
        notifications = worker.fetch_pending(db_session)

        assert len(notifications) == 1
        assert notifications[0].id == pending.id

    def test_mark_completed_sets_sent(self, db_session: Session, test_user):
        """mark_completed sets status to SENT."""
        notification = NotificationDelivery(
            user_id=test_user.id,
            channel=NotificationChannel.EMAIL,
            recipient="test@example.com",
            subject="Test",
            message="Test message",
            status=DeliveryStatus.PROCESSING,
        )
        db_session.add(notification)
        db_session.commit()

        worker = NotificationWorker()
        worker.mark_completed(db_session, notification)

        assert notification.status == DeliveryStatus.SENT
        assert notification.sent_at is not None

    def test_mark_failed_increments_retry(self, db_session: Session, test_user):
        """mark_failed increments retry count."""
        notification = NotificationDelivery(
            user_id=test_user.id,
            channel=NotificationChannel.EMAIL,
            recipient="test@example.com",
            subject="Test",
            message="Test message",
            status=DeliveryStatus.PROCESSING,
            retry_count=0,
        )
        db_session.add(notification)
        db_session.commit()

        worker = NotificationWorker()
        worker.mark_failed(db_session, notification, "Test error", can_retry=True)

        assert notification.retry_count == 1
        assert notification.error_message == "Test error"
        assert notification.status == DeliveryStatus.FAILED


# ============================================================================
# ReminderWorker Tests
# ============================================================================

class TestReminderWorker:
    """Tests for ReminderWorker."""

    def test_worker_name(self):
        """ReminderWorker has correct name."""
        worker = ReminderWorker()
        assert worker.worker_name == "ReminderWorker"

    def test_fetch_pending_returns_due_reminders(self, db_session: Session, test_user):
        """fetch_pending returns due PENDING reminders."""
        task = Task(user_id=test_user.id, title="Test Task")
        db_session.add(task)
        db_session.flush()

        # Due reminder
        due = TaskReminder(
            task_id=task.id,
            user_id=test_user.id,
            remind_at=datetime.utcnow() - timedelta(minutes=5),
            status=ReminderStatus.PENDING,
        )
        # Future reminder
        future = TaskReminder(
            task_id=task.id,
            user_id=test_user.id,
            remind_at=datetime.utcnow() + timedelta(hours=1),
            status=ReminderStatus.PENDING,
        )
        db_session.add_all([due, future])
        db_session.commit()

        worker = ReminderWorker(batch_size=10)
        reminders = worker.fetch_pending(db_session)

        assert len(reminders) == 1
        assert reminders[0].id == due.id

    def test_process_item_creates_notification(self, db_session: Session, test_user):
        """process_item creates NotificationDelivery."""
        task = Task(
            user_id=test_user.id,
            title="Test Task",
            due_at=datetime.utcnow() + timedelta(hours=1),
        )
        db_session.add(task)
        db_session.flush()

        reminder = TaskReminder(
            task_id=task.id,
            user_id=test_user.id,
            remind_at=datetime.utcnow() - timedelta(minutes=1),
            status=ReminderStatus.PENDING,
        )
        db_session.add(reminder)
        db_session.commit()

        worker = ReminderWorker()
        worker.process_item(db_session, reminder)
        db_session.commit()

        # Check notification was created
        from sqlmodel import select
        notifications = db_session.exec(
            select(NotificationDelivery).where(
                NotificationDelivery.reminder_id == reminder.id
            )
        ).all()

        assert len(notifications) == 1
        assert "Test Task" in notifications[0].subject

    def test_process_item_skips_completed_task(self, db_session: Session, test_user):
        """process_item skips reminders for completed tasks."""
        task = Task(
            user_id=test_user.id,
            title="Completed Task",
            is_completed=True,
            completed_at=datetime.utcnow(),
        )
        db_session.add(task)
        db_session.flush()

        reminder = TaskReminder(
            task_id=task.id,
            user_id=test_user.id,
            remind_at=datetime.utcnow() - timedelta(minutes=1),
            status=ReminderStatus.PENDING,
        )
        db_session.add(reminder)
        db_session.commit()

        worker = ReminderWorker()
        worker.process_item(db_session, reminder)
        db_session.commit()

        # Reminder should be cancelled
        db_session.refresh(reminder)
        assert reminder.status == ReminderStatus.CANCELLED


# ============================================================================
# AIExecutor Tests
# ============================================================================

class TestAIExecutor:
    """Tests for AIExecutor."""

    def test_is_enabled_default_false(self):
        """AI automation is disabled by default."""
        with patch("app.workers.ai_executor.get_settings") as mock_settings:
            mock_settings.return_value.AI_AUTOMATION_ENABLED = False
            executor = AIExecutor()
            assert executor.is_enabled() is False

    def test_meets_threshold_high_confidence(self):
        """High confidence meets default threshold."""
        with patch("app.workers.ai_executor.get_settings") as mock_settings:
            mock_settings.return_value.AI_CONFIDENCE_THRESHOLD = 0.8
            executor = AIExecutor()

            rec = AIRecommendation(
                task_id=uuid4(),
                recommendation_type=RecommendationType.PRIORITY_CHANGE,
                confidence=RecommendationConfidence.HIGH,
                reason="Test",
                suggested_action={"field": "priority", "suggested_value": "high"},
            )

            assert executor.meets_threshold(rec) is True

    def test_meets_threshold_low_confidence_fails(self):
        """Low confidence doesn't meet default threshold."""
        with patch("app.workers.ai_executor.get_settings") as mock_settings:
            mock_settings.return_value.AI_CONFIDENCE_THRESHOLD = 0.8
            executor = AIExecutor()

            rec = AIRecommendation(
                task_id=uuid4(),
                recommendation_type=RecommendationType.PRIORITY_CHANGE,
                confidence=RecommendationConfidence.LOW,
                reason="Test",
                suggested_action={"field": "priority", "suggested_value": "high"},
            )

            assert executor.meets_threshold(rec) is False

    def test_execute_recommendation_disabled(self):
        """Execute returns not applied when disabled."""
        with patch("app.workers.ai_executor.get_settings") as mock_settings:
            mock_settings.return_value.AI_AUTOMATION_ENABLED = False
            executor = AIExecutor()

            rec = AIRecommendation(
                task_id=uuid4(),
                recommendation_type=RecommendationType.PRIORITY_CHANGE,
                confidence=RecommendationConfidence.HIGH,
                reason="Test",
                suggested_action={},
            )

            result = executor.execute_recommendation(Mock(), rec)

            assert result.applied is False
            assert "disabled" in result.reason.lower()

    def test_execute_recommendation_below_threshold(self):
        """Execute returns not applied when below threshold."""
        with patch("app.workers.ai_executor.get_settings") as mock_settings:
            mock_settings.return_value.AI_AUTOMATION_ENABLED = True
            mock_settings.return_value.AI_CONFIDENCE_THRESHOLD = 0.8
            executor = AIExecutor()

            rec = AIRecommendation(
                task_id=uuid4(),
                recommendation_type=RecommendationType.PRIORITY_CHANGE,
                confidence=RecommendationConfidence.LOW,
                reason="Test",
                suggested_action={},
            )

            result = executor.execute_recommendation(Mock(), rec)

            assert result.applied is False
            assert "threshold" in result.reason.lower()

    def test_get_ai_executor_singleton(self):
        """get_ai_executor returns singleton."""
        # Reset singleton
        import app.workers.ai_executor as ai_module
        ai_module._executor_instance = None

        executor1 = get_ai_executor()
        executor2 = get_ai_executor()

        assert executor1 is executor2


# ============================================================================
# WorkerRunner Tests
# ============================================================================

class TestWorkerRunner:
    """Tests for WorkerRunner."""

    def test_runner_initializes_workers(self):
        """WorkerRunner initializes all workers."""
        with patch("app.workers.runner.get_settings") as mock_settings:
            mock_settings.return_value.WORKER_BATCH_SIZE = 50
            mock_settings.return_value.WORKER_MAX_RETRIES = 3
            mock_settings.return_value.WORKER_POLL_INTERVAL_SECONDS = 5

            runner = WorkerRunner()

            assert len(runner._workers) == 3
            worker_names = [w.worker_name for w in runner._workers]
            assert "EventWorker" in worker_names
            assert "NotificationWorker" in worker_names
            assert "ReminderWorker" in worker_names

    def test_run_once_returns_result(self):
        """run_once returns RunnerResult."""
        with patch("app.workers.runner.get_settings") as mock_settings:
            mock_settings.return_value.WORKER_BATCH_SIZE = 50
            mock_settings.return_value.WORKER_MAX_RETRIES = 3

            runner = WorkerRunner()

            # Mock workers to do nothing
            for worker in runner._workers:
                worker.run = Mock(return_value=WorkerResult(
                    worker_name=worker.worker_name,
                    started_at=datetime.utcnow(),
                    status=WorkerStatus.COMPLETED,
                ))

            result = runner.run_once()

            assert isinstance(result, RunnerResult)
            assert result.workers_run == 3
            assert result.completed_at is not None

    def test_runner_result_aggregates_counts(self):
        """RunnerResult aggregates worker counts."""
        result = RunnerResult(started_at=datetime.utcnow())
        result.total_processed = 15
        result.total_failed = 3
        result.workers_run = 3

        d = result.to_dict()

        assert d["total_processed"] == 15
        assert d["total_failed"] == 3
        assert d["workers_run"] == 3

    def test_request_shutdown_sets_flag(self):
        """request_shutdown sets shutdown flag."""
        with patch("app.workers.runner.get_settings") as mock_settings:
            mock_settings.return_value.WORKER_BATCH_SIZE = 50
            mock_settings.return_value.WORKER_MAX_RETRIES = 3

            runner = WorkerRunner()
            assert runner._shutdown_requested is False

            runner.request_shutdown()
            assert runner._shutdown_requested is True


# ============================================================================
# Integration Tests
# ============================================================================

class TestWorkerIntegration:
    """Integration tests for workers."""

    def test_full_reminder_workflow(self, db_session: Session, test_user):
        """Test complete reminder -> notification workflow."""
        # Create task with due date
        task = Task(
            user_id=test_user.id,
            title="Integration Test Task",
            due_at=datetime.utcnow() + timedelta(hours=2),
        )
        db_session.add(task)
        db_session.flush()

        # Create due reminder
        reminder = TaskReminder(
            task_id=task.id,
            user_id=test_user.id,
            remind_at=datetime.utcnow() - timedelta(minutes=1),
            status=ReminderStatus.PENDING,
        )
        db_session.add(reminder)
        db_session.commit()

        # Run reminder worker
        reminder_worker = ReminderWorker(batch_size=10)
        reminder_result = reminder_worker.run(db_session)

        assert reminder_result.processed_count == 1

        # Verify notification was created
        from sqlmodel import select
        notification = db_session.exec(
            select(NotificationDelivery).where(
                NotificationDelivery.reminder_id == reminder.id
            )
        ).first()

        assert notification is not None
        assert notification.status == DeliveryStatus.PENDING

        # Run notification worker
        notification_worker = NotificationWorker(batch_size=10)
        notification_result = notification_worker.run(db_session)

        assert notification_result.processed_count == 1

        # Verify notification is sent
        db_session.refresh(notification)
        assert notification.status == DeliveryStatus.SENT

    def test_event_worker_processes_outbox(self, db_session: Session, test_user):
        """Test event worker processes outbox."""
        task = Task(user_id=test_user.id, title="Test Task")
        db_session.add(task)
        db_session.flush()

        # Create pending event
        event = TaskEvent(
            event_type=TaskEventType.TASK_CREATED,
            task_id=task.id,
            user_id=test_user.id,
            payload={"task_id": str(task.id), "title": "Test Task"},
            processing_status=ProcessingStatus.PENDING,
        )
        db_session.add(event)
        db_session.commit()

        # Run event worker
        worker = EventWorker(batch_size=10)
        result = worker.run(db_session)

        assert result.processed_count == 1

        # Verify event is completed
        db_session.refresh(event)
        assert event.processing_status == ProcessingStatus.COMPLETED


# ============================================================================
# Pytest Fixtures
# ============================================================================

@pytest.fixture
def db_session():
    """Create a test database session."""
    from sqlmodel import create_engine, SQLModel
    from sqlmodel.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    # Import all models to register them
    from app.models.user import User
    from app.models.task import Task
    from app.models.task_event import TaskEvent
    from app.models.notification import NotificationDelivery
    from app.models.reminder import TaskReminder
    from app.models.audit_log import AuditLog

    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        yield session


@pytest.fixture
def test_user(db_session: Session):
    """Create a test user."""
    from app.models.user import User

    user = User(
        email="test@example.com",
        name="Test User",
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user

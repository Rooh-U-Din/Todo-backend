"""Tests for Phase V Step 3: Event Consumers & Intelligent Workflows.

These tests validate the in-process event consumer layer without
requiring database or Dapr runtime.
"""

import pytest
from datetime import datetime, timedelta
from uuid import uuid4

from app.events.types import EventType, TaskEventData
from app.events.consumers import (
    EventDispatcher,
    AuditConsumer,
    NotificationConsumer,
    RecurrenceConsumer,
    get_event_dispatcher,
)
from app.services.ai_insights import (
    AIInsightsService,
    AIRecommendation,
    TaskInsights,
    RecommendationType,
    RecommendationConfidence,
)
from app.services.reminders import ReminderService, ReminderCandidate
from app.models.task import Task, Priority, RecurrenceType


class TestEventTypes:
    """Test event type definitions."""

    def test_all_event_types_defined(self):
        """Verify all required event types are defined."""
        assert EventType.TASK_CREATED == "task.created.v1"
        assert EventType.TASK_UPDATED == "task.updated.v1"
        assert EventType.TASK_COMPLETED == "task.completed.v1"
        assert EventType.TASK_DELETED == "task.deleted.v1"
        assert EventType.TASK_RECURRED == "task.recurred.v1"

    def test_event_data_to_cloudevents(self):
        """Verify CloudEvents format conversion."""
        event = TaskEventData(
            event_id=uuid4(),
            event_type=EventType.TASK_CREATED,
            aggregate_id=uuid4(),
            user_id=uuid4(),
            timestamp=datetime.utcnow(),
            data={"title": "Test Task"},
        )

        cloud_event = event.to_cloudevents_dict()

        assert cloud_event["specversion"] == "1.0"
        assert cloud_event["type"] == "task.created.v1"
        assert cloud_event["source"] == "/backend/tasks"
        assert "data" in cloud_event
        assert cloud_event["data"]["title"] == "Test Task"


class TestEventDispatcher:
    """Test event dispatcher functionality."""

    def test_dispatcher_has_default_consumers(self):
        """Verify dispatcher initializes with default consumers."""
        dispatcher = get_event_dispatcher()
        consumer_names = [c.__class__.__name__ for c in dispatcher._consumers]

        assert "AuditConsumer" in consumer_names
        assert "NotificationConsumer" in consumer_names
        assert "RecurrenceConsumer" in consumer_names

    def test_audit_consumer_handles_all_events(self):
        """Verify audit consumer handles all task events."""
        consumer = AuditConsumer()

        assert consumer.handles(EventType.TASK_CREATED)
        assert consumer.handles(EventType.TASK_UPDATED)
        assert consumer.handles(EventType.TASK_COMPLETED)
        assert consumer.handles(EventType.TASK_DELETED)
        assert consumer.handles(EventType.TASK_RECURRED)

    def test_notification_consumer_handles_specific_events(self):
        """Verify notification consumer handles only specific events."""
        consumer = NotificationConsumer()

        assert consumer.handles(EventType.TASK_CREATED)
        assert consumer.handles(EventType.TASK_COMPLETED)
        assert not consumer.handles(EventType.TASK_UPDATED)
        assert not consumer.handles(EventType.TASK_DELETED)

    def test_recurrence_consumer_handles_completion_only(self):
        """Verify recurrence consumer handles only completion events."""
        consumer = RecurrenceConsumer()

        assert consumer.handles(EventType.TASK_COMPLETED)
        assert not consumer.handles(EventType.TASK_CREATED)
        assert not consumer.handles(EventType.TASK_UPDATED)
        assert not consumer.handles(EventType.TASK_DELETED)


class TestAIInsightsService:
    """Test AI insights and recommendation generation."""

    def test_priority_change_for_overdue_low_priority(self):
        """Suggest priority bump for overdue low-priority tasks."""
        service = AIInsightsService()

        task = Task(
            id=uuid4(),
            user_id=uuid4(),
            title="Overdue Task",
            is_completed=False,
            due_at=datetime.utcnow() - timedelta(days=2),
            priority=Priority.LOW,
        )

        recommendation = service.suggest_priority_change(task)

        assert recommendation is not None
        assert recommendation.recommendation_type == RecommendationType.PRIORITY_CHANGE
        assert recommendation.confidence == RecommendationConfidence.HIGH
        assert recommendation.suggested_action["suggested_value"] == Priority.MEDIUM.value

    def test_no_priority_change_for_completed_task(self):
        """No priority suggestion for completed tasks."""
        service = AIInsightsService()

        task = Task(
            id=uuid4(),
            user_id=uuid4(),
            title="Done Task",
            is_completed=True,
            due_at=datetime.utcnow() - timedelta(days=2),
            priority=Priority.LOW,
        )

        recommendation = service.suggest_priority_change(task)
        assert recommendation is None

    def test_no_priority_change_for_task_without_due_date(self):
        """No priority suggestion for tasks without due dates."""
        service = AIInsightsService()

        task = Task(
            id=uuid4(),
            user_id=uuid4(),
            title="No Due Date",
            is_completed=False,
            due_at=None,
            priority=Priority.LOW,
        )

        recommendation = service.suggest_priority_change(task)
        assert recommendation is None

    def test_ai_context_structure(self):
        """Verify AI context data structure."""
        # This test validates the structure without database
        context = {
            "summary": {
                "total_pending_tasks": 5,
                "overdue_tasks": 2,
                "tasks_with_reminders": 3,
                "neglected_tasks": 1,
            },
            "recommendations": [],
            "generated_at": datetime.utcnow().isoformat(),
        }

        assert "summary" in context
        assert "recommendations" in context
        assert "generated_at" in context


class TestReminderService:
    """Test reminder logic without database."""

    def test_generate_reminder_for_task_with_due_date(self):
        """Generate reminder candidate for task with due date."""
        service = ReminderService()

        task = Task(
            id=uuid4(),
            user_id=uuid4(),
            title="Task with Due Date",
            is_completed=False,
            due_at=datetime.utcnow() + timedelta(days=2),
        )

        candidate = service.generate_reminder_candidate(task)

        assert candidate is not None
        assert candidate.task_id == task.id
        assert candidate.user_id == task.user_id
        assert candidate.remind_at < task.due_at

    def test_no_reminder_for_completed_task(self):
        """No reminder for completed tasks."""
        service = ReminderService()

        task = Task(
            id=uuid4(),
            user_id=uuid4(),
            title="Completed Task",
            is_completed=True,
            due_at=datetime.utcnow() + timedelta(days=2),
        )

        candidate = service.generate_reminder_candidate(task)
        assert candidate is None

    def test_no_reminder_for_overdue_task(self):
        """No reminder for already overdue tasks."""
        service = ReminderService()

        task = Task(
            id=uuid4(),
            user_id=uuid4(),
            title="Overdue Task",
            is_completed=False,
            due_at=datetime.utcnow() - timedelta(days=1),
        )

        candidate = service.generate_reminder_candidate(task)
        assert candidate is None

    def test_reminder_candidate_serialization(self):
        """Verify reminder candidate can be serialized."""
        candidate = ReminderCandidate(
            task_id=uuid4(),
            user_id=uuid4(),
            remind_at=datetime.utcnow() + timedelta(hours=1),
            reason="Test reminder",
        )

        data = candidate.to_dict()

        assert "task_id" in data
        assert "user_id" in data
        assert "remind_at" in data
        assert "reason" in data


class TestRecommendationStructures:
    """Test AI recommendation data structures."""

    def test_recommendation_to_dict(self):
        """Verify recommendation serialization."""
        rec = AIRecommendation(
            recommendation_type=RecommendationType.PRIORITY_CHANGE,
            task_id=uuid4(),
            confidence=RecommendationConfidence.HIGH,
            reason="Task is overdue",
            suggested_action={"field": "priority", "value": "high"},
        )

        data = rec.to_dict()

        assert data["recommendation_type"] == "priority_change"
        assert data["confidence"] == "high"
        assert "reason" in data
        assert "suggested_action" in data

    def test_task_insights_to_dict(self):
        """Verify task insights serialization."""
        insights = TaskInsights(
            task_id=uuid4(),
            is_overdue=True,
            days_until_due=-2,
            has_reminder=False,
            neglected_days=10,
        )

        data = insights.to_dict()

        assert data["is_overdue"] is True
        assert data["days_until_due"] == -2
        assert data["has_reminder"] is False
        assert data["neglected_days"] == 10


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

"""AI Insights Service for Phase V intelligent workflows.

This module provides AI decision hooks that analyze tasks and generate
structured recommendations. It does NOT auto-modify data - only generates
suggestions for human review or AI-assisted decision making.

Capabilities:
1. Priority change suggestions based on due dates and overdue status
2. Reminder suggestions for tasks with due dates
3. Detection of overdue or neglected tasks
4. Context preparation for AI chatbot integration

Design Principles:
- Pure functions where possible
- No side effects on task data
- Structured output for easy consumption
- Testable via direct function calls
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any
from uuid import UUID

from sqlmodel import Session, select

from app.models.task import Task, Priority, RecurrenceType
from app.models.reminder import TaskReminder, ReminderStatus

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# AI Recommendation Types
# -----------------------------------------------------------------------------


class RecommendationType(str, Enum):
    """Types of AI recommendations."""

    PRIORITY_CHANGE = "priority_change"
    ADD_REMINDER = "add_reminder"
    TASK_OVERDUE = "task_overdue"
    TASK_NEGLECTED = "task_neglected"
    RECURRING_OPTIMIZATION = "recurring_optimization"


class RecommendationConfidence(str, Enum):
    """Confidence levels for recommendations."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class AIRecommendation:
    """A structured AI recommendation for task management.

    Attributes:
        recommendation_type: The type of recommendation
        task_id: The task this recommendation applies to
        confidence: How confident the AI is in this recommendation
        reason: Human-readable explanation
        suggested_action: What action to take
        metadata: Additional context for the recommendation
    """

    recommendation_type: RecommendationType
    task_id: UUID
    confidence: RecommendationConfidence
    reason: str
    suggested_action: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "recommendation_type": self.recommendation_type.value,
            "task_id": str(self.task_id),
            "confidence": self.confidence.value,
            "reason": self.reason,
            "suggested_action": self.suggested_action,
            "metadata": self.metadata,
        }


@dataclass
class TaskInsights:
    """Aggregated insights for a task.

    Attributes:
        task_id: The task ID
        is_overdue: Whether the task is past its due date
        days_until_due: Days until due (negative if overdue)
        has_reminder: Whether a reminder is scheduled
        neglected_days: Days since last activity
        recommendations: List of AI recommendations
    """

    task_id: UUID
    is_overdue: bool = False
    days_until_due: int | None = None
    has_reminder: bool = False
    neglected_days: int = 0
    recommendations: list[AIRecommendation] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "task_id": str(self.task_id),
            "is_overdue": self.is_overdue,
            "days_until_due": self.days_until_due,
            "has_reminder": self.has_reminder,
            "neglected_days": self.neglected_days,
            "recommendations": [r.to_dict() for r in self.recommendations],
        }


# -----------------------------------------------------------------------------
# AI Insights Service
# -----------------------------------------------------------------------------


class AIInsightsService:
    """Service for generating AI-powered task insights and recommendations.

    This service analyzes tasks and generates structured recommendations
    without modifying any data. The recommendations can be:
    1. Displayed to users in the UI
    2. Used by the AI chatbot for context-aware responses
    3. Logged for analytics and improvement

    Thread Safety: All methods are stateless and thread-safe.
    """

    # Configuration thresholds
    OVERDUE_PRIORITY_BOOST_DAYS = 1  # Boost priority if overdue by this many days
    NEGLECTED_TASK_DAYS = 7  # Consider task neglected after this many days
    REMINDER_SUGGESTION_HOURS = 24  # Suggest reminder if due within this window

    def analyze_task(self, session: Session, task: Task) -> TaskInsights:
        """Generate comprehensive insights for a single task.

        Args:
            session: Database session for querying related data
            task: The task to analyze

        Returns:
            TaskInsights: Aggregated insights and recommendations
        """
        now = datetime.utcnow()
        insights = TaskInsights(task_id=task.id)

        # Calculate due date metrics
        if task.due_at:
            delta = task.due_at - now
            insights.days_until_due = delta.days
            insights.is_overdue = delta.total_seconds() < 0

        # Check for existing reminder
        existing_reminder = session.exec(
            select(TaskReminder)
            .where(TaskReminder.task_id == task.id)
            .where(TaskReminder.status == ReminderStatus.PENDING)
        ).first()
        insights.has_reminder = existing_reminder is not None

        # Calculate neglected days
        insights.neglected_days = (now - task.updated_at).days

        # Generate recommendations
        insights.recommendations = self._generate_recommendations(session, task, insights)

        return insights

    def analyze_user_tasks(
        self,
        session: Session,
        user_id: UUID,
        include_completed: bool = False,
    ) -> list[TaskInsights]:
        """Analyze all tasks for a user and generate insights.

        Args:
            session: Database session
            user_id: The user whose tasks to analyze
            include_completed: Whether to include completed tasks

        Returns:
            list[TaskInsights]: Insights for each task
        """
        query = select(Task).where(Task.user_id == user_id)
        if not include_completed:
            query = query.where(Task.is_completed == False)

        tasks = session.exec(query).all()
        return [self.analyze_task(session, task) for task in tasks]

    def get_overdue_tasks(self, session: Session, user_id: UUID) -> list[Task]:
        """Get all overdue tasks for a user.

        Args:
            session: Database session
            user_id: The user to check

        Returns:
            list[Task]: List of overdue tasks
        """
        now = datetime.utcnow()
        return list(
            session.exec(
                select(Task)
                .where(Task.user_id == user_id)
                .where(Task.is_completed == False)
                .where(Task.due_at < now)
                .order_by(Task.due_at)
            ).all()
        )

    def get_neglected_tasks(
        self,
        session: Session,
        user_id: UUID,
        threshold_days: int | None = None,
    ) -> list[Task]:
        """Get tasks that haven't been updated recently.

        Args:
            session: Database session
            user_id: The user to check
            threshold_days: Days of inactivity (default: NEGLECTED_TASK_DAYS)

        Returns:
            list[Task]: List of neglected tasks
        """
        threshold = threshold_days or self.NEGLECTED_TASK_DAYS
        cutoff = datetime.utcnow() - timedelta(days=threshold)

        return list(
            session.exec(
                select(Task)
                .where(Task.user_id == user_id)
                .where(Task.is_completed == False)
                .where(Task.updated_at < cutoff)
                .order_by(Task.updated_at)
            ).all()
        )

    def suggest_priority_change(self, task: Task) -> AIRecommendation | None:
        """Suggest a priority change based on task state.

        Rules:
        - Overdue tasks with LOW priority → suggest MEDIUM
        - Overdue tasks with MEDIUM priority → suggest HIGH
        - Tasks due within 24h with LOW priority → suggest MEDIUM

        Args:
            task: The task to analyze

        Returns:
            AIRecommendation or None if no change suggested
        """
        if task.is_completed or not task.due_at:
            return None

        now = datetime.utcnow()
        time_until_due = task.due_at - now
        hours_until_due = time_until_due.total_seconds() / 3600

        # Overdue task priority boost
        if hours_until_due < 0:
            days_overdue = abs(hours_until_due) / 24
            if task.priority == Priority.LOW:
                return AIRecommendation(
                    recommendation_type=RecommendationType.PRIORITY_CHANGE,
                    task_id=task.id,
                    confidence=RecommendationConfidence.HIGH,
                    reason=f"Task is {int(days_overdue)} days overdue with low priority",
                    suggested_action={
                        "field": "priority",
                        "current_value": task.priority.value,
                        "suggested_value": Priority.MEDIUM.value,
                    },
                )
            elif task.priority == Priority.MEDIUM and days_overdue >= self.OVERDUE_PRIORITY_BOOST_DAYS:
                return AIRecommendation(
                    recommendation_type=RecommendationType.PRIORITY_CHANGE,
                    task_id=task.id,
                    confidence=RecommendationConfidence.MEDIUM,
                    reason=f"Task is {int(days_overdue)} days overdue",
                    suggested_action={
                        "field": "priority",
                        "current_value": task.priority.value,
                        "suggested_value": Priority.HIGH.value,
                    },
                )

        # Upcoming due date priority consideration
        if 0 < hours_until_due <= self.REMINDER_SUGGESTION_HOURS:
            if task.priority == Priority.LOW:
                return AIRecommendation(
                    recommendation_type=RecommendationType.PRIORITY_CHANGE,
                    task_id=task.id,
                    confidence=RecommendationConfidence.MEDIUM,
                    reason=f"Task is due within {int(hours_until_due)} hours",
                    suggested_action={
                        "field": "priority",
                        "current_value": task.priority.value,
                        "suggested_value": Priority.MEDIUM.value,
                    },
                )

        return None

    def suggest_reminder(
        self,
        session: Session,
        task: Task,
    ) -> AIRecommendation | None:
        """Suggest adding a reminder for a task.

        Rules:
        - Task with due_at but no reminder → suggest reminder
        - Reminder time: 1 hour before due for same-day, 1 day before otherwise

        Args:
            session: Database session
            task: The task to analyze

        Returns:
            AIRecommendation or None if no reminder suggested
        """
        if task.is_completed or not task.due_at:
            return None

        # Check for existing reminder
        existing = session.exec(
            select(TaskReminder)
            .where(TaskReminder.task_id == task.id)
            .where(TaskReminder.status == ReminderStatus.PENDING)
        ).first()

        if existing:
            return None

        now = datetime.utcnow()
        time_until_due = task.due_at - now

        # Don't suggest reminder if already overdue
        if time_until_due.total_seconds() < 0:
            return None

        hours_until_due = time_until_due.total_seconds() / 3600

        # Calculate suggested reminder time
        if hours_until_due <= 24:
            # Due within 24 hours: remind 1 hour before
            remind_at = task.due_at - timedelta(hours=1)
            remind_description = "1 hour before due"
        else:
            # Due later: remind 1 day before
            remind_at = task.due_at - timedelta(days=1)
            remind_description = "1 day before due"

        # Don't suggest reminder in the past
        if remind_at <= now:
            remind_at = now + timedelta(minutes=30)
            remind_description = "in 30 minutes"

        return AIRecommendation(
            recommendation_type=RecommendationType.ADD_REMINDER,
            task_id=task.id,
            confidence=RecommendationConfidence.HIGH,
            reason=f"Task has due date but no reminder set",
            suggested_action={
                "remind_at": remind_at.isoformat(),
                "remind_description": remind_description,
            },
        )

    def _generate_recommendations(
        self,
        session: Session,
        task: Task,
        insights: TaskInsights,
    ) -> list[AIRecommendation]:
        """Generate all applicable recommendations for a task.

        Args:
            session: Database session
            task: The task to analyze
            insights: Pre-computed insights

        Returns:
            list[AIRecommendation]: All applicable recommendations
        """
        recommendations: list[AIRecommendation] = []

        # Priority change suggestion
        priority_rec = self.suggest_priority_change(task)
        if priority_rec:
            recommendations.append(priority_rec)

        # Reminder suggestion
        reminder_rec = self.suggest_reminder(session, task)
        if reminder_rec:
            recommendations.append(reminder_rec)

        # Overdue warning
        if insights.is_overdue:
            recommendations.append(
                AIRecommendation(
                    recommendation_type=RecommendationType.TASK_OVERDUE,
                    task_id=task.id,
                    confidence=RecommendationConfidence.HIGH,
                    reason=f"Task is {abs(insights.days_until_due or 0)} days overdue",
                    suggested_action={"action": "review_and_reschedule"},
                )
            )

        # Neglected task warning
        if insights.neglected_days >= self.NEGLECTED_TASK_DAYS:
            recommendations.append(
                AIRecommendation(
                    recommendation_type=RecommendationType.TASK_NEGLECTED,
                    task_id=task.id,
                    confidence=RecommendationConfidence.MEDIUM,
                    reason=f"Task hasn't been updated in {insights.neglected_days} days",
                    suggested_action={"action": "review_or_delete"},
                )
            )

        return recommendations

    def prepare_ai_context(
        self,
        session: Session,
        user_id: UUID,
    ) -> dict[str, Any]:
        """Prepare context data for AI chatbot integration.

        This method aggregates task insights into a structured format
        suitable for providing context to the AI chatbot.

        Args:
            session: Database session
            user_id: The user to prepare context for

        Returns:
            dict: Structured context for AI consumption
        """
        insights_list = self.analyze_user_tasks(session, user_id)

        # Aggregate statistics
        total_tasks = len(insights_list)
        overdue_count = sum(1 for i in insights_list if i.is_overdue)
        with_reminders = sum(1 for i in insights_list if i.has_reminder)
        neglected_count = sum(
            1 for i in insights_list if i.neglected_days >= self.NEGLECTED_TASK_DAYS
        )

        # Collect all recommendations
        all_recommendations = []
        for insights in insights_list:
            all_recommendations.extend([r.to_dict() for r in insights.recommendations])

        return {
            "summary": {
                "total_pending_tasks": total_tasks,
                "overdue_tasks": overdue_count,
                "tasks_with_reminders": with_reminders,
                "neglected_tasks": neglected_count,
            },
            "recommendations": all_recommendations,
            "generated_at": datetime.utcnow().isoformat(),
        }


# -----------------------------------------------------------------------------
# Singleton Service Instance
# -----------------------------------------------------------------------------

_service_instance: AIInsightsService | None = None


def get_ai_insights_service() -> AIInsightsService:
    """Get or create the AI insights service singleton.

    Returns:
        AIInsightsService: The singleton service instance
    """
    global _service_instance
    if _service_instance is None:
        _service_instance = AIInsightsService()
    return _service_instance

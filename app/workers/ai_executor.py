"""AI recommendation executor for Phase V Step 4.

Safe executor for AI-generated recommendations:
1. Evaluates recommendations with confidence threshold
2. Applies actions only if globally enabled
3. Logs all AI-applied actions to AuditLog
4. Provides easy global disable via configuration
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlmodel import Session, select

from app.config import get_settings
from app.models.task import Task, Priority
from app.models.reminder import TaskReminder
from app.models.audit_log import AuditLog
from app.services.ai_insights import (
    AIInsightsService,
    AIRecommendation,
    RecommendationType,
    RecommendationConfidence,
    get_ai_insights_service,
)
from app.services.reminders import get_reminder_service

logger = logging.getLogger(__name__)


@dataclass
class ExecutionResult:
    """Result of an AI recommendation execution.

    Attributes:
        recommendation: The original recommendation
        applied: Whether the recommendation was applied
        reason: Reason for applying or skipping
        changes: Dict of changes made (if applied)
    """

    recommendation: AIRecommendation
    applied: bool
    reason: str
    changes: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for logging."""
        return {
            "recommendation_type": self.recommendation.recommendation_type.value,
            "task_id": str(self.recommendation.task_id),
            "applied": self.applied,
            "reason": self.reason,
            "changes": self.changes,
        }


class AIExecutor:
    """Safe executor for AI-generated recommendations.

    Features:
    - Confidence threshold gating
    - Global enable/disable flag
    - Comprehensive audit logging
    - Safe, reversible actions only

    Safety Principles:
    1. Never delete data
    2. Log all actions
    3. Respect confidence thresholds
    4. Easy to disable globally
    """

    # Confidence level to numeric value mapping
    CONFIDENCE_VALUES: dict[RecommendationConfidence, float] = {
        RecommendationConfidence.LOW: 0.3,
        RecommendationConfidence.MEDIUM: 0.6,
        RecommendationConfidence.HIGH: 0.9,
    }

    def __init__(self) -> None:
        """Initialize the AI executor."""
        self._logger = logging.getLogger(self.__class__.__name__)
        self._insights_service = get_ai_insights_service()
        self._reminder_service = get_reminder_service()

    def is_enabled(self) -> bool:
        """Check if AI automation is globally enabled.

        Returns:
            True if AI automation is enabled
        """
        settings = get_settings()
        return settings.AI_AUTOMATION_ENABLED

    def get_confidence_threshold(self) -> float:
        """Get the configured confidence threshold.

        Returns:
            Threshold value (0.0 - 1.0)
        """
        settings = get_settings()
        return settings.AI_CONFIDENCE_THRESHOLD

    def meets_threshold(self, recommendation: AIRecommendation) -> bool:
        """Check if recommendation meets confidence threshold.

        Args:
            recommendation: The recommendation to check

        Returns:
            True if meets threshold
        """
        threshold = self.get_confidence_threshold()
        confidence_value = self.CONFIDENCE_VALUES.get(
            recommendation.confidence, 0.0
        )
        return confidence_value >= threshold

    def evaluate_user_tasks(
        self,
        session: Session,
        user_id: UUID,
    ) -> list[AIRecommendation]:
        """Evaluate all tasks for a user and generate recommendations.

        Does NOT apply any changes, just returns recommendations.

        Args:
            session: Database session
            user_id: The user to evaluate

        Returns:
            List of AI recommendations
        """
        insights_list = self._insights_service.analyze_user_tasks(session, user_id)
        recommendations = []

        for insights in insights_list:
            recommendations.extend(insights.recommendations)

        return recommendations

    def execute_recommendation(
        self,
        session: Session,
        recommendation: AIRecommendation,
        dry_run: bool = False,
    ) -> ExecutionResult:
        """Execute a single AI recommendation.

        Args:
            session: Database session
            recommendation: The recommendation to execute
            dry_run: If True, only log what would happen

        Returns:
            ExecutionResult with details
        """
        # Check global enable
        if not self.is_enabled():
            return ExecutionResult(
                recommendation=recommendation,
                applied=False,
                reason="AI automation is globally disabled",
            )

        # Check confidence threshold
        if not self.meets_threshold(recommendation):
            return ExecutionResult(
                recommendation=recommendation,
                applied=False,
                reason=f"Confidence {recommendation.confidence.value} below threshold {self.get_confidence_threshold()}",
            )

        # Route to appropriate handler
        handler = self._get_handler(recommendation.recommendation_type)
        if not handler:
            return ExecutionResult(
                recommendation=recommendation,
                applied=False,
                reason=f"No handler for type {recommendation.recommendation_type.value}",
            )

        # Execute (or dry run)
        try:
            if dry_run:
                result = ExecutionResult(
                    recommendation=recommendation,
                    applied=False,
                    reason="Dry run - would have applied",
                    changes=recommendation.suggested_action,
                )
            else:
                changes = handler(session, recommendation)
                result = ExecutionResult(
                    recommendation=recommendation,
                    applied=True,
                    reason="Successfully applied",
                    changes=changes,
                )

            # Log the execution
            self._log_execution(session, result)

            self._logger.info(
                f"AI recommendation executed",
                extra=result.to_dict(),
            )

            return result

        except Exception as e:
            self._logger.error(
                f"AI recommendation execution failed",
                extra={
                    "recommendation": recommendation.to_dict(),
                    "error": str(e),
                },
                exc_info=True,
            )
            return ExecutionResult(
                recommendation=recommendation,
                applied=False,
                reason=f"Execution failed: {str(e)[:200]}",
            )

    def execute_all_for_user(
        self,
        session: Session,
        user_id: UUID,
        dry_run: bool = False,
    ) -> list[ExecutionResult]:
        """Execute all eligible recommendations for a user.

        Args:
            session: Database session
            user_id: The user to process
            dry_run: If True, only log what would happen

        Returns:
            List of ExecutionResults
        """
        recommendations = self.evaluate_user_tasks(session, user_id)
        results = []

        for rec in recommendations:
            result = self.execute_recommendation(session, rec, dry_run)
            results.append(result)

        return results

    def _get_handler(self, rec_type: RecommendationType):
        """Get the handler function for a recommendation type.

        Args:
            rec_type: The recommendation type

        Returns:
            Handler function or None
        """
        handlers = {
            RecommendationType.PRIORITY_CHANGE: self._apply_priority_change,
            RecommendationType.ADD_REMINDER: self._apply_add_reminder,
            # TASK_OVERDUE and TASK_NEGLECTED are informational only
        }
        return handlers.get(rec_type)

    def _apply_priority_change(
        self,
        session: Session,
        recommendation: AIRecommendation,
    ) -> dict[str, Any]:
        """Apply a priority change recommendation.

        Args:
            session: Database session
            recommendation: The recommendation

        Returns:
            Dict of changes made
        """
        task = session.get(Task, recommendation.task_id)
        if not task:
            raise ValueError(f"Task {recommendation.task_id} not found")

        action = recommendation.suggested_action
        old_priority = task.priority.value
        new_priority = action.get("suggested_value", task.priority.value)

        # Apply the change
        task.priority = Priority(new_priority)
        task.updated_at = datetime.utcnow()
        session.add(task)

        return {
            "field": "priority",
            "old_value": old_priority,
            "new_value": new_priority,
        }

    def _apply_add_reminder(
        self,
        session: Session,
        recommendation: AIRecommendation,
    ) -> dict[str, Any]:
        """Apply an add reminder recommendation.

        Args:
            session: Database session
            recommendation: The recommendation

        Returns:
            Dict of changes made
        """
        task = session.get(Task, recommendation.task_id)
        if not task:
            raise ValueError(f"Task {recommendation.task_id} not found")

        action = recommendation.suggested_action
        remind_at_str = action.get("remind_at")
        if not remind_at_str:
            raise ValueError("remind_at not specified in recommendation")

        remind_at = datetime.fromisoformat(remind_at_str.replace("Z", "+00:00"))

        # Create the reminder
        reminder = self._reminder_service.create_reminder(
            session=session,
            task_id=task.id,
            user_id=task.user_id,
            remind_at=remind_at,
        )

        return {
            "action": "add_reminder",
            "reminder_id": str(reminder.id),
            "remind_at": remind_at.isoformat(),
        }

    def _log_execution(
        self,
        session: Session,
        result: ExecutionResult,
    ) -> None:
        """Log AI execution to audit log.

        Args:
            session: Database session
            result: The execution result
        """
        rec = result.recommendation

        # Determine action based on result
        if result.applied:
            action = "ai.recommendation.applied"
        elif "dry run" in result.reason.lower():
            action = "ai.recommendation.dry_run"
        else:
            action = "ai.recommendation.skipped"

        audit = AuditLog(
            user_id=rec.suggested_action.get("user_id", rec.task_id),  # Best effort
            action=action,
            entity_type="task",
            entity_id=rec.task_id,
            details={
                "recommendation_type": rec.recommendation_type.value,
                "confidence": rec.confidence.value,
                "applied": result.applied,
                "reason": result.reason,
                "changes": result.changes,
                "ai_automated": True,
            },
        )
        session.add(audit)


# Singleton instance
_executor_instance: AIExecutor | None = None


def get_ai_executor() -> AIExecutor:
    """Get or create the AI executor singleton.

    Returns:
        AIExecutor instance
    """
    global _executor_instance
    if _executor_instance is None:
        _executor_instance = AIExecutor()
    return _executor_instance

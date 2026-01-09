"""Services module for Phase V event-driven architecture.

Services:
- tasks.py: Task CRUD with event emission and consumer dispatch
- reminders.py: Reminder scheduling and management (Phase V Step 3)
- ai_insights.py: AI decision hooks and recommendations (Phase V Step 3)
- auth.py: Authentication and JWT management
- conversation.py: Chat conversation management
"""

from app.services.reminders import ReminderService, get_reminder_service
from app.services.ai_insights import AIInsightsService, get_ai_insights_service

__all__ = [
    # Reminder service (Phase V Step 3)
    "ReminderService",
    "get_reminder_service",
    # AI insights service (Phase V Step 3)
    "AIInsightsService",
    "get_ai_insights_service",
]

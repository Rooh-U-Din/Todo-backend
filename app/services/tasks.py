"""Task service for CRUD operations with event-driven workflows.

Phase V Step 3: Extended with event consumer dispatch and reminder handling.

Event Flow:
    API → create/update/delete → emit TaskEvent → dispatch to consumers
                                                     ↓
                                    [AuditConsumer, NotificationConsumer,
                                     RecurrenceConsumer, AIInsightsConsumer]
"""

import logging
from datetime import datetime, timedelta
from uuid import UUID

from sqlmodel import Session, func, select

from app.config import get_settings
from app.events.publisher import get_event_publisher
from app.events.consumers import get_event_dispatcher
from app.events.types import EventType, TaskEventData
from app.models.task import Task, TaskCreate, TaskUpdate, RecurrenceType, Priority
from app.models.task_event import TaskEvent
from app.services.reminders import get_reminder_service

logger = logging.getLogger(__name__)


class TaskValidationError(Exception):
    """Exception raised for task validation errors."""
    pass


def _build_event_data(task: Task) -> dict:
    """Build event payload data from a task.

    Args:
        task: The task to build data from

    Returns:
        dict: Event payload data
    """
    event_data = {
        "title": task.title,
        "is_completed": task.is_completed,
    }

    if task.description:
        event_data["description"] = task.description
    if task.due_at:
        event_data["due_at"] = task.due_at.isoformat()
    if task.recurrence_type != RecurrenceType.NONE:
        event_data["recurrence_type"] = task.recurrence_type.value
    if task.recurrence_interval:
        event_data["recurrence_interval"] = task.recurrence_interval
    if task.priority:
        event_data["priority"] = task.priority.value
    if task.parent_task_id:
        event_data["parent_task_id"] = str(task.parent_task_id)

    return event_data


def _emit_task_event(
    session: Session,
    event_type: EventType,
    task: Task,
) -> TaskEvent | None:
    """Emit a task event using the outbox pattern.

    This function:
    1. Persists the event to the database (outbox pattern)
    2. Dispatches to in-process consumers for immediate reactions
    3. Queues the event for external publishing (Dapr/Kafka)

    Args:
        session: Database session
        event_type: Type of event to emit
        task: The task that triggered the event

    Returns:
        TaskEvent or None if events are disabled
    """
    settings = get_settings()
    if not settings.EVENTS_ENABLED:
        return None

    publisher = get_event_publisher()
    dispatcher = get_event_dispatcher()

    # Build event data
    event_data = _build_event_data(task)

    # Persist event to outbox (within same transaction)
    task_event = publisher.emit(
        session=session,
        event_type=event_type,
        task_id=task.id,
        user_id=task.user_id,
        data=event_data,
    )

    # Create TaskEventData for dispatcher
    event = TaskEventData(
        event_id=task_event.id,
        event_type=event_type,
        aggregate_id=task.id,
        user_id=task.user_id,
        timestamp=task_event.created_at,
        data=event_data,
    )

    # Phase V Step 3: Dispatch to in-process consumers
    # This handles AuditLog, NotificationDelivery, and AI insights
    try:
        dispatcher.dispatch(session, event, task_event)
    except Exception as e:
        logger.error(
            "Event dispatch to consumers failed",
            extra={
                "event_id": str(task_event.id),
                "event_type": event_type.value,
                "error": str(e),
            },
            exc_info=True,
        )
        # Don't fail the main operation if dispatch fails

    # Store the event for post-commit external publishing
    session.info["pending_events"] = session.info.get("pending_events", [])
    session.info["pending_events"].append(task_event)

    return task_event


def _publish_pending_events(session: Session) -> None:
    """Publish any pending events after transaction commit.

    This should be called after session.commit() succeeds.
    Publishing failures are logged but do NOT raise exceptions.
    """
    settings = get_settings()
    if not settings.EVENTS_ENABLED:
        return

    pending_events = session.info.get("pending_events", [])
    if not pending_events:
        return

    publisher = get_event_publisher()
    for task_event in pending_events:
        publisher.publish_event(session, task_event)

    # Clear pending events
    session.info["pending_events"] = []


def validate_recurrence(
    recurrence_type: RecurrenceType | None,
    recurrence_interval: int | None,
) -> None:
    """Validate recurrence settings.

    Raises:
        TaskValidationError: If recurrence settings are invalid.
    """
    if recurrence_type == RecurrenceType.CUSTOM:
        if recurrence_interval is None:
            raise TaskValidationError(
                "recurrence_interval is required when recurrence_type is 'custom'"
            )
        if recurrence_interval < 1 or recurrence_interval > 365:
            raise TaskValidationError(
                "recurrence_interval must be between 1 and 365 days"
            )


def create_task(session: Session, user_id: UUID, task_data: TaskCreate) -> Task:
    """Create a new task for the specified user.

    Raises:
        TaskValidationError: If task data is invalid.
    """
    recurrence_type = task_data.recurrence_type or RecurrenceType.NONE

    # Phase V: Validate recurrence settings
    validate_recurrence(recurrence_type, task_data.recurrence_interval)

    task = Task(
        user_id=user_id,
        title=task_data.title,
        description=task_data.description,
        # Phase V: Extended fields with defaults
        recurrence_type=recurrence_type,
        recurrence_interval=task_data.recurrence_interval,
        due_at=task_data.due_at,
        priority=task_data.priority or Priority.MEDIUM,
    )

    # Calculate next_occurrence_at for recurring tasks
    if task.recurrence_type != RecurrenceType.NONE and task.due_at:
        task.next_occurrence_at = task.due_at

    session.add(task)
    # Flush to get task.id before emitting event
    session.flush()

    # Phase V: Emit task.created event (outbox pattern)
    _emit_task_event(session, EventType.TASK_CREATED, task)

    session.commit()
    session.refresh(task)

    # Phase V: Publish pending events after commit
    _publish_pending_events(session)

    return task


def get_user_tasks(
    session: Session,
    user_id: UUID,
    completed: bool | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Task], int]:
    """
    Get tasks for the specified user with optional filtering.
    Returns (tasks, total_count).
    """
    query = select(Task).where(Task.user_id == user_id)
    count_query = select(func.count()).select_from(Task).where(Task.user_id == user_id)

    if completed is not None:
        query = query.where(Task.is_completed == completed)
        count_query = count_query.where(Task.is_completed == completed)

    query = query.order_by(Task.created_at.desc()).offset(offset).limit(limit)

    tasks = list(session.exec(query).all())
    total = session.exec(count_query).one()

    return tasks, total


def get_filtered_tasks(
    session: Session,
    user_id: UUID,
    completed: bool | None = None,
    priority: Priority | None = None,
    tag_id: UUID | None = None,
    due_before: datetime | None = None,
    due_after: datetime | None = None,
    search: str | None = None,
    sort_by: str | None = None,
    sort_order: str = "desc",
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Task], int]:
    """Get tasks with advanced filtering and sorting.

    Phase V Step 5: Enhanced filtering for priority, tags, dates, and search.

    Args:
        session: Database session
        user_id: The user ID
        completed: Filter by completion status
        priority: Filter by priority level
        tag_id: Filter by tag (tasks with this tag)
        due_before: Filter tasks due before this date
        due_after: Filter tasks due after this date
        search: Search in title and description
        sort_by: Sort field (created_at, due_at, priority)
        sort_order: Sort order (asc, desc)
        limit: Maximum tasks to return
        offset: Number of tasks to skip

    Returns:
        tuple[list[Task], int]: Tasks and total count
    """
    from app.models.tag import TaskTagAssociation

    query = select(Task).where(Task.user_id == user_id)
    count_query = select(func.count()).select_from(Task).where(Task.user_id == user_id)

    # Filter by completion status
    if completed is not None:
        query = query.where(Task.is_completed == completed)
        count_query = count_query.where(Task.is_completed == completed)

    # Filter by priority
    if priority is not None:
        query = query.where(Task.priority == priority)
        count_query = count_query.where(Task.priority == priority)

    # Filter by tag (join with associations)
    if tag_id is not None:
        query = query.join(TaskTagAssociation, Task.id == TaskTagAssociation.task_id)
        query = query.where(TaskTagAssociation.tag_id == tag_id)
        count_query = count_query.join(TaskTagAssociation, Task.id == TaskTagAssociation.task_id)
        count_query = count_query.where(TaskTagAssociation.tag_id == tag_id)

    # Filter by due date range
    if due_before is not None:
        query = query.where(Task.due_at != None).where(Task.due_at <= due_before)
        count_query = count_query.where(Task.due_at != None).where(Task.due_at <= due_before)

    if due_after is not None:
        query = query.where(Task.due_at != None).where(Task.due_at >= due_after)
        count_query = count_query.where(Task.due_at != None).where(Task.due_at >= due_after)

    # Search in title and description
    if search:
        search_pattern = f"%{search}%"
        query = query.where(
            (Task.title.ilike(search_pattern)) | (Task.description.ilike(search_pattern))
        )
        count_query = count_query.where(
            (Task.title.ilike(search_pattern)) | (Task.description.ilike(search_pattern))
        )

    # Sorting
    if sort_by == "created_at":
        order_col = Task.created_at
    elif sort_by == "due_at":
        order_col = Task.due_at
    elif sort_by == "priority":
        # Priority order: high > medium > low
        order_col = Task.priority
    else:
        order_col = Task.created_at

    if sort_order == "asc":
        query = query.order_by(order_col.asc())
    else:
        query = query.order_by(order_col.desc())

    # Pagination
    query = query.offset(offset).limit(limit)

    tasks = list(session.exec(query).all())
    total = session.exec(count_query).one()

    return tasks, total


def get_task_by_id(session: Session, user_id: UUID, task_id: UUID) -> Task | None:
    """Get a specific task owned by the user."""
    return session.exec(
        select(Task).where(Task.id == task_id, Task.user_id == user_id)
    ).first()


def update_task(
    session: Session, task: Task, task_data: TaskUpdate
) -> Task:
    """Update a task with the provided data.

    Raises:
        TaskValidationError: If task data is invalid.
    """
    update_data = task_data.model_dump(exclude_unset=True)

    # Phase V: Validate recurrence settings if updating recurrence fields
    new_recurrence_type = update_data.get("recurrence_type", task.recurrence_type)
    new_recurrence_interval = update_data.get("recurrence_interval", task.recurrence_interval)
    validate_recurrence(new_recurrence_type, new_recurrence_interval)

    for key, value in update_data.items():
        setattr(task, key, value)

    # Update next_occurrence_at if recurrence or due_at changed
    if task.recurrence_type != RecurrenceType.NONE and task.due_at:
        if not task.is_completed:
            task.next_occurrence_at = task.due_at
    else:
        task.next_occurrence_at = None

    task.updated_at = datetime.utcnow()
    session.add(task)

    # Phase V: Emit task.updated event (outbox pattern)
    _emit_task_event(session, EventType.TASK_UPDATED, task)

    session.commit()
    session.refresh(task)

    # Phase V: Publish pending events after commit
    _publish_pending_events(session)

    return task


def toggle_task_completion(session: Session, task: Task) -> Task:
    """Toggle the completion status of a task.

    Phase V Step 3: Enhanced with reminder cancellation and recurrence events.

    When completing a task:
    1. Toggle is_completed flag
    2. Cancel any pending reminders
    3. For recurring tasks, generate next occurrence
    4. Emit TASK_COMPLETED event
    5. For recurring tasks, also emit TASK_RECURRED event
    """
    task.is_completed = not task.is_completed
    task.updated_at = datetime.utcnow()

    next_task = None

    # Phase V Step 3: Handle task completion side effects
    if task.is_completed:
        # Cancel pending reminders for completed task
        reminder_service = get_reminder_service()
        reminder_service.handle_task_completion(session, task.id)

        # Generate next occurrence for recurring tasks
        if task.recurrence_type != RecurrenceType.NONE:
            next_task = _generate_next_occurrence(session, task)
            if next_task:
                session.add(next_task)
                session.flush()  # Get next_task.id for event emission

    session.add(task)

    # Phase V: Emit appropriate event based on completion state
    if task.is_completed:
        _emit_task_event(session, EventType.TASK_COMPLETED, task)

        # Phase V Step 3: Emit TASK_RECURRED for the new occurrence
        if next_task:
            _emit_task_event(session, EventType.TASK_RECURRED, next_task)
    else:
        _emit_task_event(session, EventType.TASK_UPDATED, task)

    session.commit()
    session.refresh(task)

    # Phase V: Publish pending events after commit
    _publish_pending_events(session)

    return task


def _calculate_next_due_date(task: Task) -> datetime | None:
    """Calculate the next due date based on recurrence type."""
    if not task.due_at:
        return None

    base_date = task.due_at
    now = datetime.utcnow()

    # If the due date is in the past, calculate from now
    if base_date < now:
        base_date = now

    if task.recurrence_type == RecurrenceType.DAILY:
        return base_date + timedelta(days=1)
    elif task.recurrence_type == RecurrenceType.WEEKLY:
        return base_date + timedelta(weeks=1)
    elif task.recurrence_type == RecurrenceType.CUSTOM and task.recurrence_interval:
        return base_date + timedelta(days=task.recurrence_interval)

    return None


def _generate_next_occurrence(session: Session, completed_task: Task) -> Task | None:
    """Generate the next occurrence of a recurring task."""
    next_due = _calculate_next_due_date(completed_task)
    if not next_due:
        return None

    next_task = Task(
        user_id=completed_task.user_id,
        title=completed_task.title,
        description=completed_task.description,
        recurrence_type=completed_task.recurrence_type,
        recurrence_interval=completed_task.recurrence_interval,
        due_at=next_due,
        next_occurrence_at=next_due,
        priority=completed_task.priority,
        parent_task_id=completed_task.parent_task_id or completed_task.id,
    )
    return next_task


def delete_task(session: Session, task: Task) -> None:
    """Delete a task.

    Phase V Step 3: Enhanced with reminder cancellation.
    """
    # Phase V Step 3: Cancel pending reminders before deletion
    reminder_service = get_reminder_service()
    reminder_service.handle_task_deletion(session, task.id)

    # Phase V: Emit task.deleted event BEFORE deleting (need task data)
    _emit_task_event(session, EventType.TASK_DELETED, task)

    session.delete(task)
    session.commit()

    # Phase V: Publish pending events after commit
    _publish_pending_events(session)

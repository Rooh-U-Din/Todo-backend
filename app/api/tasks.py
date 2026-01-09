"""Task API endpoints.

Phase V Step 5: Extended with reminders, tags, and advanced filtering.
"""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from app.api.deps import CurrentUser, DBSession
from app.models.task import TaskCreate, TaskListResponse, TaskResponse, TaskUpdate, Priority
from app.models.reminder import ReminderCreate, ReminderResponse
from app.models.tag import TagResponse
from app.services.tasks import (
    TaskValidationError,
    create_task,
    delete_task,
    get_task_by_id,
    get_user_tasks,
    get_filtered_tasks,
    toggle_task_completion,
    update_task,
)
from app.services.reminders import get_reminder_service
from app.services.tags import (
    TagNotFoundError,
    assign_tags_to_task,
    get_task_tags,
)

router = APIRouter(prefix="/api/tasks", tags=["Tasks"])


class TagAssignment(BaseModel):
    """Request body for tag assignment."""
    tag_ids: list[UUID]


@router.post("", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
def create_task_endpoint(
    session: DBSession,
    current_user: CurrentUser,
    task_data: TaskCreate,
) -> TaskResponse:
    """Create a new task for the authenticated user."""
    try:
        task = create_task(session, current_user.id, task_data)
        return TaskResponse.model_validate(task)
    except TaskValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.get("", response_model=TaskListResponse)
def list_tasks_endpoint(
    session: DBSession,
    current_user: CurrentUser,
    completed: bool | None = Query(default=None, description="Filter by completion status"),
    priority: Priority | None = Query(default=None, description="Filter by priority"),
    tag_id: UUID | None = Query(default=None, description="Filter by tag ID"),
    due_before: datetime | None = Query(default=None, description="Filter tasks due before this date"),
    due_after: datetime | None = Query(default=None, description="Filter tasks due after this date"),
    search: str | None = Query(default=None, max_length=100, description="Search in title and description"),
    sort_by: str | None = Query(default=None, pattern="^(created_at|due_at|priority)$", description="Sort field"),
    sort_order: str = Query(default="desc", pattern="^(asc|desc)$", description="Sort order"),
    limit: int = Query(default=50, ge=1, le=100, description="Maximum number of tasks"),
    offset: int = Query(default=0, ge=0, description="Number of tasks to skip"),
) -> TaskListResponse:
    """List all tasks for the authenticated user with optional filtering and sorting.

    Phase V Step 5: Enhanced with priority, tag, date, and search filtering.
    """
    tasks, total = get_filtered_tasks(
        session=session,
        user_id=current_user.id,
        completed=completed,
        priority=priority,
        tag_id=tag_id,
        due_before=due_before,
        due_after=due_after,
        search=search,
        sort_by=sort_by,
        sort_order=sort_order,
        limit=limit,
        offset=offset,
    )
    return TaskListResponse(
        tasks=[TaskResponse.model_validate(t) for t in tasks],
        total=total,
    )


@router.get("/{task_id}", response_model=TaskResponse)
def get_task_endpoint(
    session: DBSession,
    current_user: CurrentUser,
    task_id: UUID,
) -> TaskResponse:
    """Get a specific task by ID."""
    task = get_task_by_id(session, current_user.id, task_id)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task not found",
        )
    return TaskResponse.model_validate(task)


@router.put("/{task_id}", response_model=TaskResponse)
def update_task_endpoint(
    session: DBSession,
    current_user: CurrentUser,
    task_id: UUID,
    task_data: TaskUpdate,
) -> TaskResponse:
    """Update a task."""
    task = get_task_by_id(session, current_user.id, task_id)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task not found",
        )

    # Validate that title is not empty if provided
    if task_data.title is not None and len(task_data.title.strip()) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Title cannot be empty",
        )

    try:
        updated_task = update_task(session, task, task_data)
        return TaskResponse.model_validate(updated_task)
    except TaskValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.post("/{task_id}/toggle", response_model=TaskResponse)
def toggle_task_endpoint(
    session: DBSession,
    current_user: CurrentUser,
    task_id: UUID,
) -> TaskResponse:
    """Toggle task completion status."""
    task = get_task_by_id(session, current_user.id, task_id)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task not found",
        )

    toggled_task = toggle_task_completion(session, task)
    return TaskResponse.model_validate(toggled_task)


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task_endpoint(
    session: DBSession,
    current_user: CurrentUser,
    task_id: UUID,
) -> None:
    """Delete a task."""
    task = get_task_by_id(session, current_user.id, task_id)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task not found",
        )

    delete_task(session, task)


# =============================================================================
# Reminder Endpoints (Phase V Step 5)
# =============================================================================


@router.post("/{task_id}/reminder", response_model=ReminderResponse, status_code=status.HTTP_201_CREATED)
def create_reminder_endpoint(
    session: DBSession,
    current_user: CurrentUser,
    task_id: UUID,
    reminder_data: ReminderCreate,
) -> ReminderResponse:
    """Create a reminder for a task.

    If a reminder already exists, it will be replaced.
    """
    task = get_task_by_id(session, current_user.id, task_id)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task not found",
        )

    if task.is_completed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot create reminder for completed task",
        )

    reminder_service = get_reminder_service()
    reminder = reminder_service.create_reminder(
        session=session,
        task_id=task.id,
        user_id=current_user.id,
        remind_at=reminder_data.remind_at,
    )
    session.commit()

    return ReminderResponse.model_validate(reminder)


@router.delete("/{task_id}/reminder", status_code=status.HTTP_204_NO_CONTENT)
def delete_reminder_endpoint(
    session: DBSession,
    current_user: CurrentUser,
    task_id: UUID,
) -> None:
    """Cancel all pending reminders for a task."""
    task = get_task_by_id(session, current_user.id, task_id)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task not found",
        )

    reminder_service = get_reminder_service()
    reminder_service.cancel_task_reminders(session, task.id)
    session.commit()


@router.get("/{task_id}/reminder", response_model=ReminderResponse | None)
def get_reminder_endpoint(
    session: DBSession,
    current_user: CurrentUser,
    task_id: UUID,
) -> ReminderResponse | None:
    """Get the current pending reminder for a task."""
    task = get_task_by_id(session, current_user.id, task_id)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task not found",
        )

    reminder_service = get_reminder_service()
    reminders = reminder_service.get_upcoming_reminders(session, current_user.id, within_hours=24*365)

    for reminder in reminders:
        if reminder.task_id == task_id:
            return ReminderResponse.model_validate(reminder)

    return None


# =============================================================================
# Tag Assignment Endpoints (Phase V Step 5)
# =============================================================================


@router.put("/{task_id}/tags", response_model=list[TagResponse])
def assign_tags_endpoint(
    session: DBSession,
    current_user: CurrentUser,
    task_id: UUID,
    assignment: TagAssignment,
) -> list[TagResponse]:
    """Assign tags to a task (replaces existing assignments)."""
    task = get_task_by_id(session, current_user.id, task_id)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task not found",
        )

    try:
        tags = assign_tags_to_task(session, current_user.id, task_id, assignment.tag_ids)
        return [TagResponse.model_validate(t) for t in tags]
    except TagNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )


@router.get("/{task_id}/tags", response_model=list[TagResponse])
def get_task_tags_endpoint(
    session: DBSession,
    current_user: CurrentUser,
    task_id: UUID,
) -> list[TagResponse]:
    """Get all tags assigned to a task."""
    task = get_task_by_id(session, current_user.id, task_id)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task not found",
        )

    tags = get_task_tags(session, task_id)
    return [TagResponse.model_validate(t) for t in tags]

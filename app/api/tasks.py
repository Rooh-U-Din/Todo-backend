"""Task API endpoints."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import CurrentUser, DBSession
from app.models.task import TaskCreate, TaskListResponse, TaskResponse, TaskUpdate
from app.services.tasks import (
    create_task,
    delete_task,
    get_task_by_id,
    get_user_tasks,
    toggle_task_completion,
    update_task,
)

router = APIRouter(prefix="/api/tasks", tags=["Tasks"])


@router.post("", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
def create_task_endpoint(
    session: DBSession,
    current_user: CurrentUser,
    task_data: TaskCreate,
) -> TaskResponse:
    """Create a new task for the authenticated user."""
    task = create_task(session, current_user.id, task_data)
    return TaskResponse.model_validate(task)


@router.get("", response_model=TaskListResponse)
def list_tasks_endpoint(
    session: DBSession,
    current_user: CurrentUser,
    completed: bool | None = Query(default=None, description="Filter by completion status"),
    limit: int = Query(default=50, ge=1, le=100, description="Maximum number of tasks"),
    offset: int = Query(default=0, ge=0, description="Number of tasks to skip"),
) -> TaskListResponse:
    """List all tasks for the authenticated user."""
    tasks, total = get_user_tasks(session, current_user.id, completed, limit, offset)
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

    updated_task = update_task(session, task, task_data)
    return TaskResponse.model_validate(updated_task)


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

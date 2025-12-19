"""Task service for CRUD operations."""

from datetime import datetime
from uuid import UUID

from sqlmodel import Session, func, select

from app.models.task import Task, TaskCreate, TaskUpdate


def create_task(session: Session, user_id: UUID, task_data: TaskCreate) -> Task:
    """Create a new task for the specified user."""
    task = Task(
        user_id=user_id,
        title=task_data.title,
        description=task_data.description,
    )
    session.add(task)
    session.commit()
    session.refresh(task)
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


def get_task_by_id(session: Session, user_id: UUID, task_id: UUID) -> Task | None:
    """Get a specific task owned by the user."""
    return session.exec(
        select(Task).where(Task.id == task_id, Task.user_id == user_id)
    ).first()


def update_task(
    session: Session, task: Task, task_data: TaskUpdate
) -> Task:
    """Update a task with the provided data."""
    update_data = task_data.model_dump(exclude_unset=True)

    for key, value in update_data.items():
        setattr(task, key, value)

    task.updated_at = datetime.utcnow()
    session.add(task)
    session.commit()
    session.refresh(task)
    return task


def toggle_task_completion(session: Session, task: Task) -> Task:
    """Toggle the completion status of a task."""
    task.is_completed = not task.is_completed
    task.updated_at = datetime.utcnow()
    session.add(task)
    session.commit()
    session.refresh(task)
    return task


def delete_task(session: Session, task: Task) -> None:
    """Delete a task."""
    session.delete(task)
    session.commit()

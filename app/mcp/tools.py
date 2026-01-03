"""Tools for task management via Gemini AI agent.

These tools expose task operations to Google Gemini's function calling,
allowing the AI to perform CRUD operations on behalf of users.
"""

import logging
import sys
from typing import Any
from uuid import UUID

from sqlmodel import Session

from app.models.task import TaskCreate, TaskUpdate
from app.services import tasks as task_service

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)


# Tool definitions for Gemini function calling
TOOL_DEFINITIONS = [
    {
        "name": "add_task",
        "description": "Create a new task for the user. Use this when the user wants to add, create, or make a new task.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "The title of the task (required, 1-200 characters)"
                },
                "description": {
                    "type": "string",
                    "description": "Optional description for the task (max 2000 characters)"
                }
            },
            "required": ["title"]
        }
    },
    {
        "name": "list_tasks",
        "description": "Get the user's tasks. Use this when the user wants to see, show, list, or view their tasks.",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["all", "pending", "completed"],
                    "description": "Filter by status - 'all', 'pending', or 'completed'"
                }
            },
            "required": []
        }
    },
    {
        "name": "complete_task",
        "description": "Mark a task as completed. Use this when the user wants to complete, finish, or mark a task as done. You can specify the task by ID or by name/title.",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "The UUID of the task to complete (optional if task_name is provided)"
                },
                "task_name": {
                    "type": "string",
                    "description": "The name/title of the task to complete (optional if task_id is provided)"
                }
            },
            "required": []
        }
    },
    {
        "name": "delete_task",
        "description": "Delete a task. Use this when the user wants to delete, remove, or get rid of a task. You can specify the task by ID or by name/title.",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "The UUID of the task to delete (optional if task_name is provided)"
                },
                "task_name": {
                    "type": "string",
                    "description": "The name/title of the task to delete (optional if task_id is provided)"
                }
            },
            "required": []
        }
    },
    {
        "name": "update_task",
        "description": "Update a task's title and/or description. Use this when the user wants to rename, change, or update a task.",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "The UUID of the task to update"
                },
                "title": {
                    "type": "string",
                    "description": "New title for the task (optional)"
                },
                "description": {
                    "type": "string",
                    "description": "New description for the task (optional)"
                }
            },
            "required": ["task_id"]
        }
    }
]


def execute_tool(
    tool_name: str,
    args: dict[str, Any],
    user_id: str,
    session: Session
) -> dict[str, Any]:
    """Execute a tool by name with the given arguments."""

    if tool_name == "add_task":
        return _add_task(
            user_id=user_id,
            session=session,
            title=args.get("title", ""),
            description=args.get("description"),
        )
    elif tool_name == "list_tasks":
        return _list_tasks(
            user_id=user_id,
            session=session,
            status=args.get("status", "all"),
        )
    elif tool_name == "complete_task":
        return _complete_task(
            user_id=user_id,
            session=session,
            task_id=args.get("task_id", ""),
            task_name=args.get("task_name", ""),
        )
    elif tool_name == "delete_task":
        return _delete_task(
            user_id=user_id,
            session=session,
            task_id=args.get("task_id", ""),
            task_name=args.get("task_name", ""),
        )
    elif tool_name == "update_task":
        return _update_task(
            user_id=user_id,
            session=session,
            task_id=args.get("task_id", ""),
            title=args.get("title"),
            description=args.get("description"),
        )
    else:
        return {"error": f"Unknown tool: {tool_name}"}


def _add_task(
    user_id: str,
    session: Session,
    title: str,
    description: str | None = None
) -> dict[str, Any]:
    """Create a new task for the user."""
    try:
        task_data = TaskCreate(title=title, description=description)
        task = task_service.create_task(
            session=session,
            user_id=UUID(user_id),
            task_data=task_data,
        )
        return {
            "task_id": str(task.id),
            "status": "created",
            "title": task.title,
        }
    except Exception as e:
        logger.error(f"Failed to create task: {e}")
        return {
            "task_id": None,
            "status": "error",
            "title": title,
            "error": str(e),
        }


def _list_tasks(
    user_id: str,
    session: Session,
    status: str = "all"
) -> dict[str, Any]:
    """Get the user's tasks with optional filtering."""
    try:
        # Convert status to completed filter
        completed = None
        if status == "pending":
            completed = False
        elif status == "completed":
            completed = True

        tasks, count = task_service.get_user_tasks(
            session=session,
            user_id=UUID(user_id),
            completed=completed,
            limit=50,
        )

        return {
            "tasks": [
                {
                    "id": str(task.id),
                    "title": task.title,
                    "description": task.description,
                    "is_completed": task.is_completed,
                    "created_at": task.created_at.isoformat(),
                }
                for task in tasks
            ],
            "count": count,
        }
    except Exception as e:
        return {
            "tasks": [],
            "count": 0,
            "error": str(e),
        }


def _find_task_by_name(
    user_id: str,
    session: Session,
    task_name: str
):
    """Find a task by name/title (case-insensitive partial match)."""
    tasks, _ = task_service.get_user_tasks(
        session=session,
        user_id=UUID(user_id),
        limit=100,
    )

    task_name_lower = task_name.lower().strip()

    # Try exact match first
    for task in tasks:
        if task.title.lower() == task_name_lower:
            return task

    # Try partial match
    for task in tasks:
        if task_name_lower in task.title.lower():
            return task

    # If only one task exists, return it (user said "the task")
    if len(tasks) == 1:
        return tasks[0]

    return None


def _find_task_by_id_prefix(
    user_id: str,
    session: Session,
    id_prefix: str
):
    """Find a task by UUID prefix (for short ID matching)."""
    tasks, _ = task_service.get_user_tasks(
        session=session,
        user_id=UUID(user_id),
        limit=100,
    )

    id_prefix_lower = id_prefix.lower().strip()
    if not id_prefix_lower:
        return None

    # Match by UUID prefix
    for task in tasks:
        if str(task.id).lower().startswith(id_prefix_lower):
            return task

    return None


def _complete_task(
    user_id: str,
    session: Session,
    task_id: str,
    task_name: str = ""
) -> dict[str, Any]:
    """Mark a task as completed."""
    try:
        task = None

        # Try to find by full UUID first
        if task_id:
            try:
                task = task_service.get_task_by_id(
                    session=session,
                    user_id=UUID(user_id),
                    task_id=UUID(task_id),
                )
            except ValueError:
                # Invalid UUID format - try as UUID prefix
                logger.info(f"task_id '{task_id}' is not a valid UUID, trying prefix match")
                task = _find_task_by_id_prefix(user_id, session, task_id)

        # If no ID or not found by ID, try by name
        if not task and task_name:
            task = _find_task_by_name(user_id, session, task_name)

        # If task_id was provided but failed, also try it as a name
        if not task and task_id and not task_name:
            logger.info(f"Trying task_id '{task_id}' as task name")
            task = _find_task_by_name(user_id, session, task_id)

        # If still nothing and user just said "complete the task", find single task
        if not task and not task_id and not task_name:
            task = _find_task_by_name(user_id, session, "")

        if not task:
            return {
                "task_id": task_id or task_name,
                "status": "not_found",
                "title": None,
                "message": "Could not find the task. Try 'list tasks' to see available tasks.",
            }

        # Mark as completed
        task_data = TaskUpdate(is_completed=True)
        updated_task = task_service.update_task(
            session=session,
            task=task,
            task_data=task_data,
        )

        logger.info(f"Task completed: {updated_task.id} - {updated_task.title}")

        return {
            "task_id": str(updated_task.id),
            "status": "completed",
            "title": updated_task.title,
        }
    except Exception as e:
        logger.error(f"Error completing task: {e}")
        return {
            "task_id": task_id,
            "status": "error",
            "error": str(e),
        }


def _delete_task(
    user_id: str,
    session: Session,
    task_id: str,
    task_name: str = ""
) -> dict[str, Any]:
    """Delete a task."""
    try:
        task = None

        # Try to find by full UUID first
        if task_id:
            try:
                task = task_service.get_task_by_id(
                    session=session,
                    user_id=UUID(user_id),
                    task_id=UUID(task_id),
                )
            except ValueError:
                # Invalid UUID format - try as UUID prefix
                logger.info(f"task_id '{task_id}' is not a valid UUID, trying prefix match")
                task = _find_task_by_id_prefix(user_id, session, task_id)

        # If no ID or not found by ID, try by name
        if not task and task_name:
            task = _find_task_by_name(user_id, session, task_name)

        # If task_id was provided but failed, also try it as a name
        if not task and task_id and not task_name:
            logger.info(f"Trying task_id '{task_id}' as task name")
            task = _find_task_by_name(user_id, session, task_id)

        # If still nothing and user just said "delete the task", find single task
        if not task and not task_id and not task_name:
            task = _find_task_by_name(user_id, session, "")

        if not task:
            return {
                "task_id": task_id or task_name,
                "status": "not_found",
                "title": None,
                "message": "Could not find the task. Try 'list tasks' to see available tasks.",
            }

        title = task.title
        deleted_id = str(task.id)
        task_service.delete_task(session=session, task=task)

        logger.info(f"Task deleted: {deleted_id} - {title}")

        return {
            "task_id": deleted_id,
            "status": "deleted",
            "title": title,
        }
    except Exception as e:
        logger.error(f"Error deleting task: {e}")
        return {
            "task_id": task_id,
            "status": "error",
            "error": str(e),
        }


def _update_task(
    user_id: str,
    session: Session,
    task_id: str,
    title: str | None = None,
    description: str | None = None
) -> dict[str, Any]:
    """Update a task's title and/or description."""
    try:
        task = None

        # Try to find by full UUID first
        if task_id:
            try:
                task = task_service.get_task_by_id(
                    session=session,
                    user_id=UUID(user_id),
                    task_id=UUID(task_id),
                )
            except ValueError:
                # Invalid UUID format - try as UUID prefix
                logger.info(f"task_id '{task_id}' is not a valid UUID, trying prefix match")
                task = _find_task_by_id_prefix(user_id, session, task_id)

        # If task_id was provided but failed, also try it as a name
        if not task and task_id:
            logger.info(f"Trying task_id '{task_id}' as task name")
            task = _find_task_by_name(user_id, session, task_id)

        if not task:
            return {
                "task_id": task_id,
                "status": "not_found",
                "title": None,
                "message": "Could not find the task. Try 'list tasks' to see available tasks.",
            }

        if title is None and description is None:
            return {
                "task_id": task_id,
                "status": "no_changes",
                "title": task.title,
            }

        task_data = TaskUpdate(title=title, description=description)
        updated_task = task_service.update_task(
            session=session,
            task=task,
            task_data=task_data,
        )

        logger.info(f"Task updated: {updated_task.id} - {updated_task.title}")

        return {
            "task_id": str(updated_task.id),
            "status": "updated",
            "title": updated_task.title,
        }
    except Exception as e:
        logger.error(f"Error updating task: {e}")
        return {
            "task_id": task_id,
            "status": "error",
            "error": str(e),
        }

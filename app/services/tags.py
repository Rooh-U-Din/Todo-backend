"""Tag service for Phase V tag management.

Provides CRUD operations for task tags and tag-task associations.
"""

import logging
from uuid import UUID

from sqlmodel import Session, select, func

from app.models.tag import TaskTag, TaskTagAssociation, TagCreate, TagUpdate

logger = logging.getLogger(__name__)


class TagNotFoundError(Exception):
    """Raised when a tag is not found."""
    pass


class TagValidationError(Exception):
    """Raised when tag validation fails."""
    pass


def create_tag(session: Session, user_id: UUID, tag_data: TagCreate) -> TaskTag:
    """Create a new tag for the user.

    Args:
        session: Database session
        user_id: The user ID
        tag_data: Tag creation data

    Returns:
        TaskTag: The created tag

    Raises:
        TagValidationError: If tag with same name exists
    """
    # Check for duplicate name
    existing = session.exec(
        select(TaskTag)
        .where(TaskTag.user_id == user_id)
        .where(func.lower(TaskTag.name) == tag_data.name.lower())
    ).first()

    if existing:
        raise TagValidationError(f"Tag '{tag_data.name}' already exists")

    tag = TaskTag(
        user_id=user_id,
        name=tag_data.name,
        color=tag_data.color,
    )
    session.add(tag)
    session.commit()
    session.refresh(tag)

    logger.info(
        "Tag created",
        extra={"tag_id": str(tag.id), "user_id": str(user_id), "name": tag.name},
    )

    return tag


def get_user_tags(
    session: Session,
    user_id: UUID,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[TaskTag], int]:
    """Get all tags for a user.

    Args:
        session: Database session
        user_id: The user ID
        limit: Maximum tags to return
        offset: Number of tags to skip

    Returns:
        tuple[list[TaskTag], int]: Tags and total count
    """
    query = (
        select(TaskTag)
        .where(TaskTag.user_id == user_id)
        .order_by(TaskTag.name)
        .offset(offset)
        .limit(limit)
    )
    count_query = (
        select(func.count())
        .select_from(TaskTag)
        .where(TaskTag.user_id == user_id)
    )

    tags = list(session.exec(query).all())
    total = session.exec(count_query).one()

    return tags, total


def get_tag_by_id(session: Session, user_id: UUID, tag_id: UUID) -> TaskTag | None:
    """Get a specific tag by ID.

    Args:
        session: Database session
        user_id: The user ID (for ownership check)
        tag_id: The tag ID

    Returns:
        TaskTag or None if not found
    """
    return session.exec(
        select(TaskTag)
        .where(TaskTag.id == tag_id)
        .where(TaskTag.user_id == user_id)
    ).first()


def update_tag(
    session: Session,
    user_id: UUID,
    tag_id: UUID,
    tag_data: TagUpdate,
) -> TaskTag:
    """Update a tag.

    Args:
        session: Database session
        user_id: The user ID
        tag_id: The tag ID
        tag_data: Update data

    Returns:
        TaskTag: The updated tag

    Raises:
        TagNotFoundError: If tag not found
        TagValidationError: If update would create duplicate name
    """
    tag = get_tag_by_id(session, user_id, tag_id)
    if not tag:
        raise TagNotFoundError(f"Tag {tag_id} not found")

    update_data = tag_data.model_dump(exclude_unset=True)

    # Check for duplicate name if name is being updated
    if "name" in update_data and update_data["name"].lower() != tag.name.lower():
        existing = session.exec(
            select(TaskTag)
            .where(TaskTag.user_id == user_id)
            .where(func.lower(TaskTag.name) == update_data["name"].lower())
            .where(TaskTag.id != tag_id)
        ).first()

        if existing:
            raise TagValidationError(f"Tag '{update_data['name']}' already exists")

    for key, value in update_data.items():
        setattr(tag, key, value)

    session.add(tag)
    session.commit()
    session.refresh(tag)

    logger.info("Tag updated", extra={"tag_id": str(tag_id)})

    return tag


def delete_tag(session: Session, user_id: UUID, tag_id: UUID) -> None:
    """Delete a tag and its associations.

    Args:
        session: Database session
        user_id: The user ID
        tag_id: The tag ID

    Raises:
        TagNotFoundError: If tag not found
    """
    tag = get_tag_by_id(session, user_id, tag_id)
    if not tag:
        raise TagNotFoundError(f"Tag {tag_id} not found")

    # Delete associations first
    associations = session.exec(
        select(TaskTagAssociation).where(TaskTagAssociation.tag_id == tag_id)
    ).all()

    for assoc in associations:
        session.delete(assoc)

    session.delete(tag)
    session.commit()

    logger.info("Tag deleted", extra={"tag_id": str(tag_id)})


def assign_tags_to_task(
    session: Session,
    user_id: UUID,
    task_id: UUID,
    tag_ids: list[UUID],
) -> list[TaskTag]:
    """Assign tags to a task (replaces existing assignments).

    Args:
        session: Database session
        user_id: The user ID
        task_id: The task ID
        tag_ids: List of tag IDs to assign

    Returns:
        list[TaskTag]: The assigned tags

    Raises:
        TagNotFoundError: If any tag not found
    """
    # Verify all tags belong to user
    tags = []
    for tag_id in tag_ids:
        tag = get_tag_by_id(session, user_id, tag_id)
        if not tag:
            raise TagNotFoundError(f"Tag {tag_id} not found")
        tags.append(tag)

    # Remove existing associations
    existing = session.exec(
        select(TaskTagAssociation).where(TaskTagAssociation.task_id == task_id)
    ).all()

    for assoc in existing:
        session.delete(assoc)

    # Create new associations
    for tag in tags:
        assoc = TaskTagAssociation(task_id=task_id, tag_id=tag.id)
        session.add(assoc)

    session.commit()

    logger.info(
        "Tags assigned to task",
        extra={"task_id": str(task_id), "tag_count": len(tags)},
    )

    return tags


def get_task_tags(session: Session, task_id: UUID) -> list[TaskTag]:
    """Get all tags assigned to a task.

    Args:
        session: Database session
        task_id: The task ID

    Returns:
        list[TaskTag]: Tags assigned to the task
    """
    associations = session.exec(
        select(TaskTagAssociation).where(TaskTagAssociation.task_id == task_id)
    ).all()

    tags = []
    for assoc in associations:
        tag = session.get(TaskTag, assoc.tag_id)
        if tag:
            tags.append(tag)

    return tags


def get_tasks_by_tag(session: Session, tag_id: UUID) -> list[UUID]:
    """Get all task IDs that have a specific tag.

    Args:
        session: Database session
        tag_id: The tag ID

    Returns:
        list[UUID]: Task IDs with this tag
    """
    associations = session.exec(
        select(TaskTagAssociation).where(TaskTagAssociation.tag_id == tag_id)
    ).all()

    return [assoc.task_id for assoc in associations]

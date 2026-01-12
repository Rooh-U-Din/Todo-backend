"""TaskTag entity models for Phase V."""

from datetime import datetime
from typing import Annotated, TYPE_CHECKING
from uuid import UUID, uuid4

from pydantic import StringConstraints
from sqlmodel import Field, SQLModel

if TYPE_CHECKING:
    from app.models.user import User

# Hex color pattern validation
HexColor = Annotated[str, StringConstraints(max_length=7, pattern=r"^#[0-9A-Fa-f]{6}$")]


class TaskTag(SQLModel, table=True):
    """Task tag database model."""

    __tablename__ = "task_tags"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="users.id", index=True)
    name: str = Field(max_length=50, index=True)
    color: str | None = Field(default=None, max_length=7)  # Hex color #RRGGBB
    created_at: datetime = Field(default_factory=datetime.utcnow)


class TaskTagAssociation(SQLModel, table=True):
    """Junction table for task-tag many-to-many relationship."""

    __tablename__ = "task_tag_associations"

    task_id: UUID = Field(foreign_key="tasks.id", primary_key=True)
    tag_id: UUID = Field(foreign_key="task_tags.id", primary_key=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class TagCreate(SQLModel):
    """Schema for tag creation."""

    name: str = Field(min_length=1, max_length=50)
    color: HexColor | None = None


class TagUpdate(SQLModel):
    """Schema for tag update."""

    name: str | None = Field(default=None, min_length=1, max_length=50)
    color: HexColor | None = None


class TagResponse(SQLModel):
    """Schema for tag response."""

    id: UUID
    name: str
    color: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class TagListResponse(SQLModel):
    """Schema for tag list response."""

    tags: list[TagResponse]
    total: int

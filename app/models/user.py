"""User entity model."""

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from pydantic import EmailStr
from sqlmodel import Field, Relationship, SQLModel

if TYPE_CHECKING:
    from app.models.task import Task


class UserBase(SQLModel):
    """Base User schema."""

    email: str = Field(max_length=255, unique=True, index=True)


class User(UserBase, table=True):
    """User database model."""

    __tablename__ = "users"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    hashed_password: str = Field(max_length=255)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    tasks: list["Task"] = Relationship(
        back_populates="user",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class UserCreate(SQLModel):
    """Schema for user registration."""

    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class UserLogin(SQLModel):
    """Schema for user login."""

    email: EmailStr
    password: str


class UserResponse(SQLModel):
    """Schema for user response (no password)."""

    id: UUID
    email: str
    created_at: datetime

    model_config = {"from_attributes": True}


class AuthResponse(SQLModel):
    """Schema for authentication response."""

    user: UserResponse
    token: str
    expires_at: datetime

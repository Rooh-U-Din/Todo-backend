"""Message entity model for Phase III AI Chatbot."""

from datetime import datetime
from typing import TYPE_CHECKING, Literal
from uuid import UUID, uuid4

from sqlmodel import Field, Relationship, SQLModel

if TYPE_CHECKING:
    from app.models.conversation import Conversation
    from app.models.user import User


class Message(SQLModel, table=True):
    """Message database model."""

    __tablename__ = "messages"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    conversation_id: UUID = Field(foreign_key="conversations.id", index=True)
    user_id: UUID = Field(foreign_key="users.id", index=True)
    role: str = Field(max_length=10)  # "user" or "assistant"
    content: str = Field(max_length=10000)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    conversation: "Conversation" = Relationship(back_populates="messages")
    user: "User" = Relationship()


class MessageCreate(SQLModel):
    """Schema for message creation."""

    role: Literal["user", "assistant"]
    content: str = Field(max_length=10000)


class MessageResponse(SQLModel):
    """Schema for message response."""

    id: UUID
    role: str
    content: str
    created_at: datetime

    model_config = {"from_attributes": True}

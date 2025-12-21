"""Conversation service for chat history management."""

from datetime import datetime
from uuid import UUID

from sqlmodel import Session, select

from app.models.conversation import Conversation
from app.models.message import Message


def get_or_create_conversation(session: Session, user_id: UUID) -> Conversation:
    """Get the most recent conversation or create a new one for the user."""
    conversation = session.exec(
        select(Conversation)
        .where(Conversation.user_id == user_id)
        .order_by(Conversation.updated_at.desc())
    ).first()

    if not conversation:
        conversation = Conversation(user_id=user_id)
        session.add(conversation)
        session.commit()
        session.refresh(conversation)

    return conversation


def get_conversation_by_id(
    session: Session, user_id: UUID, conversation_id: UUID
) -> Conversation | None:
    """Get a specific conversation owned by the user."""
    return session.exec(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == user_id,
        )
    ).first()


def get_user_conversations(
    session: Session, user_id: UUID, limit: int = 10, offset: int = 0
) -> list[Conversation]:
    """Get conversations for the user, ordered by most recent."""
    return list(
        session.exec(
            select(Conversation)
            .where(Conversation.user_id == user_id)
            .order_by(Conversation.updated_at.desc())
            .offset(offset)
            .limit(limit)
        ).all()
    )


def get_recent_messages(
    session: Session, conversation_id: UUID, limit: int = 50
) -> list[Message]:
    """Get the most recent messages for AI context, in chronological order."""
    messages = list(
        session.exec(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.desc())
            .limit(limit)
        ).all()
    )
    # Reverse to get chronological order
    return messages[::-1]


def create_message(
    session: Session,
    conversation_id: UUID,
    user_id: UUID,
    role: str,
    content: str,
) -> Message:
    """Store a new message and update conversation timestamp."""
    import logging
    logger = logging.getLogger(__name__)

    message = Message(
        conversation_id=conversation_id,
        user_id=user_id,
        role=role,
        content=content,
    )
    session.add(message)
    logger.info(f"Saving message: conversation={conversation_id}, role={role}, content={content[:50]}...")

    # Update conversation timestamp
    conversation = session.get(Conversation, conversation_id)
    if conversation:
        conversation.updated_at = datetime.utcnow()
        session.add(conversation)

    session.commit()
    session.refresh(message)
    return message


def get_messages_by_conversation(
    session: Session,
    conversation_id: UUID,
    user_id: UUID,
    limit: int = 50,
    offset: int = 0,
) -> list[Message]:
    """Get messages for a conversation, with pagination."""
    import logging
    logger = logging.getLogger(__name__)

    messages = list(
        session.exec(
            select(Message)
            .where(
                Message.conversation_id == conversation_id,
                Message.user_id == user_id,
            )
            .order_by(Message.created_at.asc())
            .offset(offset)
            .limit(limit)
        ).all()
    )
    logger.info(f"Fetching messages: conversation={conversation_id}, user={user_id}, found={len(messages)}")
    return messages

"""Chat API endpoints for Phase III AI Chatbot."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.api.deps import CurrentUser, DBSession
from app.models.conversation import ConversationResponse
from app.models.message import MessageResponse
from app.services.conversation import (
    get_conversation_by_id,
    get_messages_by_conversation,
    get_user_conversations,
)

router = APIRouter(prefix="/api", tags=["Chat"])


class ChatRequest(BaseModel):
    """Request body for chat endpoint."""

    message: str = Field(min_length=1, max_length=2000)
    conversation_id: UUID | None = None


class ChatResponse(BaseModel):
    """Response body for chat endpoint."""

    message: str
    conversation_id: UUID


@router.post("/{user_id}/chat", response_model=ChatResponse)
async def send_chat_message(
    user_id: UUID,
    request: ChatRequest,
    session: DBSession,
    current_user: CurrentUser,
) -> ChatResponse:
    """
    Send a chat message and receive an AI response.

    The AI assistant can help manage tasks through natural language:
    - Create tasks: "Add a task to buy groceries"
    - List tasks: "Show me my tasks"
    - Complete tasks: "Mark groceries as done"
    - Delete tasks: "Delete the groceries task"
    - Update tasks: "Rename groceries to buy organic groceries"
    """
    # Verify user ID matches authenticated user
    if current_user.id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User ID does not match authenticated user",
        )

    try:
        from app.services.chat import process_chat_message

        ai_response, conversation_id = await process_chat_message(
            session=session,
            user_id=user_id,
            message=request.message,
        )

        return ChatResponse(
            message=ai_response,
            conversation_id=conversation_id,
        )
    except Exception as e:
        # Log the error but return a user-friendly message
        import logging
        logging.error(f"Chat endpoint error: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Chat service is temporarily unavailable. Please try again later.",
        )


class ConversationListResponse(BaseModel):
    """Response body for conversations list."""

    conversations: list[ConversationResponse]
    total: int


class MessageListResponse(BaseModel):
    """Response body for messages list."""

    messages: list[MessageResponse]
    total: int


@router.get("/{user_id}/conversations", response_model=ConversationListResponse)
async def list_conversations(
    user_id: UUID,
    session: DBSession,
    current_user: CurrentUser,
    limit: int = Query(default=10, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
) -> ConversationListResponse:
    """Get the user's conversations, ordered by most recent activity."""
    if current_user.id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User ID does not match authenticated user",
        )

    conversations = get_user_conversations(
        session=session,
        user_id=user_id,
        limit=limit,
        offset=offset,
    )

    return ConversationListResponse(
        conversations=[
            ConversationResponse.model_validate(c) for c in conversations
        ],
        total=len(conversations),
    )


@router.get(
    "/{user_id}/conversations/{conversation_id}/messages",
    response_model=MessageListResponse,
)
async def list_messages(
    user_id: UUID,
    conversation_id: UUID,
    session: DBSession,
    current_user: CurrentUser,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> MessageListResponse:
    """Get messages in a conversation, ordered chronologically."""
    if current_user.id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User ID does not match authenticated user",
        )

    # Verify conversation belongs to user
    conversation = get_conversation_by_id(
        session=session,
        user_id=user_id,
        conversation_id=conversation_id,
    )
    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        )

    messages = get_messages_by_conversation(
        session=session,
        conversation_id=conversation_id,
        user_id=user_id,
        limit=limit,
        offset=offset,
    )

    return MessageListResponse(
        messages=[MessageResponse.model_validate(m) for m in messages],
        total=len(messages),
    )

"""Chat service for Gemini AI agent orchestration."""

import json
import logging
import sys
from uuid import UUID

# Configure logging to stdout for Railway
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

import google.generativeai as genai
from sqlmodel import Session

from app.config import get_settings
from app.mcp.tools import TOOL_DEFINITIONS, execute_tool
from app.services.conversation import (
    create_message,
    get_or_create_conversation,
    get_recent_messages,
)

settings = get_settings()

# Configure Gemini
genai.configure(api_key=settings.GEMINI_API_KEY)

# System prompt for the Todo AI assistant
SYSTEM_PROMPT = """You are a helpful AI assistant for task management. You help users manage their todo list through natural language conversation.

You can:
- Create new tasks when users say things like "add a task", "create a task", "remind me to", etc.
- List tasks when users ask "show my tasks", "what are my tasks", "list tasks", etc.
- Mark tasks as complete when users say "mark X as done", "complete X", "finish X", etc.
- Delete tasks when users say "delete X", "remove X", "get rid of X", etc.
- Update tasks when users say "rename X to Y", "change X", "update X", etc.

Always confirm actions you take with a friendly message. If a user's request is ambiguous, ask for clarification.

When listing tasks:
- Format them in a readable way with titles and status (completed or pending)
- If there are no tasks, suggest creating one

When a task operation fails (e.g., task not found), provide a helpful message without exposing technical details.

IMPORTANT: When the user asks to complete, delete, or update a task by name (not by ID), you should:
1. First call list_tasks to get the task IDs
2. Find the task that matches the user's description
3. Then call the appropriate function with the task_id

Always use the available functions to perform task operations. Do not make up task IDs."""

# Maximum turns for agent execution to prevent infinite loops
MAX_TURNS = 10


def _build_gemini_tools():
    """Convert tool definitions to Gemini function declarations."""
    function_declarations = []
    for tool in TOOL_DEFINITIONS:
        # Build proper Schema for parameters
        properties = {}
        required = tool["parameters"].get("required", [])

        for prop_name, prop_def in tool["parameters"].get("properties", {}).items():
            prop_schema = genai.protos.Schema(
                type=genai.protos.Type.STRING,
                description=prop_def.get("description", "")
            )
            # Handle enum type
            if "enum" in prop_def:
                prop_schema = genai.protos.Schema(
                    type=genai.protos.Type.STRING,
                    enum=prop_def["enum"],
                    description=prop_def.get("description", "")
                )
            properties[prop_name] = prop_schema

        func_decl = genai.protos.FunctionDeclaration(
            name=tool["name"],
            description=tool["description"],
            parameters=genai.protos.Schema(
                type=genai.protos.Type.OBJECT,
                properties=properties,
                required=required
            ) if properties else None
        )
        function_declarations.append(func_decl)

    logger.info(f"Built {len(function_declarations)} Gemini tools: {[f.name for f in function_declarations]}")
    return [genai.protos.Tool(function_declarations=function_declarations)]


def _create_model():
    """Create a Gemini model with function calling enabled."""
    tools = _build_gemini_tools()
    logger.info(f"Creating model with tools: {tools}")

    # Configure tool usage - ANY mode FORCES the model to use a tool
    tool_config = {
        "function_calling_config": {
            "mode": "ANY"
        }
    }

    return genai.GenerativeModel(
        model_name="gemini-2.5-flash-lite",
        tools=tools,
        tool_config=tool_config,
        system_instruction=SYSTEM_PROMPT,
    )


async def process_chat_message(
    session: Session, user_id: UUID, message: str
) -> tuple[str, UUID]:
    """
    Process a chat message and return the AI response.

    Returns:
        tuple[str, UUID]: The AI response message and conversation ID.
    """
    # Get or create conversation
    conversation = get_or_create_conversation(session, user_id)

    # Store user message
    create_message(
        session=session,
        conversation_id=conversation.id,
        user_id=user_id,
        role="user",
        content=message,
    )

    # Load recent messages for context (limit to 50)
    recent_messages = get_recent_messages(session, conversation.id, limit=50)

    # Build conversation history for Gemini
    history = []
    for msg in recent_messages[:-1]:  # Exclude the message we just added
        role = "user" if msg.role == "user" else "model"
        history.append({"role": role, "parts": [msg.content]})

    # Create model and chat
    model = _create_model()

    try:
        # Start chat with history
        chat = model.start_chat(history=history)

        # Send message and handle function calls
        ai_response = await _process_with_function_calling(
            chat=chat,
            message=message,
            user_id=str(user_id),
            session=session,
        )
    except Exception as e:
        logger.error(f"AI agent error: {e}")
        ai_response = "I'm sorry, I'm having trouble processing your request right now. Please try again later."

    # Store AI response
    create_message(
        session=session,
        conversation_id=conversation.id,
        user_id=user_id,
        role="assistant",
        content=ai_response,
    )

    return ai_response, conversation.id


async def _process_with_function_calling(
    chat,
    message: str,
    user_id: str,
    session: Session,
) -> str:
    """Process message with Gemini function calling loop."""
    logger.info(f"Sending message to Gemini: '{message}'")

    response = chat.send_message(message)
    logger.info(f"Initial Gemini response parts: {[type(p).__name__ for p in response.parts]}")

    # Function calling loop
    turn_count = 0
    while turn_count < MAX_TURNS:
        turn_count += 1

        # Check if there are function calls to process
        function_calls = []
        for part in response.parts:
            logger.info(f"Part attributes: {[attr for attr in dir(part) if not attr.startswith('_')]}")
            if hasattr(part, 'function_call') and part.function_call:
                logger.info(f"Found function_call: {part.function_call}")
                function_calls.append(part.function_call)

        if not function_calls:
            # No function calls, return the text response
            text_response = _extract_text_response(response)
            logger.info(f"No function calls in response, returning text: {text_response[:200] if text_response else 'empty'}")
            return text_response

        logger.info(f"Found {len(function_calls)} function call(s): {[fc.name for fc in function_calls]}")

        # Process all function calls
        function_responses = []
        for fc in function_calls:
            tool_name = fc.name
            args = dict(fc.args) if fc.args else {}

            logger.info(f"Executing tool: {tool_name} with args: {args}")

            # Execute the tool
            result = execute_tool(
                tool_name=tool_name,
                args=args,
                user_id=user_id,
                session=session,
            )

            logger.info(f"Tool result: {result}")

            function_responses.append({
                "name": tool_name,
                "response": result,
            })

        # Send function results back to Gemini
        response_parts = []
        for fr in function_responses:
            response_parts.append(
                genai.protos.Part(
                    function_response=genai.protos.FunctionResponse(
                        name=fr["name"],
                        response={"result": fr["response"]}
                    )
                )
            )

        response = chat.send_message(response_parts)

    # Max turns reached
    return _extract_text_response(response)


def _extract_text_response(response) -> str:
    """Extract text from Gemini response."""
    text_parts = []
    for part in response.parts:
        if hasattr(part, 'text') and part.text:
            text_parts.append(part.text)

    if text_parts:
        return " ".join(text_parts)

    return "I've processed your request."

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
SYSTEM_PROMPT = """You are a task management AI assistant. You MUST use the provided functions to manage tasks.

CRITICAL RULES:
1. You MUST call a function for ANY task operation. NEVER just say you did something without calling the function.
2. To add a task: ALWAYS call add_task function with the title
3. To list tasks: ALWAYS call list_tasks function
4. To complete a task: ALWAYS call complete_task function with task_id
5. To delete a task: ALWAYS call delete_task function with task_id
6. To update a task: ALWAYS call update_task function with task_id

Available operations:
- add_task: Create a new task (requires title)
- list_tasks: Show all tasks (optional status filter: all/pending/completed)
- complete_task: Mark task as done (requires task_id)
- delete_task: Remove a task (requires task_id)
- update_task: Change task title/description (requires task_id)

WORKFLOW FOR TASK BY NAME:
When user refers to a task by name (not UUID), you must:
1. First call list_tasks to get all tasks with their IDs
2. Find the matching task
3. Call the appropriate function with the task_id

NEVER pretend to perform an action. ALWAYS use the functions."""

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

    return [genai.protos.Tool(function_declarations=function_declarations)]


def _create_model():
    """Create a Gemini model with function calling enabled."""
    tools = _build_gemini_tools()

    # Configure tool usage - ANY mode forces function calling
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
    response = chat.send_message(message)

    # Track all function results for final response
    all_function_responses = []

    # Function calling loop
    turn_count = 0
    while turn_count < MAX_TURNS:
        turn_count += 1

        # Check if there are function calls to process
        function_calls = []
        for part in response.parts:
            if hasattr(part, 'function_call') and part.function_call:
                function_calls.append(part.function_call)

        if not function_calls:
            # No function calls - if we have accumulated results, return them
            if all_function_responses:
                return _generate_response_from_results(all_function_responses)
            # Otherwise return the text response
            return _extract_text_response(response)

        # Process all function calls
        function_response_parts = []
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

            logger.info(f"Tool {tool_name} result: {result}")

            all_function_responses.append({
                "name": tool_name,
                "response": result,
            })

            # Build function response for Gemini
            function_response_parts.append(
                genai.protos.Part(
                    function_response=genai.protos.FunctionResponse(
                        name=tool_name,
                        response={"result": result}
                    )
                )
            )

        # Check if this was a mutation (not list_tasks) - return immediately
        # For list_tasks, send results back to Gemini for follow-up actions
        has_mutation = any(
            fr["name"] in ("add_task", "complete_task", "delete_task", "update_task")
            for fr in all_function_responses
        )

        if has_mutation:
            # Mutation completed, return results to user
            return _generate_response_from_results(all_function_responses)

        # If only list_tasks was called and we've already done 2+ turns, return
        # This prevents the AI from making multiple list_tasks calls
        only_list_tasks = all(fr["name"] == "list_tasks" for fr in all_function_responses)
        if only_list_tasks and turn_count >= 2:
            logger.info(f"Returning after {turn_count} list_tasks calls to prevent duplicates")
            return _generate_response_from_results(all_function_responses)

        # Send function results back to Gemini for potential follow-up calls
        # This enables multi-step workflows like: list -> find -> complete
        try:
            response = chat.send_message(function_response_parts)
            logger.info(f"Gemini response after function result: parts={len(response.parts)}")
            for i, part in enumerate(response.parts):
                if hasattr(part, 'function_call') and part.function_call:
                    logger.info(f"  Part {i}: function_call={part.function_call.name}")
                elif hasattr(part, 'text') and part.text:
                    logger.info(f"  Part {i}: text={part.text[:100]}...")
        except Exception as e:
            logger.error(f"Error sending function response to Gemini: {e}")
            return _generate_response_from_results(all_function_responses)

    # Max turns reached
    return _generate_response_from_results(all_function_responses) if all_function_responses else _extract_text_response(response)


def _extract_text_response(response) -> str:
    """Extract text from Gemini response."""
    text_parts = []
    for part in response.parts:
        if hasattr(part, 'text') and part.text:
            text_parts.append(part.text)

    if text_parts:
        return " ".join(text_parts)

    return "I've processed your request."


def _generate_response_from_results(function_responses: list) -> str:
    """Generate a user-friendly response from function execution results.

    Only shows the LAST result for each tool type to avoid duplicates when
    the AI makes multiple calls to the same tool.
    """
    # Keep only the last result for each tool name to avoid duplicates
    seen_tools = {}
    for fr in function_responses:
        seen_tools[fr["name"]] = fr

    messages = []

    for fr in seen_tools.values():
        name = fr["name"]
        result = fr["response"]

        if name == "add_task":
            if result.get("status") == "created":
                messages.append(f"Created task: \"{result.get('title')}\"")
            else:
                messages.append(f"Failed to create task: {result.get('error', 'Unknown error')}")

        elif name == "list_tasks":
            tasks = result.get("tasks", [])
            count = result.get("count", 0)
            if count == 0:
                messages.append("You have no tasks. Would you like to add one?")
            else:
                task_lines = []
                for t in tasks:
                    status = "✓" if t.get("is_completed") else "○"
                    # Include short ID for user reference
                    short_id = t.get("id", "")[:8]
                    task_lines.append(f"  {status} [{short_id}] {t.get('title')}")
                messages.append(f"Your tasks ({count}):\n" + "\n".join(task_lines))

        elif name == "complete_task":
            if result.get("status") == "completed":
                messages.append(f"Marked \"{result.get('title')}\" as completed!")
            elif result.get("status") == "not_found":
                messages.append("Task not found. Use 'list tasks' to see your tasks.")
            else:
                messages.append(f"Failed to complete task: {result.get('error', 'Unknown error')}")

        elif name == "delete_task":
            if result.get("status") == "deleted":
                messages.append(f"Deleted task: \"{result.get('title')}\"")
            elif result.get("status") == "not_found":
                messages.append("Task not found. Use 'list tasks' to see your tasks.")
            else:
                messages.append(f"Failed to delete task: {result.get('error', 'Unknown error')}")

        elif name == "update_task":
            if result.get("status") == "updated":
                messages.append(f"Updated task: \"{result.get('title')}\"")
            elif result.get("status") == "not_found":
                messages.append("Task not found. Use 'list tasks' to see your tasks.")
            else:
                messages.append(f"Failed to update task: {result.get('error', 'Unknown error')}")

    return "\n".join(messages) if messages else "Done!"

"""MCP tools package for Phase III AI Chatbot.

This package contains Model Context Protocol tools that expose
task operations to the AI agent for natural language task management.
"""

from app.mcp.tools import TOOL_DEFINITIONS, execute_tool

__all__ = [
    "TOOL_DEFINITIONS",
    "execute_tool",
]

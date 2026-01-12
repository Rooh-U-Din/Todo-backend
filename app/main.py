"""FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlmodel import SQLModel

from app.api.auth import router as auth_router
from app.api.chat import router as chat_router
from app.api.tasks import router as tasks_router
from app.api.tags import router as tags_router
from app.config import get_settings
from app.db.session import engine

logger = logging.getLogger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create database tables on startup."""
    # Import models to register them with SQLModel
    from app.models import (  # noqa: F401
        Conversation, Message, Task, User,
        # Phase V models
        TaskReminder, TaskTag, TaskTagAssociation,
        TaskEvent, AuditLog, NotificationDelivery,
    )
    SQLModel.metadata.create_all(engine)
    yield

app = FastAPI(
    title="Todo Web Application API (Phase II)",
    description="RESTful API for the Full-Stack Todo Web Application",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS origins - include both configured URL and common deployment domains
cors_origins = [
    settings.FRONTEND_URL,
    "https://hackathon-ii-todo-spec-driven-devel.vercel.app",
    "http://localhost:3000",
    "http://localhost:3001",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:3001",
]
# Remove duplicates and empty strings
cors_origins = [origin for origin in set(cors_origins) if origin]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers
app.include_router(auth_router)
app.include_router(chat_router)
app.include_router(tasks_router)
app.include_router(tags_router)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Global exception handler to ensure CORS headers on all error responses.

    This prevents CORS errors from masking the actual server error.
    """
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


@app.get("/health")
def health_check() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "healthy"}

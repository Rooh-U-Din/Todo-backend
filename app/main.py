"""FastAPI application entry point."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import SQLModel

from app.api.auth import router as auth_router
from app.api.chat import router as chat_router
from app.api.tasks import router as tasks_router
from app.config import get_settings
from app.db.session import engine

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create database tables on startup."""
    # Import models to register them with SQLModel
    from app.models import Conversation, Message, Task, User  # noqa: F401
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


@app.get("/health")
def health_check() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "healthy"}

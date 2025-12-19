"""Environment configuration for the Todo Backend application."""

import os
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


class Settings:
    """Application settings loaded from environment variables."""

    def __init__(self) -> None:
        self.DATABASE_URL: str = os.getenv("DATABASE_URL", "")
        self.BETTER_AUTH_SECRET: str = os.getenv("BETTER_AUTH_SECRET", "")
        self.FRONTEND_URL: str = os.getenv("FRONTEND_URL", "http://localhost:3000")
        self.JWT_ALGORITHM: str = "HS256"
        self.JWT_EXPIRATION_HOURS: int = 24
        # Phase III: AI Chatbot configuration (using Gemini)
        self.GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")

    def validate(self) -> None:
        """Validate that required environment variables are set."""
        if not self.DATABASE_URL:
            raise ValueError("DATABASE_URL environment variable is required")
        if not self.BETTER_AUTH_SECRET:
            raise ValueError("BETTER_AUTH_SECRET environment variable is required")


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    settings = Settings()
    return settings

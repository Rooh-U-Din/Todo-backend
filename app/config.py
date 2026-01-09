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
        # Phase V: Dapr configuration
        self.DAPR_HTTP_PORT: int = int(os.getenv("DAPR_HTTP_PORT", "3500"))
        self.DAPR_PUBSUB_NAME: str = os.getenv("DAPR_PUBSUB_NAME", "taskpubsub")
        self.DAPR_TOPIC_NAME: str = os.getenv("DAPR_TOPIC_NAME", "task-events")
        self.EVENTS_ENABLED: bool = os.getenv("EVENTS_ENABLED", "true").lower() == "true"

        # Phase V Step 4: Worker configuration
        self.WORKER_BATCH_SIZE: int = int(os.getenv("WORKER_BATCH_SIZE", "50"))
        self.WORKER_MAX_RETRIES: int = int(os.getenv("WORKER_MAX_RETRIES", "3"))
        self.WORKER_RETRY_DELAY_SECONDS: int = int(os.getenv("WORKER_RETRY_DELAY_SECONDS", "60"))
        self.WORKER_POLL_INTERVAL_SECONDS: int = int(os.getenv("WORKER_POLL_INTERVAL_SECONDS", "5"))

        # Phase V Step 4: AI automation configuration
        self.AI_AUTOMATION_ENABLED: bool = os.getenv("AI_AUTOMATION_ENABLED", "false").lower() == "true"
        self.AI_CONFIDENCE_THRESHOLD: float = float(os.getenv("AI_CONFIDENCE_THRESHOLD", "0.8"))

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

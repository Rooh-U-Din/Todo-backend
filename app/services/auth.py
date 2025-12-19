"""Authentication service for user management and JWT generation."""

import re
from datetime import datetime, timedelta
from uuid import UUID

import bcrypt
from jose import jwt
from sqlmodel import Session, select

from app.config import get_settings
from app.models.user import AuthResponse, User, UserCreate, UserResponse

settings = get_settings()

# Password policy regex: at least one uppercase, one lowercase, one digit
PASSWORD_PATTERN = re.compile(r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d).{8,}$")

# Email validation (RFC 5322 simplified)
EMAIL_PATTERN = re.compile(
    r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
)


def hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    password_bytes = password.encode('utf-8')
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password_bytes, salt).decode('utf-8')


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash."""
    password_bytes = plain_password.encode('utf-8')
    hashed_bytes = hashed_password.encode('utf-8')
    return bcrypt.checkpw(password_bytes, hashed_bytes)


def validate_email(email: str) -> bool:
    """Validate email format (RFC 5322 simplified)."""
    return bool(EMAIL_PATTERN.match(email))


def validate_password_policy(password: str) -> tuple[bool, str]:
    """
    Validate password meets policy requirements.
    Returns (is_valid, error_message).
    """
    if len(password) < 8:
        return False, "Password must be at least 8 characters long"
    if not PASSWORD_PATTERN.match(password):
        return False, "Password must contain at least one uppercase letter, one lowercase letter, and one digit"
    return True, ""


def generate_jwt(user_id: UUID) -> tuple[str, datetime]:
    """
    Generate a JWT token for the user.
    Returns (token, expires_at).
    """
    expires_at = datetime.utcnow() + timedelta(hours=settings.JWT_EXPIRATION_HOURS)
    payload = {
        "sub": str(user_id),
        "exp": expires_at,
        "iat": datetime.utcnow(),
    }
    token = jwt.encode(payload, settings.BETTER_AUTH_SECRET, algorithm=settings.JWT_ALGORITHM)
    return token, expires_at


def get_user_by_email(session: Session, email: str) -> User | None:
    """Get a user by email address."""
    return session.exec(select(User).where(User.email == email)).first()


def create_user(session: Session, user_data: UserCreate) -> User:
    """
    Create a new user.
    Assumes email uniqueness and password policy are already validated.
    """
    hashed = hash_password(user_data.password)
    user = User(
        email=user_data.email,
        hashed_password=hashed,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def authenticate_user(session: Session, email: str, password: str) -> User | None:
    """
    Authenticate a user by email and password.
    Returns the user if valid, None otherwise.
    """
    user = get_user_by_email(session, email)
    if user is None:
        return None
    if not verify_password(password, user.hashed_password):
        return None
    return user


def create_auth_response(user: User) -> AuthResponse:
    """Create an authentication response with JWT token."""
    token, expires_at = generate_jwt(user.id)
    return AuthResponse(
        user=UserResponse.model_validate(user),
        token=token,
        expires_at=expires_at,
    )

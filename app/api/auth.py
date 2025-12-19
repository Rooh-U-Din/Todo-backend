"""Authentication API endpoints."""

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, DBSession
from app.models.user import AuthResponse, UserCreate, UserLogin
from app.services.auth import (
    authenticate_user,
    create_auth_response,
    create_user,
    get_user_by_email,
    validate_email,
    validate_password_policy,
)

router = APIRouter(prefix="/api/auth", tags=["Auth"])


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
def register_user(session: DBSession, user_data: UserCreate) -> AuthResponse:
    """Register a new user account."""
    # Validate email format
    if not validate_email(user_data.email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid email format",
        )

    # Validate password policy
    is_valid, error_msg = validate_password_policy(user_data.password)
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error_msg,
        )

    # Check email uniqueness
    existing_user = get_user_by_email(session, user_data.email)
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    # Create user
    user = create_user(session, user_data)

    # Generate JWT and return
    return create_auth_response(user)


@router.post("/login", response_model=AuthResponse)
def login_user(session: DBSession, credentials: UserLogin) -> AuthResponse:
    """Sign in with email and password."""
    user = authenticate_user(session, credentials.email, credentials.password)
    if user is None:
        # Generic error message to prevent enumeration
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    return create_auth_response(user)


@router.post("/logout")
def logout_user(current_user: CurrentUser) -> dict[str, str]:
    """Sign out (invalidate session)."""
    # JWT is stateless, so logout is handled client-side by discarding the token.
    # This endpoint exists for API completeness and future session management.
    return {"message": "Logged out successfully"}

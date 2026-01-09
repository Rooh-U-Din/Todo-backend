"""Tags API endpoints for Phase V."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import CurrentUser, DBSession
from app.models.tag import TagCreate, TagUpdate, TagResponse, TagListResponse
from app.services.tags import (
    TagNotFoundError,
    TagValidationError,
    create_tag,
    delete_tag,
    get_tag_by_id,
    get_user_tags,
    update_tag,
)

router = APIRouter(prefix="/api/tags", tags=["Tags"])


@router.post("", response_model=TagResponse, status_code=status.HTTP_201_CREATED)
def create_tag_endpoint(
    session: DBSession,
    current_user: CurrentUser,
    tag_data: TagCreate,
) -> TagResponse:
    """Create a new tag for the authenticated user."""
    try:
        tag = create_tag(session, current_user.id, tag_data)
        return TagResponse.model_validate(tag)
    except TagValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.get("", response_model=TagListResponse)
def list_tags_endpoint(
    session: DBSession,
    current_user: CurrentUser,
    limit: int = Query(default=100, ge=1, le=500, description="Maximum tags to return"),
    offset: int = Query(default=0, ge=0, description="Number of tags to skip"),
) -> TagListResponse:
    """List all tags for the authenticated user."""
    tags, total = get_user_tags(session, current_user.id, limit, offset)
    return TagListResponse(
        tags=[TagResponse.model_validate(t) for t in tags],
        total=total,
    )


@router.get("/{tag_id}", response_model=TagResponse)
def get_tag_endpoint(
    session: DBSession,
    current_user: CurrentUser,
    tag_id: UUID,
) -> TagResponse:
    """Get a specific tag by ID."""
    tag = get_tag_by_id(session, current_user.id, tag_id)
    if tag is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tag not found",
        )
    return TagResponse.model_validate(tag)


@router.patch("/{tag_id}", response_model=TagResponse)
def update_tag_endpoint(
    session: DBSession,
    current_user: CurrentUser,
    tag_id: UUID,
    tag_data: TagUpdate,
) -> TagResponse:
    """Update a tag."""
    try:
        tag = update_tag(session, current_user.id, tag_id, tag_data)
        return TagResponse.model_validate(tag)
    except TagNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tag not found",
        )
    except TagValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.delete("/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_tag_endpoint(
    session: DBSession,
    current_user: CurrentUser,
    tag_id: UUID,
) -> None:
    """Delete a tag."""
    try:
        delete_tag(session, current_user.id, tag_id)
    except TagNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tag not found",
        )

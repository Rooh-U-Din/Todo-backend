"""AuditLog entity model for Phase V audit logging."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlmodel import Field, SQLModel, Column
from sqlalchemy.dialects.postgresql import JSONB


class AuditLog(SQLModel, table=True):
    """Audit log database model for immutable activity records."""

    __tablename__ = "audit_logs"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="users.id", index=True)
    action: str = Field(max_length=50, index=True)
    entity_type: str = Field(max_length=50, index=True)
    entity_id: UUID | None = Field(default=None, index=True)
    details: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB))
    ip_address: str | None = Field(default=None, max_length=45)
    user_agent: str | None = Field(default=None, max_length=500)
    timestamp: datetime = Field(default_factory=datetime.utcnow, index=True)


class AuditLogCreate(SQLModel):
    """Schema for audit log creation."""

    user_id: UUID
    action: str = Field(max_length=50)
    entity_type: str = Field(max_length=50)
    entity_id: UUID | None = None
    details: dict[str, Any] | None = None
    ip_address: str | None = None
    user_agent: str | None = None


class AuditLogResponse(SQLModel):
    """Schema for audit log response."""

    id: UUID
    user_id: UUID
    action: str
    entity_type: str
    entity_id: UUID | None
    details: dict[str, Any] | None
    ip_address: str | None
    user_agent: str | None
    timestamp: datetime

    model_config = {"from_attributes": True}


class AuditLogListResponse(SQLModel):
    """Schema for audit log list response."""

    logs: list[AuditLogResponse]
    total: int

"""
API Request Validation.

Uses Pydantic for request payload validation.
"""

from typing import Optional

from pydantic import BaseModel, Field, field_validator


class ScheduleFollowupsRequest(BaseModel):
    """Request body for /schedule-followups endpoint."""
    
    draft_id: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="The Firestore document ID of the draft",
    )
    
    @field_validator("draft_id")
    @classmethod
    def validate_draft_id(cls, v: str) -> str:
        """Validate draft_id format."""
        v = v.strip()
        if not v:
            raise ValueError("draft_id cannot be empty or whitespace")
        if "/" in v or "\\" in v:
            raise ValueError("draft_id cannot contain path separators")
        return v


class CancelFollowupsRequest(BaseModel):
    """Request body for /cancel-followups endpoint."""
    
    draft_id: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="The Firestore document ID of the draft",
    )
    
    @field_validator("draft_id")
    @classmethod
    def validate_draft_id(cls, v: str) -> str:
        """Validate draft_id format."""
        v = v.strip()
        if not v:
            raise ValueError("draft_id cannot be empty or whitespace")
        if "/" in v or "\\" in v:
            raise ValueError("draft_id cannot contain path separators")
        return v


class ProcessFollowupsRequest(BaseModel):
    """Request body for /process-pending-followups endpoint."""
    
    limit: Optional[int] = Field(
        default=None,
        ge=1,
        le=1000,
        description="Maximum number of followups to process",
    )
    dry_run: bool = Field(
        default=False,
        description="If true, only simulate processing without making changes",
    )


class RetryFollowupsRequest(BaseModel):
    """Request body for /retry-failed-followups endpoint."""
    
    limit: Optional[int] = Field(
        default=None,
        ge=1,
        le=100,
        description="Maximum number of followups to retry",
    )

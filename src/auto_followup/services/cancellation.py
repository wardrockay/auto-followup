"""
Followup Cancellation Service.

Handles cancellation of pending followup tasks.
"""

from dataclasses import dataclass
from typing import Optional

from auto_followup.core.exceptions import DraftNotFoundError
from auto_followup.infrastructure.firestore import (
    DraftRepository,
    FollowupRepository,
)
from auto_followup.infrastructure.logging import get_logger, log_duration


logger = get_logger(__name__)


@dataclass(frozen=True)
class CancellationResult:
    """Result of a followup cancellation operation."""
    draft_id: str
    cancelled_count: int
    success: bool
    message: str


class CancellationService:
    """
    Service for cancelling followup tasks.
    
    Responsible for:
    - Cancelling pending followups for a specific draft
    - Validating draft existence before cancellation
    """
    
    def __init__(
        self,
        draft_repository: Optional[DraftRepository] = None,
        followup_repository: Optional[FollowupRepository] = None,
    ) -> None:
        self._draft_repo = draft_repository or DraftRepository()
        self._followup_repo = followup_repository or FollowupRepository()
    
    @log_duration("cancel_followups")
    def cancel_for_draft(self, draft_id: str) -> CancellationResult:
        """
        Cancel all pending followups for a specific draft.
        
        Args:
            draft_id: The draft document ID.
            
        Returns:
            CancellationResult with cancellation outcome.
            
        Raises:
            DraftNotFoundError: If draft doesn't exist.
        """
        logger.info(
            f"Cancelling followups for draft {draft_id}",
            extra={"extra_fields": {"draft_id": draft_id}}
        )
        
        if not self._draft_repo.exists(draft_id):
            raise DraftNotFoundError(draft_id)
        
        cancelled_count = self._followup_repo.cancel_pending_for_draft(draft_id)
        
        if cancelled_count == 0:
            message = f"No pending followups found for draft {draft_id}"
        else:
            message = f"Cancelled {cancelled_count} followup(s) for draft {draft_id}"
        
        logger.info(
            message,
            extra={"extra_fields": {
                "draft_id": draft_id,
                "cancelled_count": cancelled_count,
            }}
        )
        
        return CancellationResult(
            draft_id=draft_id,
            cancelled_count=cancelled_count,
            success=True,
            message=message,
        )

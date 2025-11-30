"""
Firestore Infrastructure Package.

Exports:
- Data models (EmailDraft, FollowupTask, etc.)
- Repositories (DraftRepository, FollowupRepository)
"""

from auto_followup.infrastructure.firestore.models import (
    EmailDraft,
    FollowupStatus,
    FollowupTask,
    ProcessingResult,
    ScheduleResult,
)
from auto_followup.infrastructure.firestore.repositories import (
    DraftRepository,
    FirestoreClient,
    FollowupRepository,
)


__all__ = [
    # Models
    "EmailDraft",
    "FollowupStatus",
    "FollowupTask",
    "ProcessingResult",
    "ScheduleResult",
    # Repositories
    "DraftRepository",
    "FirestoreClient",
    "FollowupRepository",
]

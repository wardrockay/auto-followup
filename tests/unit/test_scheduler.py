"""
Tests for Scheduler Service.

Tests the followup scheduling logic.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from auto_followup.core.exceptions import (
    DraftNotFoundError,
    DraftNotSentError,
    MissingSentAtError,
)
from auto_followup.infrastructure.firestore import (
    EmailDraft,
    FollowupStatus,
)
from auto_followup.services.scheduler import SchedulerService


class TestSchedulerService:
    """Tests for SchedulerService."""
    
    @pytest.fixture
    def mock_draft_repo(self):
        """Create mock draft repository."""
        return MagicMock()
    
    @pytest.fixture
    def mock_followup_repo(self):
        """Create mock followup repository."""
        return MagicMock()
    
    @pytest.fixture
    def service(self, mock_draft_repo, mock_followup_repo):
        """Create scheduler service with mock repositories."""
        return SchedulerService(
            draft_repository=mock_draft_repo,
            followup_repository=mock_followup_repo,
        )
    
    def test_schedule_for_sent_draft_creates_followups(
        self,
        service,
        mock_draft_repo,
        mock_followup_repo,
        sample_draft,
    ):
        """Should create followups for a sent draft."""
        mock_draft_repo.get_by_id.return_value = sample_draft
        mock_followup_repo.has_existing_followups.return_value = False
        mock_followup_repo.create_batch.return_value = [
            "followup-1",
            "followup-2",
            "followup-3",
            "followup-4",
        ]
        
        result = service.schedule_for_draft("draft-123")
        
        assert result.success
        assert result.scheduled_count == 4
        assert len(result.followup_ids) == 4
        mock_followup_repo.create_batch.assert_called_once()
    
    def test_schedule_for_draft_already_scheduled_skips(
        self,
        service,
        mock_draft_repo,
        mock_followup_repo,
        sample_draft,
    ):
        """Should skip scheduling if followups already exist."""
        mock_draft_repo.get_by_id.return_value = sample_draft
        mock_followup_repo.has_existing_followups.return_value = True
        
        result = service.schedule_for_draft("draft-123")
        
        assert not result.success
        assert result.scheduled_count == 0
        assert "already scheduled" in result.skipped_reason.lower()
        mock_followup_repo.create_batch.assert_not_called()
    
    def test_schedule_for_unsent_draft_raises_error(
        self,
        service,
        mock_draft_repo,
        sample_unsent_draft,
    ):
        """Should raise DraftNotSentError for unsent draft."""
        mock_draft_repo.get_by_id.return_value = sample_unsent_draft
        
        with pytest.raises(DraftNotSentError):
            service.schedule_for_draft("draft-789")
    
    def test_schedule_for_nonexistent_draft_raises_error(
        self,
        service,
        mock_draft_repo,
    ):
        """Should raise DraftNotFoundError for nonexistent draft."""
        mock_draft_repo.get_by_id.side_effect = DraftNotFoundError("draft-999")
        
        with pytest.raises(DraftNotFoundError):
            service.schedule_for_draft("draft-999")
    
    def test_schedule_for_draft_without_sent_at_raises_error(
        self,
        service,
        mock_draft_repo,
    ):
        """Should raise MissingSentAtError for draft without sent_at."""
        draft_no_sent_at = EmailDraft(
            doc_id="draft-123",
            draft_status="sent",
            sent_at=None,
            raw_data={},
        )
        mock_draft_repo.get_by_id.return_value = draft_no_sent_at
        
        with pytest.raises(MissingSentAtError):
            service.schedule_for_draft("draft-123")

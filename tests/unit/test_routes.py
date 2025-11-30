"""
Tests for API Routes.

Tests the Flask HTTP endpoints.
"""

from unittest.mock import MagicMock, patch

import pytest

from auto_followup.core.exceptions import DraftNotFoundError, DraftNotSentError
from auto_followup.infrastructure.firestore import ScheduleResult


class TestHealthEndpoint:
    """Tests for health check endpoint."""
    
    def test_health_returns_healthy(self, client):
        """Health endpoint should return healthy status."""
        response = client.get("/health")
        
        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert data["status"] == "healthy"


class TestScheduleFollowupsEndpoint:
    """Tests for schedule-followups endpoint."""
    
    def test_schedule_requires_draft_id(self, client):
        """Should return 400 when draft_id is missing."""
        response = client.post("/schedule-followups", json={})
        
        assert response.status_code == 400
        data = response.get_json()
        assert data["success"] is False
        assert "draft_id" in data["error"].lower()
    
    @patch("auto_followup.api.routes.SchedulerService")
    def test_schedule_returns_success(self, mock_service_class, client):
        """Should return success when scheduling succeeds."""
        mock_service = MagicMock()
        mock_service_class.return_value = mock_service
        mock_service.schedule_for_draft.return_value = ScheduleResult(
            draft_id="draft-123",
            scheduled_count=4,
            followup_ids=["f1", "f2", "f3", "f4"],
        )
        
        response = client.post(
            "/schedule-followups",
            json={"draft_id": "draft-123"},
        )
        
        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert data["scheduled_count"] == 4
    
    @patch("auto_followup.api.routes.SchedulerService")
    def test_schedule_returns_404_for_missing_draft(
        self,
        mock_service_class,
        client,
    ):
        """Should return 404 when draft not found."""
        mock_service = MagicMock()
        mock_service_class.return_value = mock_service
        mock_service.schedule_for_draft.side_effect = DraftNotFoundError("draft-999")
        
        response = client.post(
            "/schedule-followups",
            json={"draft_id": "draft-999"},
        )
        
        assert response.status_code == 404
        data = response.get_json()
        assert data["success"] is False


class TestCancelFollowupsEndpoint:
    """Tests for cancel-followups endpoint."""
    
    def test_cancel_requires_draft_id(self, client):
        """Should return 400 when draft_id is missing."""
        response = client.post("/cancel-followups", json={})
        
        assert response.status_code == 400
        data = response.get_json()
        assert data["success"] is False


class TestProcessPendingFollowupsEndpoint:
    """Tests for process-pending-followups endpoint."""
    
    @patch("auto_followup.api.routes.ProcessorService")
    def test_process_returns_results_summary(self, mock_service_class, client):
        """Should return processing results summary."""
        mock_service = MagicMock()
        mock_service_class.return_value = mock_service
        mock_service.process_due_followups.return_value = []
        
        response = client.post("/process-pending-followups")
        
        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert "processed_count" in data

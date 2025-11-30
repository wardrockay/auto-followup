"""
Test Configuration and Fixtures.

Provides shared fixtures for all tests.
"""

from datetime import datetime, timezone
from typing import Generator
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask
from flask.testing import FlaskClient

from auto_followup.app import create_app
from auto_followup.infrastructure.firestore import (
    EmailDraft,
    FollowupStatus,
    FollowupTask,
)


@pytest.fixture
def app() -> Flask:
    """Create test Flask application."""
    test_config = {
        "TESTING": True,
    }
    return create_app(test_config)


@pytest.fixture
def client(app: Flask) -> FlaskClient:
    """Create test client."""
    return app.test_client()


@pytest.fixture
def mock_firestore() -> Generator[MagicMock, None, None]:
    """Mock Firestore client."""
    with patch(
        "auto_followup.infrastructure.firestore.repositories.FirestoreClient.get_client"
    ) as mock:
        mock_client = MagicMock()
        mock.return_value = mock_client
        yield mock_client


@pytest.fixture
def sample_draft() -> EmailDraft:
    """Create a sample email draft for testing."""
    return EmailDraft(
        doc_id="draft-123",
        odoo_contact_id="contact-456",
        sent_at=datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc),
        draft_status="sent",
        recipient_email="test@example.com",
        company_name="Test Company",
        contact_first_name="John",
        raw_data={},
    )


@pytest.fixture
def sample_unsent_draft() -> EmailDraft:
    """Create a sample unsent draft for testing."""
    return EmailDraft(
        doc_id="draft-789",
        odoo_contact_id="contact-456",
        sent_at=None,
        draft_status="draft",
        recipient_email="test@example.com",
        company_name="Test Company",
        contact_first_name="John",
        raw_data={},
    )


@pytest.fixture
def sample_followup_task() -> FollowupTask:
    """Create a sample followup task for testing."""
    return FollowupTask(
        doc_id="followup-001",
        draft_id="draft-123",
        followup_number=1,
        days_after_sent=3,
        scheduled_date=datetime(2024, 1, 18, 10, 0, 0, tzinfo=timezone.utc),
        status=FollowupStatus.PENDING,
        created_at=datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc),
    )


@pytest.fixture
def sample_failed_followup_task() -> FollowupTask:
    """Create a sample failed followup task for testing."""
    return FollowupTask(
        doc_id="followup-002",
        draft_id="draft-123",
        followup_number=1,
        days_after_sent=3,
        scheduled_date=datetime(2024, 1, 18, 10, 0, 0, tzinfo=timezone.utc),
        status=FollowupStatus.FAILED,
        created_at=datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc),
        error_message="Mail-writer timeout",
    )

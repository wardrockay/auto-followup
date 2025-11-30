"""Configuration package."""

from auto_followup.config.settings import (
    Settings,
    FirestoreSettings,
    OdooSettings,
    MailWriterSettings,
    FollowupScheduleSettings,
    settings,
)

__all__ = [
    "Settings",
    "FirestoreSettings",
    "OdooSettings",
    "MailWriterSettings",
    "FollowupScheduleSettings",
    "settings",
]

"""
Services Layer.

Business logic orchestration:
- Followup scheduling
- Followup cancellation
- Followup processing
- Retry failed operations
"""

from auto_followup.services.cancellation import (
    CancellationResult,
    CancellationService,
)
from auto_followup.services.processor import ProcessorService
from auto_followup.services.retry import RetryService
from auto_followup.services.scheduler import SchedulerService


__all__ = [
    "CancellationResult",
    "CancellationService",
    "ProcessorService",
    "RetryService",
    "SchedulerService",
]

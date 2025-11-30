"""
Retry Service.

Handles retrying failed followup tasks.
"""

from typing import List, Optional

from auto_followup.infrastructure.firestore import (
    FollowupRepository,
    FollowupStatus,
    ProcessingResult,
)
from auto_followup.infrastructure.logging import get_logger, log_duration
from auto_followup.services.processor import ProcessorService


logger = get_logger(__name__)


class RetryService:
    """
    Service for retrying failed followup tasks.
    
    Responsible for:
    - Finding failed followups
    - Resetting status to pending
    - Triggering reprocessing
    """
    
    def __init__(
        self,
        followup_repository: Optional[FollowupRepository] = None,
        processor_service: Optional[ProcessorService] = None,
    ) -> None:
        self._followup_repo = followup_repository or FollowupRepository()
        self._processor = processor_service or ProcessorService()
    
    @log_duration("retry_failed_followups")
    def retry_all_failed(self) -> List[ProcessingResult]:
        """
        Retry all failed followup tasks.
        
        Returns:
            List of ProcessingResult for each retried followup.
        """
        results: List[ProcessingResult] = []
        
        logger.info("Starting retry of failed followups")
        
        failed_tasks = list(self._followup_repo.get_failed_followups())
        
        if not failed_tasks:
            logger.info("No failed followups to retry")
            return results
        
        logger.info(
            f"Found {len(failed_tasks)} failed followups to retry",
            extra={"extra_fields": {"count": len(failed_tasks)}}
        )
        
        for task in failed_tasks:
            self._followup_repo.update_status(
                task.doc_id,
                FollowupStatus.PENDING,
            )
            
            result = self._processor.process_followup(task)
            results.append(result)
        
        success_count = sum(1 for r in results if r.success)
        failure_count = len(results) - success_count
        
        logger.info(
            f"Retry complete: {success_count} success, {failure_count} still failed",
            extra={"extra_fields": {
                "total_retried": len(results),
                "success_count": success_count,
                "failure_count": failure_count,
            }}
        )
        
        return results

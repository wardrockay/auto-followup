"""
Flask API Routes.

Defines all HTTP endpoints for the followup service.
"""

from typing import Any, Dict, Tuple

from flask import Blueprint, request
from pydantic import ValidationError as PydanticValidationError

from auto_followup.api.rate_limiting import rate_limit
from auto_followup.api.validation import (
    CancelFollowupsRequest,
    ScheduleFollowupsRequest,
)
from auto_followup.core.exceptions import (
    BusinessError,
    DraftNotFoundError,
    DraftNotSentError,
    ExternalServiceError,
    MissingSentAtError,
    ValidationError,
)
from auto_followup.infrastructure.circuit_breaker import CircuitBreakerOpenError
from auto_followup.infrastructure.logging import get_logger
from auto_followup.infrastructure.metrics import get_metrics, metrics_endpoint
from auto_followup.services import (
    CancellationService,
    ProcessorService,
    RetryService,
    SchedulerService,
)


logger = get_logger(__name__)


api_bp = Blueprint("api", __name__)


def _error_response(
    message: str,
    status_code: int,
    error_type: str = "error",
) -> Tuple[Dict[str, Any], int]:
    """Create standardized error response."""
    return {
        "success": False,
        "error": message,
        "error_type": error_type,
    }, status_code


def _success_response(
    data: Dict[str, Any],
    status_code: int = 200,
) -> Tuple[Dict[str, Any], int]:
    """Create standardized success response."""
    return {
        "success": True,
        **data,
    }, status_code


# ============================================================================
# Health Check
# ============================================================================

@api_bp.route("/health", methods=["GET"])
def health_check() -> Tuple[Dict[str, Any], int]:
    """
    Health check endpoint for Cloud Run.
    
    Cloud Run uses this endpoint for:
    - Startup probes: Verify the container started successfully
    - Liveness probes: Verify the container is still running
    
    Returns:
        Health status response.
    """
    return _success_response({
        "status": "healthy",
        "service": "auto-followup",
        "version": "1.0.0",
    })


@api_bp.route("/", methods=["GET"])
def root() -> Tuple[Dict[str, Any], int]:
    """
    Root endpoint - redirects to health for Cloud Run default checks.
    """
    return health_check()


@api_bp.route("/metrics", methods=["GET"])
def metrics() -> Tuple[Dict[str, Any], int]:
    """
    Prometheus metrics endpoint.
    """
    return metrics_endpoint()


# ============================================================================
# Scheduling Endpoints
# ============================================================================

@api_bp.route("/schedule-missing-followups", methods=["POST"])
@rate_limit
def schedule_missing_followups() -> Tuple[Dict[str, Any], int]:
    """
    Schedule followups for all sent drafts without any followup scheduled.
    
    This endpoint finds all prospection emails that were sent but don't have
    any followup tasks scheduled yet, and creates the followup schedule for them.
    
    Returns:
        Summary of scheduling results.
    """
    try:
        scheduler = SchedulerService()
        results = scheduler.schedule_all_sent_drafts()
        
        # Count successes and failures
        success_count = sum(1 for r in results if r.success)
        skipped_count = len(results) - success_count
        total_scheduled = sum(r.scheduled_count for r in results)
        
        # Record metrics
        if total_scheduled > 0:
            get_metrics().followups_scheduled_total.inc(total_scheduled)
        
        return _success_response({
            "processed_drafts": len(results),
            "drafts_with_followups_added": success_count,
            "drafts_skipped": skipped_count,
            "total_followups_scheduled": total_scheduled,
            "results": [
                {
                    "draft_id": r.draft_id,
                    "scheduled_count": r.scheduled_count,
                    "followup_ids": r.followup_ids,
                    "skipped_reason": r.skipped_reason,
                }
                for r in results
            ],
        })
        
    except ExternalServiceError as e:
        logger.error(
            f"External service error during bulk scheduling: {e}",
            extra={"extra_fields": {"error_type": type(e).__name__}}
        )
        return _error_response(str(e), 503, "external_service_error")


@api_bp.route("/schedule-followups", methods=["POST"])
@rate_limit
def schedule_followups() -> Tuple[Dict[str, Any], int]:
    """
    Schedule followups for a sent draft.
    
    Request Body:
        draft_id (str): The draft document ID.
        
    Returns:
        Scheduling result.
    """
    data = request.get_json() or {}
    
    # Validate request with Pydantic
    try:
        validated = ScheduleFollowupsRequest(**data)
    except PydanticValidationError as e:
        return _error_response(
            str(e.errors()[0]["msg"]),
            400,
            "validation_error",
        )
    
    try:
        scheduler = SchedulerService()
        result = scheduler.schedule_for_draft(validated.draft_id)
        
        # Record metrics
        if result.scheduled_count > 0:
            get_metrics().followups_scheduled_total.inc(result.scheduled_count)
        
        return _success_response({
            "draft_id": result.draft_id,
            "scheduled_count": result.scheduled_count,
            "followup_ids": result.followup_ids,
            "skipped_reason": result.skipped_reason,
        })
        
    except DraftNotFoundError as e:
        return _error_response(str(e), 404, "draft_not_found")
    except DraftNotSentError as e:
        return _error_response(str(e), 400, "draft_not_sent")
    except MissingSentAtError as e:
        return _error_response(str(e), 400, "missing_sent_at")


# ============================================================================
# Cancellation Endpoints
# ============================================================================

@api_bp.route("/cancel-followups", methods=["POST"])
@rate_limit
def cancel_followups() -> Tuple[Dict[str, Any], int]:
    """
    Cancel pending followups for a draft.
    
    Request Body:
        draft_id (str): The draft document ID.
        
    Returns:
        Cancellation result.
    """
    data = request.get_json() or {}
    
    # Validate request with Pydantic
    try:
        validated = CancelFollowupsRequest(**data)
    except PydanticValidationError as e:
        return _error_response(
            str(e.errors()[0]["msg"]),
            400,
            "validation_error",
        )
    
    try:
        cancellation = CancellationService()
        result = cancellation.cancel_for_draft(validated.draft_id)
        
        # Record metrics
        if result.cancelled_count > 0:
            get_metrics().followups_cancelled_total.inc(result.cancelled_count)
        
        return _success_response({
            "draft_id": result.draft_id,
            "cancelled_count": result.cancelled_count,
            "message": result.message,
        })
        
    except DraftNotFoundError as e:
        return _error_response(str(e), 404, "draft_not_found")


# ============================================================================
# Processing Endpoints
# ============================================================================

@api_bp.route("/process-pending-followups", methods=["POST"])
@rate_limit
def process_pending_followups() -> Tuple[Dict[str, Any], int]:
    """
    Process all followups that are due.
    
    Returns:
        Processing results summary.
    """
    try:
        processor = ProcessorService()
        results = processor.process_due_followups()
        
        success_count = sum(1 for r in results if r.success)
        failure_count = len(results) - success_count
        
        # Record metrics
        metrics = get_metrics()
        metrics.followups_processed_total.inc(success_count, status="success")
        metrics.followups_failed_total.inc(failure_count)
        
        return _success_response({
            "processed_count": len(results),
            "success_count": success_count,
            "failure_count": failure_count,
            "results": [
                {
                    "followup_id": r.followup_id,
                    "draft_id": r.draft_id,
                    "followup_number": r.followup_number,
                    "success": r.success,
                    "error_message": r.error_message,
                }
                for r in results
            ],
        })
        
    except CircuitBreakerOpenError as e:
        logger.warning(
            f"Circuit breaker open: {e}",
            extra={"extra_fields": {"error_type": "circuit_breaker_open"}}
        )
        return _error_response(str(e), 503, "circuit_breaker_open")
    except ExternalServiceError as e:
        logger.error(
            f"External service error during processing: {e}",
            extra={"extra_fields": {"error_type": type(e).__name__}}
        )
        return _error_response(str(e), 503, "external_service_error")


# ============================================================================
# Retry Endpoints
# ============================================================================

@api_bp.route("/retry-failed-followups", methods=["POST"])
@rate_limit
def retry_failed_followups() -> Tuple[Dict[str, Any], int]:
    """
    Retry all failed followup tasks.
    
    Returns:
        Retry results summary.
    """
    try:
        retry_service = RetryService()
        results = retry_service.retry_all_failed()
        
        success_count = sum(1 for r in results if r.success)
        failure_count = len(results) - success_count
        
        # Record metrics
        metrics = get_metrics()
        metrics.followups_processed_total.inc(success_count, status="retried")
        
        return _success_response({
            "retried_count": len(results),
            "success_count": success_count,
            "failure_count": failure_count,
            "results": [
                {
                    "followup_id": r.followup_id,
                    "draft_id": r.draft_id,
                    "followup_number": r.followup_number,
                    "success": r.success,
                    "error_message": r.error_message,
                }
                for r in results
            ],
        })
        
    except CircuitBreakerOpenError as e:
        logger.warning(
            f"Circuit breaker open: {e}",
            extra={"extra_fields": {"error_type": "circuit_breaker_open"}}
        )
        return _error_response(str(e), 503, "circuit_breaker_open")
    except ExternalServiceError as e:
        logger.error(
            f"External service error during retry: {e}",
            extra={"extra_fields": {"error_type": type(e).__name__}}
        )
        return _error_response(str(e), 503, "external_service_error")


# ============================================================================
# Error Handlers
# ============================================================================

@api_bp.errorhandler(BusinessError)
def handle_business_error(error: BusinessError) -> Tuple[Dict[str, Any], int]:
    """Handle business logic errors (4xx)."""
    logger.warning(
        f"Business error: {error}",
        extra={"extra_fields": {"error_type": type(error).__name__}}
    )
    return _error_response(str(error), 400, type(error).__name__)


@api_bp.errorhandler(ExternalServiceError)
def handle_external_service_error(
    error: ExternalServiceError,
) -> Tuple[Dict[str, Any], int]:
    """Handle external service errors (5xx)."""
    logger.error(
        f"External service error: {error}",
        extra={"extra_fields": {"error_type": type(error).__name__}}
    )
    return _error_response(str(error), 503, type(error).__name__)


@api_bp.errorhandler(Exception)
def handle_unexpected_error(error: Exception) -> Tuple[Dict[str, Any], int]:
    """Handle unexpected errors (500)."""
    logger.exception(
        f"Unexpected error: {error}",
        extra={"extra_fields": {"error_type": type(error).__name__}}
    )
    return _error_response(
        "An unexpected error occurred",
        500,
        "internal_error",
    )

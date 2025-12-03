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

@api_bp.route("/migrate-pending-to-scheduled", methods=["POST"])
@rate_limit
def migrate_pending_to_scheduled() -> Tuple[Dict[str, Any], int]:
    """
    Migrate all followups with status 'pending' to 'scheduled'.
    
    This is a one-time migration endpoint to align statuses with Prospector UI.
    Changes all followup tasks from legacy 'pending' status to 'scheduled'.
    
    Returns:
        Migration results.
    """
    try:
        scheduler = SchedulerService()
        result = scheduler.migrate_pending_to_scheduled()
        
        return _success_response({
            "migrated_count": result["migrated_count"],
            "message": result["message"],
        })
        
    except ExternalServiceError as e:
        logger.error(
            f"External service error during migration: {e}",
            extra={"extra_fields": {"error_type": type(e).__name__}}
        )
        return _error_response(str(e), 503, "external_service_error")


@api_bp.route("/update-followups-scheduled-flags", methods=["POST"])
@rate_limit
def update_followups_scheduled_flags() -> Tuple[Dict[str, Any], int]:
    """
    Update followups_scheduled flag for drafts that have followup_ids but missing the flag.
    
    Finds all drafts that have followup_ids array but don't have the
    followups_scheduled field set to true and updates them.
    
    Returns:
        Update results summary.
    """
    try:
        scheduler = SchedulerService()
        results = scheduler.update_missing_followups_scheduled_flags()
        
        updated_count = sum(1 for r in results if r.get("status") == "updated")
        error_count = sum(1 for r in results if r.get("status") == "error")
        
        return _success_response({
            "total_drafts_processed": len(results),
            "updated_count": updated_count,
            "error_count": error_count,
            "results": results,
        })
        
    except ExternalServiceError as e:
        logger.error(
            f"External service error during update: {e}",
            extra={"extra_fields": {"error_type": type(e).__name__}}
        )
        return _error_response(str(e), 503, "external_service_error")


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


@api_bp.route("/sync-followup-ids", methods=["POST"])
@rate_limit
def sync_followup_ids() -> Tuple[Dict[str, Any], int]:
    """
    Synchronize followup_ids for drafts that have followups but missing the field.
    
    Finds all drafts that have followup tasks but don't have the followup_ids
    field populated in the draft document and updates them.
    
    Returns:
        Synchronization results summary.
    """
    try:
        scheduler = SchedulerService()
        results = scheduler.sync_missing_followup_ids()
        
        synced_count = sum(1 for r in results if r.get("status") == "synced")
        skipped_count = sum(1 for r in results if r.get("status") == "skipped")
        error_count = sum(1 for r in results if r.get("status") == "error")
        
        return _success_response({
            "total_drafts_processed": len(results),
            "synced_count": synced_count,
            "skipped_count": skipped_count,
            "error_count": error_count,
            "results": results,
        })
        
    except ExternalServiceError as e:
        logger.error(
            f"External service error during sync: {e}",
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


@api_bp.route("/migrate-to-old-schema", methods=["POST"])
@rate_limit
def migrate_to_old_schema() -> Tuple[Dict[str, Any], int]:
    """
    Migrate followup documents from new schema to old schema.
    Changes days_after_sent -> days_after_initial and scheduled_date -> scheduled_for.
    
    Returns:
        Migration result with count of migrated documents.
    """
    try:
        scheduler = SchedulerService()
        result = scheduler.migrate_to_old_schema()
        
        return _success_response({
            "migrated_count": result["migrated_count"],
            "message": result["message"]
        })
        
    except Exception as e:
        logger.error(
            f"Error during schema migration: {str(e)}",
            extra={"extra_fields": {"error": str(e)}}
        )
        return _error_response(
            f"Migration failed: {str(e)}",
            500,
            "migration_error"
        )


@api_bp.route("/debug/followup-fields", methods=["GET"])
def debug_followup_fields() -> Tuple[Dict[str, Any], int]:
    """
    Debug endpoint that crawls all email_followups documents and returns unique fields.
    
    Returns:
        List of unique field names found across all followup documents.
    """
    try:
        from google.cloud import firestore
        
        db = firestore.Client()
        followups_ref = db.collection("email_followups")
        
        unique_fields = set()
        doc_count = 0
        
        # Stream all documents
        for doc in followups_ref.stream():
            doc_count += 1
            doc_data = doc.to_dict()
            
            # Add all field names to the set
            if doc_data:
                unique_fields.update(doc_data.keys())
        
        # Convert set to sorted list for readable output
        fields_list = sorted(list(unique_fields))
        
        return _success_response({
            "total_documents": doc_count,
            "unique_fields_count": len(fields_list),
            "fields": fields_list
        })
        
    except Exception as e:
        logger.error(
            f"Error crawling followup fields: {str(e)}",
            extra={"extra_fields": {"error": str(e)}}
        )
        return _error_response(
            f"Field crawl failed: {str(e)}",
            500,
            "crawl_error"
        )


@api_bp.route("/debug/cleanup-sent-followups", methods=["POST"])
def debug_cleanup_sent_followups() -> Tuple[Dict[str, Any], int]:
    """
    Debug endpoint to mark J+3 followups as "sent" when subsequent followups exist.
    
    Logic:
    - For each scheduled followup with days_after_initial=3
    - Check if there's a draft with same x_external_id and followup_number > 1
    - If yes, mark the J+3 followup as "sent"
    
    Returns:
        Count of followups cleaned up and details.
    """
    try:
        from google.cloud import firestore
        from datetime import datetime, timezone
        
        db = firestore.Client()
        followups_ref = db.collection("email_followups")
        drafts_ref = db.collection("email_drafts")
        
        # Get all scheduled followups with days_after_initial=3
        j3_query = (followups_ref
            .where("status", "==", "scheduled")
            .where("days_after_initial", "==", 3))
        
        cleaned_followups = []
        
        for followup_doc in j3_query.stream():
            followup_data = followup_doc.to_dict()
            draft_id = followup_data.get("draft_id")
            
            if not draft_id:
                continue
            
            # Get the original draft to find x_external_id
            try:
                draft_doc = drafts_ref.document(draft_id).get()
                if not draft_doc.exists:
                    continue
                
                draft_data = draft_doc.to_dict()
                x_external_id = draft_data.get("x_external_id")
                
                if not x_external_id:
                    continue
                
                # Check if there's another draft with same x_external_id and followup_number > 1
                subsequent_drafts_query = (drafts_ref
                    .where("x_external_id", "==", x_external_id)
                    .where("followup_number", ">", 1)
                    .limit(1))
                
                subsequent_drafts = list(subsequent_drafts_query.stream())
                
                if subsequent_drafts:
                    # Mark the J+3 followup as sent
                    followups_ref.document(followup_doc.id).update({
                        "status": "sent",
                        "processed_at": datetime.now(timezone.utc),
                        "cleanup_note": "Auto-marked as sent (subsequent followup exists)"
                    })
                    
                    cleaned_followups.append({
                        "followup_id": followup_doc.id,
                        "draft_id": draft_id,
                        "x_external_id": x_external_id,
                        "to": followup_data.get("to"),
                        "scheduled_for": followup_data.get("scheduled_for").isoformat() if followup_data.get("scheduled_for") else None,
                        "subsequent_draft": subsequent_drafts[0].id
                    })
                    
            except Exception as doc_error:
                logger.warning(
                    f"Error processing followup {followup_doc.id}: {str(doc_error)}",
                    extra={"extra_fields": {
                        "followup_id": followup_doc.id,
                        "error": str(doc_error)
                    }}
                )
                continue
        
        return _success_response({
            "cleaned_count": len(cleaned_followups),
            "followups_cleaned": cleaned_followups
        })
        
    except Exception as e:
        logger.error(
            f"Error cleaning up sent followups: {str(e)}",
            extra={"extra_fields": {"error": str(e)}}
        )
        return _error_response(
            f"Cleanup failed: {str(e)}",
            500,
            "cleanup_error"
        )


@api_bp.route("/debug/due-followups", methods=["GET"])
def debug_due_followups() -> Tuple[Dict[str, Any], int]:
    """
    Debug endpoint to check what followups are due for processing.
    
    Returns:
        List of followups that should be processed with their details.
    """
    try:
        from google.cloud import firestore
        from datetime import datetime, timezone
        
        db = firestore.Client()
        followups_ref = db.collection("email_followups")
        
        now = datetime.now(timezone.utc)
        
        # Get all scheduled followups
        scheduled_query = followups_ref.where("status", "==", "scheduled")
        scheduled_followups = []
        
        for doc in scheduled_query.stream():
            data = doc.to_dict()
            scheduled_for = data.get("scheduled_for")
            
            # Check if scheduled_for exists and its type
            scheduled_for_info = {
                "exists": scheduled_for is not None,
                "type": str(type(scheduled_for).__name__),
                "value": None,
                "is_due": False
            }
            
            if scheduled_for:
                if hasattr(scheduled_for, 'isoformat'):
                    scheduled_for_info["value"] = scheduled_for.isoformat()
                    scheduled_for_info["is_due"] = scheduled_for <= now
                else:
                    scheduled_for_info["value"] = str(scheduled_for)
            
            scheduled_followups.append({
                "id": doc.id,
                "draft_id": data.get("draft_id"),
                "status": data.get("status"),
                "scheduled_for": scheduled_for_info,
                "days_after_initial": data.get("days_after_initial"),
                "to": data.get("to")
            })
        
        # Sort by scheduled_for
        scheduled_followups.sort(key=lambda x: x["scheduled_for"]["value"] or "")
        
        return _success_response({
            "current_time": now.isoformat(),
            "total_scheduled": len(scheduled_followups),
            "due_count": sum(1 for f in scheduled_followups if f["scheduled_for"].get("is_due")),
            "followups": scheduled_followups[:20]  # Limit to first 20
        })
        
    except Exception as e:
        logger.error(
            f"Error checking due followups: {str(e)}",
            extra={"extra_fields": {"error": str(e)}}
        )
        return _error_response(
            f"Check failed: {str(e)}",
            500,
            "check_error"
        )


@api_bp.route("/migrate-followup-schema", methods=["POST"])
@rate_limit
def migrate_followup_schema() -> Tuple[Dict[str, Any], int]:
    """
    Migrate email_followups collection schema:
    1. Rename days_after_initial → business_days_after
    2. Set followup_number based on business_days_after (3→1, 7→2, 10→3, 180→4)
    
    Returns:
        Migration results with counts.
    """
    try:
        from google.cloud import firestore
        
        db = firestore.Client()
        followups_ref = db.collection("email_followups")
        
        # Mapping from business days to followup number
        days_to_followup = {
            3: 1,
            7: 2,
            10: 3,
            180: 4
        }
        
        migrated_count = 0
        skipped_count = 0
        error_count = 0
        errors = []
        
        # Stream all documents
        for doc in followups_ref.stream():
            try:
                data = doc.to_dict()
                updates = {}
                
                # 1. Handle days_after_initial → business_days_after
                if "days_after_initial" in data and "business_days_after" not in data:
                    updates["business_days_after"] = data["days_after_initial"]
                elif "business_days_after" not in data:
                    # If neither exists, skip this document
                    skipped_count += 1
                    continue
                
                # Get the business_days value (from existing or migration)
                business_days = updates.get("business_days_after") or data.get("business_days_after")
                
                # 2. Set followup_number if missing or incorrect
                if business_days in days_to_followup:
                    expected_followup_number = days_to_followup[business_days]
                    current_followup_number = data.get("followup_number")
                    
                    if current_followup_number != expected_followup_number:
                        updates["followup_number"] = expected_followup_number
                
                # Apply updates if any
                if updates:
                    followups_ref.document(doc.id).update(updates)
                    migrated_count += 1
                    
                    logger.info(
                        f"Migrated followup {doc.id}",
                        extra={"extra_fields": {
                            "followup_id": doc.id,
                            "updates": updates,
                        }}
                    )
                else:
                    skipped_count += 1
                    
            except Exception as doc_error:
                error_count += 1
                error_msg = f"Error migrating {doc.id}: {str(doc_error)}"
                errors.append(error_msg)
                logger.error(
                    error_msg,
                    extra={"extra_fields": {
                        "followup_id": doc.id,
                        "error": str(doc_error)
                    }}
                )
        
        return _success_response({
            "migrated_count": migrated_count,
            "skipped_count": skipped_count,
            "error_count": error_count,
            "errors": errors[:10],  # Limit error list
            "message": f"Migrated {migrated_count} documents, skipped {skipped_count}, {error_count} errors"
        })
        
    except Exception as e:
        logger.error(
            f"Error during followup schema migration: {str(e)}",
            extra={"extra_fields": {"error": str(e)}}
        )
        return _error_response(
            f"Migration failed: {str(e)}",
            500,
            "migration_error"
        )


@api_bp.route("/debug/email-history/<x_external_id>", methods=["GET"])
def debug_email_history(x_external_id: str) -> Tuple[Dict[str, Any], int]:
    """
    Debug endpoint that retrieves email history for a given x_external_id.
    
    Simulates what auto-followup does when building email history for mail-writer.
    
    Args:
        x_external_id: The external ID to lookup.
        
    Returns:
        Email history with all details.
    """
    try:
        from auto_followup.infrastructure.firestore import DraftRepository
        
        draft_repo = DraftRepository()
        
        # Get all drafts with same x_external_id
        drafts = draft_repo.get_by_external_id(x_external_id)
        
        # Build email history like ProcessorService._get_email_history does
        email_history = []
        all_drafts_info = []
        
        for draft in drafts:
            draft_data = draft.raw_data
            
            # Info for all drafts (for debugging)
            all_drafts_info.append({
                "draft_id": draft.doc_id,
                "status": draft.draft_status,
                "followup_number": draft_data.get("followup_number", 0),
                "subject": draft_data.get("original_subject") or draft_data.get("subject", ""),
                "has_body": bool(draft_data.get("body")),
                "sent_at": draft_data.get("sent_at").isoformat() if draft_data.get("sent_at") and hasattr(draft_data.get("sent_at"), 'isoformat') else str(draft_data.get("sent_at")),
            })
            
            # Filter for email history (only sent drafts)
            if draft.draft_status == "sent":
                subject = draft_data.get("original_subject") or draft_data.get("subject", "")
                body = draft_data.get("body", "")
                
                if subject or body:
                    email_history.append({
                        "followup_number": draft_data.get("followup_number", 0),
                        "subject": subject,
                        "body": body[:300] + "..." if len(body) > 300 else body,  # Truncate for readability
                    })
        
        # Sort by followup_number (oldest first)
        email_history.sort(key=lambda x: x.get("followup_number", 0))
        
        return _success_response({
            "x_external_id": x_external_id,
            "total_drafts_found": len(drafts),
            "sent_drafts_count": len(email_history),
            "all_drafts": all_drafts_info,
            "email_history": email_history,
            "email_history_for_mail_writer": [
                {"subject": e["subject"], "body": e["body"]} 
                for e in email_history
            ],
        })
        
    except Exception as e:
        logger.error(
            f"Error retrieving email history for {x_external_id}: {str(e)}",
            extra={"extra_fields": {
                "x_external_id": x_external_id,
                "error": str(e)
            }}
        )
        return _error_response(
            f"Failed to retrieve email history: {str(e)}",
            500,
            "history_retrieval_error"
        )


@api_bp.route("/fix-followup-times", methods=["POST"])
@rate_limit
def fix_followup_times() -> Tuple[Dict[str, Any], int]:
    """
    Fix all scheduled followups to have their time set to 1:00 AM UTC.
    Only modifies the time component, keeps the same date.
    
    Returns:
        Count of followups updated.
    """
    try:
        from google.cloud import firestore
        from datetime import datetime, timezone
        
        db = firestore.Client()
        followups_ref = db.collection("email_followups")
        
        # Get all scheduled followups
        query = followups_ref.where("status", "==", "scheduled")
        followups = list(query.stream())
        
        updated_count = 0
        errors = []
        
        for doc in followups:
            try:
                data = doc.to_dict()
                scheduled_for = data.get("scheduled_for")
                
                if not scheduled_for:
                    continue
                
                # Convert Firestore timestamp to datetime if needed
                if hasattr(scheduled_for, 'year'):
                    # It's already a datetime-like object
                    current_time = scheduled_for
                    
                    # Check if time is already 1:00 AM
                    if current_time.hour == 1 and current_time.minute == 0 and current_time.second == 0:
                        continue
                    
                    # Create new datetime with same date but 1:00 AM
                    new_time = datetime(
                        year=current_time.year,
                        month=current_time.month,
                        day=current_time.day,
                        hour=1,
                        minute=0,
                        second=0,
                        microsecond=0,
                        tzinfo=timezone.utc
                    )
                    
                    # Update in Firestore
                    doc.reference.update({"scheduled_for": new_time})
                    updated_count += 1
                    
                    logger.info(
                        f"Updated followup {doc.id}: {current_time} -> {new_time.isoformat()}"
                    )
                
            except Exception as e:
                errors.append({
                    "followup_id": doc.id,
                    "error": str(e)
                })
                logger.error(f"Error updating followup {doc.id}: {e}")
        
        return _success_response({
            "updated_count": updated_count,
            "total_checked": len(followups),
            "errors": errors if errors else None,
            "message": f"Successfully updated {updated_count} followups to 1:00 AM UTC"
        })
        
    except Exception as e:
        logger.error(
            f"Error fixing followup times: {str(e)}",
            extra={"extra_fields": {"error": str(e)}}
        )
        return _error_response(
            f"Failed to fix followup times: {str(e)}",
            500,
            "fix_times_error"
        )



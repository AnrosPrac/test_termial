# app/routes/security_routes.py
"""
FastAPI routes for editor security
"""

from fastapi import APIRouter, HTTPException, Header, Depends, Query
from typing import Optional
import uuid

from app.models.security import (
    CreateSessionRequest,
    SessionTokenResponse,
    BatchSecurityEventsRequest,
    BatchEventsResponse,
    SubmitCodeRequest,
    SubmitCodeResponse,
    SubmissionIntegrityResult,
    SessionInfoResponse,
)
from app.services.session_service import SessionService
from app.services.integrity_service import IntegrityAnalyzerService
from app.db.models import EditorSession

router = APIRouter(prefix="/api/v1/editor", tags=["security"])

# Dependency injection
session_service = SessionService()
integrity_service = IntegrityAnalyzerService()


def verify_session(
    session_id: str,
    authorization: str = Header(...)
) -> EditorSession:
    """
    Verify session token (dependency)
    """
    try:
        token = authorization.replace("Bearer ", "")
        session = session_service.validate_session(session_id, token)
        
        if not session:
            raise HTTPException(status_code=401, detail="Invalid or expired session")
        
        return session
    except Exception as e:
        raise HTTPException(status_code=401, detail=str(e))


# ============ Session Endpoints ============

@router.post("/session/create", response_model=SessionTokenResponse)
async def create_session(request: CreateSessionRequest):
    """
    Create a new editor session
    
    Returns:
        SessionTokenResponse with session ID, token, and expiration
    """
    try:
        # Get user_id from request or generate temporary one
        user_id = request.user_id or str(uuid.uuid4())
        
        result = session_service.create_session(
            user_id=user_id,
            question_id=request.question_id,
            course_id=request.course_id
        )
        
        return SessionTokenResponse(**result)
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/session/info/{session_id}", response_model=SessionInfoResponse)
async def get_session_info(
    session_id: str,
    session: EditorSession = Depends(verify_session)
):
    """
    Get current session information
    """
    try:
        info = session_service.get_session_info(session_id)
        if not info:
            raise HTTPException(status_code=404, detail="Session not found")
        
        return SessionInfoResponse(**info)
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============ Security Events ============

@router.post("/security-events/batch", response_model=BatchEventsResponse)
async def record_security_events_batch(
    request: BatchSecurityEventsRequest,
    authorization: str = Header(...)
):
    """
    Record multiple security events in batch
    
    This is the main endpoint for security event tracking.
    Frontend sends batches of events every 5 seconds.
    """
    try:
        # Validate session
        token = authorization.replace("Bearer ", "")
        session = session_service.validate_session(request.session_id, token)
        
        if not session:
            raise HTTPException(status_code=401, detail="Invalid or expired session")
        
        # Record all events
        result = session_service.record_batch_events(
            session_id=request.session_id,
            user_id=session.user_id,
            question_id=session.question_id,
            events=[
                {
                    'event_type': event.event_type.value,
                    'metadata': event.metadata,
                    'timestamp': event.timestamp
                }
                for event in request.events
            ]
        )
        
        return BatchEventsResponse(**result)
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============ Code Submission ============

@router.post("/submissions/submit", response_model=SubmitCodeResponse)
async def submit_code(
    request: SubmitCodeRequest,
    authorization: str = Header(...)
):
    """
    Submit code with integrity checking
    
    This endpoint:
    1. Validates the session
    2. Analyzes code integrity
    3. Decides whether to accept or rollback
    4. Saves checkpoint for future rollback
    """
    try:
        # Validate session
        token = authorization.replace("Bearer ", "")
        session = session_service.validate_session(request.session_id, token)
        
        if not session:
            raise HTTPException(status_code=401, detail="Invalid or expired session")
        
        # Check if session is locked
        if session.status == 'LOCKED':
            return SubmitCodeResponse(
                success=False,
                message="Session is locked due to violations",
                integrity_status="COMPROMISED",
                suspicion_score=100,
                action="LOCK_QUESTION",
                reason="Session locked - maximum violations reached",
                session_locked=True,
                violations_remaining=0
            )
        
        # Analyze integrity
        analysis = integrity_service.analyze_submission(
            session_id=request.session_id,
            user_id=session.user_id,
            question_id=request.question_id,
            code=request.code,
            language=request.language.value,
            metadata=request.metadata
        )
        
        # Check if we should rollback
        if analysis['should_rollback']:
            # Lock session
            session_service.lock_session(request.session_id)
            
            return SubmitCodeResponse(
                success=False,
                message=analysis['rollback_reason'],
                integrity_status=analysis['status'],
                suspicion_score=analysis['suspicion_score'],
                action="ROLLBACK",
                reason=analysis['rollback_reason'],
                previous_code=analysis['previous_code'],
                session_locked=session.integrity_checks.violation_count >= 3,
                violations_remaining=max(0, 3 - session.integrity_checks.violation_count)
            )
        
        # Save checkpoint for rollback capability
        session_service.save_code_checkpoint(
            session_id=request.session_id,
            user_id=session.user_id,
            question_id=request.question_id,
            language=request.language.value,
            code=request.code
        )
        
        # Generate submission ID
        submission_id = str(uuid.uuid4())
        
        # Update analysis with submission ID
        from app.db.models import SubmissionIntegrity
        SubmissionIntegrity.objects(
            session_id=request.session_id
        ).order_by('-created_at').first().update(submission_id=submission_id)
        
        # Return success response
        return SubmitCodeResponse(
            success=True,
            submission_id=submission_id,
            message="Code submitted successfully",
            integrity_status=analysis['status'],
            suspicion_score=analysis['suspicion_score'],
            session_locked=False,
            violations_remaining=3 - session.integrity_checks.violation_count
        )
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============ Admin Endpoints ============

@router.get("/admin/submissions/flagged")
async def get_flagged_submissions(
    user_id: Optional[str] = Query(None),
    skip: int = Query(0),
    limit: int = Query(100),
    authorization: str = Header(...)
):
    """
    Get flagged submissions for review (admin only)
    """
    try:
        # TODO: Add admin authentication check
        
        result = integrity_service.get_flagged_submissions(
            user_id=user_id,
            skip=skip,
            limit=limit
        )
        
        return result
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/admin/sessions/{session_id}/events")
async def get_session_events(
    session_id: str,
    authorization: str = Header(...)
):
    """
    Get all security events for a session (admin only)
    """
    try:
        from app.db.models import SecurityEvent
        
        events = SecurityEvent.objects(session_id=session_id).order_by('-timestamp')
        
        return {
            'session_id': session_id,
            'total_events': len(events),
            'events': [
                {
                    'event_id': str(e.id),
                    'event_type': e.event_type,
                    'severity': e.severity,
                    'timestamp': e.timestamp,
                    'metadata': e.metadata
                }
                for e in events
            ]
        }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/admin/statistics")
async def get_statistics(
    authorization: str = Header(...)
):
    """
    Get security statistics (admin only)
    """
    try:
        from app.db.models import SecurityEvent, SubmissionIntegrity
        
        # Count events by type
        event_counts = {}
        for event in SecurityEvent.objects():
            event_type = event.event_type
            event_counts[event_type] = event_counts.get(event_type, 0) + 1
        
        # Count submissions by status
        submissions = SubmissionIntegrity.objects()
        
        status_counts = {
            'clean': submissions(integrity_status='CLEAN').count(),
            'suspicious': submissions(integrity_status='SUSPICIOUS').count(),
            'compromised': submissions(integrity_status='COMPROMISED').count(),
        }
        
        flagged = submissions(flagged_for_review=True).count()
        
        return {
            'total_events': SecurityEvent.objects().count(),
            'event_types': event_counts,
            'total_submissions': submissions.count(),
            'submissions_by_status': status_counts,
            'flagged_for_review': flagged,
            'paste_attempt_rate': round(
                (event_counts.get('paste_attempt', 0) / 
                max(submissions.count(), 1)) * 100, 2
            )
        }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

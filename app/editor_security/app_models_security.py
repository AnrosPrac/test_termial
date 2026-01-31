# app/models/security.py
"""
Security models for the editor session management and integrity checking
"""

from datetime import datetime
from typing import Optional, List, Dict, Any
from enum import Enum
from pydantic import BaseModel, Field
from bson import ObjectId


class EventType(str, Enum):
    """All possible security event types"""
    COPY_ATTEMPT = "copy_attempt"
    PASTE_ATTEMPT = "paste_attempt"
    CUT_ATTEMPT = "cut_attempt"
    HOTKEY_COPY_ATTEMPT = "hotkey_copy_attempt"
    HOTKEY_PASTE_ATTEMPT = "hotkey_paste_attempt"
    HOTKEY_CUT_ATTEMPT = "hotkey_cut_attempt"
    CONTEXT_MENU_ATTEMPT = "context_menu_attempt"
    LANGUAGE_SWITCH = "language_switch"
    SUSPICIOUS_ACTIVITY_DETECTED = "suspicious_activity_detected"
    CODE_ROLLBACK = "code_rollback"
    SUSPICIOUS_INPUT_RATE = "suspicious_input_rate"


class EventSeverity(str, Enum):
    """Event severity levels"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class SessionStatus(str, Enum):
    """Session status"""
    ACTIVE = "ACTIVE"
    LOCKED = "LOCKED"
    EXPIRED = "EXPIRED"
    COMPLETED = "COMPLETED"


class IntegrityStatus(str, Enum):
    """Code integrity status"""
    CLEAN = "CLEAN"
    SUSPICIOUS = "SUSPICIOUS"
    COMPROMISED = "COMPROMISED"


class Language(str, Enum):
    """Supported programming languages"""
    PYTHON = "python"
    JAVASCRIPT = "javascript"
    CPP = "cpp"
    C = "c"
    JAVA = "java"


# ============ Request Models ============

class CreateSessionRequest(BaseModel):
    """Request to create a new editor session"""
    question_id: str
    course_id: Optional[str] = None


class SecurityEventRequest(BaseModel):
    """Single security event from frontend"""
    event_type: EventType
    timestamp: int
    metadata: Optional[Dict[str, Any]] = None


class BatchSecurityEventsRequest(BaseModel):
    """Batch of security events"""
    session_id: str
    events: List[SecurityEventRequest]


class SubmitCodeRequest(BaseModel):
    """Code submission with security metadata"""
    session_id: str
    question_id: str
    language: Language
    code: str
    metadata: Dict[str, Any] = Field(default_factory=dict)


# ============ Response Models ============

class SessionTokenResponse(BaseModel):
    """Session creation response"""
    session_id: str
    session_token: str
    expires_at: datetime
    question_id: str

    class Config:
        json_encoders = {
            ObjectId: str
        }


class SecurityEventResponse(BaseModel):
    """Response after recording a security event"""
    event_id: str
    session_id: str
    recorded_at: datetime
    severity: EventSeverity


class BatchEventsResponse(BaseModel):
    """Response after batch event recording"""
    total_events: int
    critical_events: int
    action_required: bool
    action_type: Optional[str] = None
    message: Optional[str] = None


class SubmissionIntegrityResult(BaseModel):
    """Integrity analysis result"""
    status: IntegrityStatus
    suspicion_score: int
    
    paste_attempts: int
    copy_attempts: int
    cut_attempts: int
    violation_count: int
    edit_time_ms: int
    
    should_rollback: bool
    rollback_reason: Optional[str] = None
    previous_code: Optional[str] = None


class SubmitCodeResponse(BaseModel):
    """Response to code submission"""
    success: bool
    submission_id: Optional[str] = None
    message: str
    
    integrity_status: IntegrityStatus
    suspicion_score: int
    
    action: Optional[str] = None
    reason: Optional[str] = None
    previous_code: Optional[str] = None
    
    session_locked: bool = False
    violations_remaining: int = 3


class SessionInfoResponse(BaseModel):
    """Current session information"""
    session_id: str
    user_id: str
    question_id: str
    status: SessionStatus
    created_at: datetime
    last_activity: datetime
    expires_at: datetime
    violation_count: int
    locked_until: Optional[datetime] = None

    class Config:
        json_encoders = {
            ObjectId: str
        }

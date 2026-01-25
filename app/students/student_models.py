from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field
from enum import Enum

# ==================== ENUMS ====================

class SubmissionStatus(str, Enum):
    NOT_STARTED = "not_started"
    SUBMITTED = "submitted"
    APPROVED = "approved"
    REJECTED = "rejected"
    RESUBMISSION_REQUESTED = "resubmission_requested"

class PlagiarismFlag(str, Enum):
    """
    Students NEVER see the actual flag value
    Only used internally for teacher review
    """
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"
    PENDING = "pending"

# ==================== DATABASE MODELS ====================

class ClassroomMembership(BaseModel):
    """
    Tracks which students have joined which classrooms
    Enforces scope matching (university + college + branch)
    """
    membership_id: str  # MEM_XXXXXX
    classroom_id: str
    student_user_id: str  # SIDHI_XXXXXX
    student_sidhi_id: str  # student@sidhilynx.id
    university_id: str  # From student's profile.college
    college_id: str  # Same as university_id
    branch_id: str  # From student's profile.department
    joined_at: datetime = Field(default_factory=datetime.utcnow)
    is_active: bool = True

class StudentAssignmentView(BaseModel):
    """
    Cached/derived view of assignment status for a student
    Calculated on-demand, not stored
    """
    assignment_id: str
    classroom_id: str
    student_user_id: str
    due_date: Optional[datetime]
    allow_late: bool
    max_attempts: int
    attempts_used: int
    submission_status: SubmissionStatus
    last_submission_id: Optional[str] = None
    can_submit: bool  # Calculated: attempts < max AND (not past due OR allow_late)

class StudentSubmission(BaseModel):
    """
    Student's code submission
    Immutable once approved=True
    """
    submission_id: str  # SUB_XXXXXX
    assignment_id: str
    classroom_id: str
    student_user_id: str
    student_sidhi_id: str
    language: str
    code: str
    attempt_number: int
    test_result: Optional[dict] = None  # {passed: int, failed: int, score: float}
    approved: Optional[bool] = None  # None=pending, True=approved, False=rejected
    approval_notes: Optional[str] = None  # Teacher feedback (visible to student)
    plagiarism_flag: PlagiarismFlag = PlagiarismFlag.PENDING  # NEVER exposed to student
    submitted_at: datetime = Field(default_factory=datetime.utcnow)
    reviewed_at: Optional[datetime] = None
    is_locked: bool = False  # True when approved=True (immutable)

class SubmissionAudit(BaseModel):
    """
    Audit log for submission attempts and blocks
    """
    audit_id: str
    student_user_id: str
    student_sidhi_id: str
    assignment_id: str
    action: str  # submit, resubmit, blocked_attempt, blocked_deadline, blocked_scope
    success: bool
    reason: Optional[str] = None
    metadata: dict = {}
    timestamp: datetime = Field(default_factory=datetime.utcnow)
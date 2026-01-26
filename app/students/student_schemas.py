from pydantic import BaseModel, Field, validator
from typing import Optional, List
from datetime import datetime
from app.students.student_models import SubmissionStatus

# ==================== REQUEST SCHEMAS ====================

class SubmissionCreate(BaseModel):
    """
    Student submits code for an assignment
    """
    language: str = Field(..., min_length=1)
    code: str = Field(..., min_length=10, max_length=50000)
    
    @validator('language')
    def validate_language(cls, v):
        valid = {"python", "java", "cpp", "c", "javascript"}
        if v.lower() not in valid:
            raise ValueError(f'Invalid language. Allowed: {valid}')
        return v.lower()

class ResubmissionCreate(BaseModel):
    """
    Student resubmits after teacher requests changes
    """
    language: str = Field(..., min_length=1)
    code: str = Field(..., min_length=10, max_length=50000)
    
    @validator('language')
    def validate_language(cls, v):
        valid = {"python", "java", "cpp", "c", "javascript"}
        if v.lower() not in valid:
            raise ValueError(f'Invalid language. Allowed: {valid}')
        return v.lower()

# ==================== RESPONSE SCHEMAS ====================

class StudentProfile(BaseModel):
    """
    Student's profile information
    """
    user_id: str
    sidhi_id: str
    username: str
    email_id: str
    college: str  # university_id
    department: str  # branch_id
    degree: str
    starting_year: str

class ClassroomDiscovery(BaseModel):
    """
    Classroom shown in discovery list
    Shows join eligibility
    """
    classroom_id: str
    teacher_name: str
    teacher_sidhi_id: str
    name: str
    year: Optional[int]
    section: Optional[str]
    university_id: str
    branch_id: str
    student_count: int
    assignment_count: int
    can_join: bool  # False if scope mismatch or joining locked
    join_reason: Optional[str] = None  # "Already joined" | "Scope mismatch" | "Joining locked"
    is_joined: bool = False
class JoinClassroomRequest(BaseModel):
    """
    Request to join a classroom
    """
    password: Optional[str] = None  # Required if classroom has password protection
class JoinedClassroom(BaseModel):
    """
    Classroom the student has joined
    """
    classroom_id: str
    teacher_name: str
    name: str
    year: Optional[int]
    section: Optional[str]
    assignment_count: int
    pending_submissions: int
    joined_at: datetime


    

class AssignmentSummary(BaseModel):
    """
    Assignment shown in classroom view
    """
    assignment_id: str
    title: str
    due_date: Optional[datetime]
    allow_late: bool
    max_attempts: int
    attempts_used: int
    status: SubmissionStatus
    can_submit: bool
    last_score: Optional[float] = None

class AssignmentDetail(BaseModel):
    """
    Full assignment details
    """
    assignment_id: str
    classroom_id: str
    title: str
    description: str
    due_date: Optional[datetime]
    allow_late: bool
    max_attempts: int
    allowed_languages: List[str]
    attempts_used: int
    status: SubmissionStatus
    questions: List['AssignmentQuestionView'] = []
    can_submit: bool
    submission_blocked_reason: Optional[str] = None

class SubmissionResponse(BaseModel):
    """
    Student's submission details
    CRITICAL: No plagiarism data exposed
    """
    submission_id: str
    assignment_id: str
    language: str
    attempt_number: int
    test_result: Optional[dict]  # {passed: int, failed: int, score: float}
    approved: Optional[bool]  # None=pending, True=approved, False=rejected
    approval_notes: Optional[str]  # Teacher feedback
    submitted_at: datetime
    reviewed_at: Optional[datetime]
    is_locked: bool  # True if approved (immutable)

class SubmissionListItem(BaseModel):
    """
    Submission in list view (without full code)
    """
    submission_id: str
    assignment_title: str
    language: str
    attempt_number: int
    test_result: Optional[dict]
    approved: Optional[bool]
    submitted_at: datetime
    is_locked: bool

class SubmitSuccess(BaseModel):
    """
    Response after successful submission
    """
    submission_id: str
    attempt_number: int
    message: str
    processing_status: str  # "queued" - tests running asynchronously

class AssignmentQuestionView(BaseModel):
    """
    Individual question in an assignment
    """
    question_id: str
    prompt: str
    language: str
    marks: Optional[int] = None

class ClassroomDetail(BaseModel):
    """
    Full classroom view with assignments
    Student can only see this if they've joined
    """
    classroom_id: str
    teacher_name: str
    name: str
    year: Optional[int]
    section: Optional[str]
    late_submission_policy: Optional[str]
    assignments: List[AssignmentSummary] = []
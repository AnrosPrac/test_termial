from pydantic import BaseModel, Field, validator
from typing import Optional, List
from datetime import datetime
from app.teachers.teacher_models import (
    JoinMode, ClassroomVisibility, AssignmentStatus, 
    SourceType, PlagiarismFlag
)

# ==================== REQUEST SCHEMAS ====================

class TeacherProfileUpdate(BaseModel):
    designation: Optional[str] = None
    bio: Optional[str] = None

class ClassroomCreate(BaseModel):
    name: str = Field(..., min_length=3, max_length=100)
    year: Optional[int] = None
    section: Optional[str] = None
    join_mode: JoinMode = JoinMode.OPEN
    visibility: ClassroomVisibility = ClassroomVisibility.DRAFT
    late_submission_policy: Optional[str] = None
    
    @validator('year')
    def validate_year(cls, v):
        if v and (v < 1 or v > 5):
            raise ValueError('Year must be between 1 and 5')
        return v

class ClassroomUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=3, max_length=100)
    year: Optional[int] = None
    section: Optional[str] = None
    join_mode: Optional[JoinMode] = None
    visibility: Optional[ClassroomVisibility] = None
    late_submission_policy: Optional[str] = None

class AssignmentCreate(BaseModel):
    title: str = Field(..., min_length=3, max_length=200)
    description: str
    source_type: SourceType = SourceType.MANUAL
    due_date: Optional[datetime] = None
    allow_late: bool = True
    max_attempts: int = Field(3, ge=1, le=10)
    allowed_languages: List[str] = ["python"]
    
    @validator('allowed_languages')
    def validate_languages(cls, v):
        valid = {"python", "java", "cpp", "c", "javascript"}
        if not all(lang in valid for lang in v):
            raise ValueError(f'Invalid language. Allowed: {valid}')
        return v

class AssignmentUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=3, max_length=200)
    description: Optional[str] = None
    due_date: Optional[datetime] = None
    allow_late: Optional[bool] = None
    max_attempts: Optional[int] = Field(None, ge=1, le=10)
    allowed_languages: Optional[List[str]] = None
    status: Optional[AssignmentStatus] = None

class TestCaseCreate(BaseModel):
    input_data: str
    expected_output: str
    weight: float = Field(1.0, ge=0.1, le=10.0)
    is_hidden: bool = False

class TestCaseUpdate(BaseModel):
    input_data: Optional[str] = None
    expected_output: Optional[str] = None
    weight: Optional[float] = Field(None, ge=0.1, le=10.0)
    is_hidden: Optional[bool] = None

class SubmissionApproval(BaseModel):
    notes: Optional[str] = None

class SubmissionRejection(BaseModel):
    notes: str = Field(..., min_length=10)
    request_resubmission: bool = False

class TestResultOverride(BaseModel):
    """
    Teacher manually overrides test results
    """
    passed: int = Field(..., ge=0)
    failed: int = Field(..., ge=0)
    score: float = Field(..., ge=0.0, le=100.0)
    reason: str = Field(..., min_length=10)

class PlagiarismReview(BaseModel):
    notes: Optional[str] = None

class RecordNotesRequest(BaseModel):
    """
    Generate academic record notes for students
    """
    include_scores: bool = True
    include_submissions: bool = True
    format: str = "pdf"  # pdf, csv, json

class ExportSubmissionSheet(BaseModel):
    """
    Export all submissions for grading
    """
    format: str = "xlsx"  # xlsx, csv

# ==================== RESPONSE SCHEMAS ====================

class TeacherProfile(BaseModel):
    user_id: str
    sidhi_id: str
    username: str
    email_id: str
    college: str  # university_id
    department: str  # branch_id
    degree: str
    designation: Optional[str] = None
    bio: Optional[str] = None
    created_at: datetime

class ClassroomResponse(BaseModel):
    classroom_id: str
    teacher_user_id: str
    teacher_sidhi_id: str
    university_id: str
    branch_id: str
    name: str
    year: Optional[int]
    section: Optional[str]
    join_mode: JoinMode
    visibility: ClassroomVisibility
    late_submission_policy: Optional[str]
    joining_locked: bool
    student_count: int = 0
    assignment_count: int = 0
    created_at: datetime
    updated_at: datetime

class AssignmentResponse(BaseModel):
    assignment_id: str
    classroom_id: str
    teacher_user_id: str
    title: str
    description: str
    source_type: SourceType
    due_date: Optional[datetime]
    allow_late: bool
    max_attempts: int
    allowed_languages: List[str]
    status: AssignmentStatus
    testcases_locked: bool
    testcase_count: int = 0
    submission_count: int = 0
    created_at: datetime
    updated_at: datetime

class TestCaseResponse(BaseModel):
    testcase_id: str
    assignment_id: str
    input_data: str
    expected_output: str
    weight: float
    is_hidden: bool
    locked: bool
    created_at: datetime

class SubmissionResponse(BaseModel):
    submission_id: str
    assignment_id: str
    classroom_id: str
    student_user_id: str
    student_sidhi_id: str
    student_username: str
    language: str
    attempt_number: int
    test_result: Optional[dict]
    teacher_override_result: Optional[dict]
    approved: Optional[bool]
    approval_notes: Optional[str]
    submitted_at: datetime
    reviewed_at: Optional[datetime]

class PlagiarismSummary(BaseModel):
    assignment_id: str
    total_pairs: int
    green_flags: int
    yellow_flags: int
    red_flags: int
    unreviewed_count: int

class PlagiarismDetail(BaseModel):
    pair_id: str
    assignment_id: str
    submission_1_id: str
    submission_2_id: str
    student_1_username: str
    student_2_username: str
    similarity_score: float
    flag: PlagiarismFlag
    details: dict
    reviewed_by_teacher: bool
    teacher_notes: Optional[str]
    detected_at: datetime

class ClassroomAnalytics(BaseModel):
    classroom_id: str
    total_students: int
    total_assignments: int
    avg_submission_rate: float
    avg_score: float
    active_students: int

class AssignmentScorecard(BaseModel):
    assignment_id: str
    total_submissions: int
    approved_submissions: int
    pending_submissions: int
    rejected_submissions: int
    avg_score: float
    avg_attempts: float
    on_time_submissions: int
    late_submissions: int
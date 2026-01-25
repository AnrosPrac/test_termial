from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field
from enum import Enum

# ==================== ENUMS ====================

class JoinMode(str, Enum):
    OPEN = "open"
    INVITE = "invite"

class ClassroomVisibility(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"

class AssignmentStatus(str, Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    CLOSED = "closed"

class SourceType(str, Enum):
    MANUAL = "manual"
    DOCUMENT = "document"
    AI = "ai"

class PlagiarismFlag(str, Enum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"

# ==================== DATABASE MODELS ====================

class TeacherMeta(BaseModel):
    """
    Extended teacher metadata (optional)
    Core data comes from users_profile collection
    """
    user_id: str  # SIDHI_XXXXXX from users_profile
    sidhi_id: str  # lumetrixtutorial@sidhilynx.id
    designation: Optional[str] = None  # Professor, Assistant Professor, etc.
    bio: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class Classroom(BaseModel):
    classroom_id: str  # CLS_XXXXXX
    teacher_user_id: str  # SIDHI_XXXXXX
    teacher_sidhi_id: str  # for display
    university_id: str  # mapped from college
    college_id: str  # same as university_id (for clarity)
    branch_id: str  # mapped from department
    name: str
    year: Optional[int] = None
    section: Optional[str] = None
    join_mode: JoinMode = JoinMode.OPEN
    visibility: ClassroomVisibility = ClassroomVisibility.DRAFT
    late_submission_policy: Optional[str] = None  # e.g., "-10% per day"
    joining_locked: bool = False  # Can students join?
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class Assignment(BaseModel):
    assignment_id: str  # ASG_XXXXXX
    classroom_id: str
    teacher_user_id: str
    teacher_sidhi_id: str
    title: str
    description: str
    source_type: SourceType = SourceType.MANUAL
    due_date: Optional[datetime] = None
    allow_late: bool = True
    max_attempts: int = 3
    allowed_languages: List[str] = ["python", "java", "cpp"]
    status: AssignmentStatus = AssignmentStatus.DRAFT
    testcases_locked: bool = False  # Once locked, immutable
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class TestCase(BaseModel):
    testcase_id: str  # TC_XXXXXX
    assignment_id: str
    input_data: str
    expected_output: str
    weight: float = 1.0  # For weighted scoring
    is_hidden: bool = False  # Hidden from students
    locked: bool = False  # Once assignment.testcases_locked = True
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class Submission(BaseModel):
    submission_id: str  # SUB_XXXXXX
    assignment_id: str
    classroom_id: str
    student_user_id: str  # SIDHI_XXXXXX
    student_sidhi_id: str
    language: str
    code: str
    attempt_number: int = 1
    test_result: Optional[dict] = None  # {passed: 5, failed: 2, score: 71.4}
    teacher_override_result: Optional[dict] = None  # Manual override
    approved: Optional[bool] = None  # None = pending, True = approved, False = rejected
    approval_notes: Optional[str] = None
    submitted_at: datetime = Field(default_factory=datetime.utcnow)
    reviewed_at: Optional[datetime] = None

class AuditLog(BaseModel):
    actor_user_id: str  # SIDHI_XXXXXX
    actor_sidhi_id: str
    role: str  # teacher, student, admin
    action: str  # create_assignment, delete_classroom, override_result, etc.
    target_type: str  # classroom, assignment, submission, testcase
    target_id: str
    metadata: dict = {}  # Extra context
    timestamp: datetime = Field(default_factory=datetime.utcnow)

class PlagiarismResult(BaseModel):
    """
    Stores AI-detected plagiarism between two submissions
    Teachers can only view, never auto-punish
    """
    pair_id: str  # PLAG_XXXXXX
    assignment_id: str
    submission_1_id: str
    submission_2_id: str
    student_1_user_id: str
    student_2_user_id: str
    similarity_score: float  # 0.0 to 1.0
    flag: PlagiarismFlag  # green (<30%), yellow (30-70%), red (>70%)
    details: dict = {}  # Code segments, match locations
    reviewed_by_teacher: bool = False
    teacher_notes: Optional[str] = None
    detected_at: datetime = Field(default_factory=datetime.utcnow)
    reviewed_at: Optional[datetime] = None

class ClassroomMembership(BaseModel):
    """
    Tracks which students are in which classroom
    """
    classroom_id: str
    student_user_id: str
    student_sidhi_id: str
    joined_at: datetime = Field(default_factory=datetime.utcnow)
    is_active: bool = True
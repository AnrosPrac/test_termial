from pydantic import BaseModel, Field, validator
from typing import List, Optional, Dict, Any
from datetime import datetime
from enum import Enum

# ==================== ENUMS ====================

class CourseType(str, Enum):
    OFFICIAL = "OFFICIAL"
    CREATOR = "CREATOR"

class CourseDomain(str, Enum):
    SOFTWARE = "SOFTWARE"
    HARDWARE = "HARDWARE"

class CourseStatus(str, Enum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"

class DifficultyLevel(str, Enum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"

class LeagueTier(str, Enum):
    BRONZE = "BRONZE"
    SILVER = "SILVER"
    GOLD = "GOLD"
    PLATINUM = "PLATINUM"
    DIAMOND = "DIAMOND"
    MYTHIC = "MYTHIC"
    LEGEND = "LEGEND"

# ==================== COURSE MODELS ====================

class CourseCreate(BaseModel):
    title: str
    description: str
    course_type: CourseType
    domain: CourseDomain
    instructor_id: Optional[str] = None
    thumbnail_url: Optional[str] = None
    tags: List[str] = []
    external_resources: List[Dict[str, str]] = []  # [{"title": "...", "url": "..."}]

class CourseUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    thumbnail_url: Optional[str] = None
    tags: Optional[List[str]] = None
    external_resources: Optional[List[Dict[str, str]]] = None

class CoursePublish(BaseModel):
    confirm: bool = True

# ==================== MODULE MODELS ====================

class ModuleCreate(BaseModel):
    course_id: str
    title: str
    description: str
    order: int
    is_locked: bool = False

# ==================== QUESTION MODELS ====================

class TestCase(BaseModel):
    input: str
    output: str
    weight: float = 1.0
    is_hidden: bool = False

class QuestionCreate(BaseModel):
    course_id: str
    module_id: Optional[str] = None
    title: str
    description: str
    difficulty: DifficultyLevel
    language: str  # c, cpp, python, verilog, vhdl, systemverilog
    problem_type: str = "coding"  # coding, mcq, theory
    test_cases: List[TestCase] = []
    time_limit: float = 2.0
    memory_limit: int = 256
    points: int = 100  # base points
    
    @validator('language')
    def validate_language(cls, v):
        allowed = ['c', 'cpp', 'python', 'verilog', 'vhdl', 'systemverilog']
        if v.lower() not in allowed:
            raise ValueError(f'Language must be one of: {allowed}')
        return v.lower()

# ==================== ENROLLMENT MODELS ====================

class EnrollmentCreate(BaseModel):
    course_id: str

class EnrollmentResponse(BaseModel):
    enrollment_id: str
    course_id: str
    user_id: str
    sidhi_id: str
    certificate_id: str
    enrolled_at: datetime
    progress: float = 0.0
    current_league: LeagueTier = LeagueTier.BRONZE

# ==================== SUBMISSION MODELS ====================

class SubmissionCreate(BaseModel):
    course_id: str
    question_id: str
    code: str
    language: str

class SubmissionResponse(BaseModel):
    submission_id: str
    question_id: str
    user_id: str
    status: str  # queued, processing, completed, failed
    verdict: Optional[str] = None
    passed: Optional[int] = None
    total: Optional[int] = None
    score: Optional[float] = None
    execution_time_ms: Optional[float] = None
    memory_mb: Optional[float] = None
    submitted_at: datetime

# ==================== GRADING MODELS ====================

class GradeResponse(BaseModel):
    course_id: str
    user_id: str
    total_questions: int
    solved_questions: int
    total_score: float
    avg_efficiency: float
    current_league: LeagueTier
    league_points: int
    rank: Optional[int] = None

# ==================== LEADERBOARD MODELS ====================

class LeaderboardEntry(BaseModel):
    rank: int
    user_id: str
    sidhi_id: str
    username: str
    college: Optional[str] = None
    league: LeagueTier
    total_points: int
    problems_solved: int
    avg_efficiency: float

class LeaderboardResponse(BaseModel):
    scope: str  # course, branch, college, state, national, alumni
    entries: List[LeaderboardEntry]
    total_users: int
    page: int
    page_size: int

# ==================== CERTIFICATE MODELS ====================

class CertificateData(BaseModel):
    certificate_id: str
    user_id: str
    sidhi_id: str
    username: str
    course_id: str
    course_title: str
    grade_points: float
    league: LeagueTier
    problems_solved: int
    completion_date: Optional[datetime] = None
    skills: List[str] = []
    badges: List[str] = []

# ==================== ACHIEVEMENT MODELS ====================

class AchievementUnlock(BaseModel):
    achievement_id: str
    title: str
    description: str
    icon: str
    unlocked_at: datetime

class BadgeProgress(BaseModel):
    badge_id: str
    title: str
    current: int
    required: int
    unlocked: bool

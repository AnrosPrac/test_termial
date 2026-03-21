"""
interview_models.py
────────────────────
Pydantic models and enums for the Interview system.
"""

from pydantic import BaseModel, validator
from typing import List, Optional, Dict, Any
from enum import Enum


# ==================== ENUMS ====================

class InterviewType(str, Enum):
    TEST  = "TEST"    # 2 questions, 15 min/q, informational, no retake
    PRE   = "PRE"     # 3 questions, 10 min/q, informational, retakeable
    FINAL = "FINAL"   # 4 questions, 20 min/q, PASS/FAIL, retakeable


class InterviewStatus(str, Enum):
    ACTIVE    = "ACTIVE"
    INACTIVE  = "INACTIVE"


class SessionStatus(str, Enum):
    STARTED    = "STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED  = "COMPLETED"
    TIMED_OUT  = "TIMED_OUT"
    TERMINATED = "TERMINATED"   # auto-submit due to integrity violations


class IntegrityEventType(str, Enum):
    TAB_SWITCH   = "TAB_SWITCH"
    PASTE_DETECT = "PASTE_DETECT"
    FOCUS_LOST   = "FOCUS_LOST"
    COPY_DETECT  = "COPY_DETECT"
    RIGHT_CLICK  = "RIGHT_CLICK"
    DEV_TOOLS    = "DEV_TOOLS"


class QuestionVerdict(str, Enum):
    ACCEPTED           = "Accepted"
    WRONG_ANSWER       = "Wrong Answer"
    TIME_LIMIT_EXCEEDED = "Time Limit Exceeded"
    COMPILATION_ERROR  = "Compilation Error"
    RUNTIME_ERROR      = "Runtime Error"
    SKIPPED            = "Skipped"       # question timed out, moved on
    NOT_ATTEMPTED      = "Not Attempted"


# ==================== CONFIG PER TYPE ====================

INTERVIEW_CONFIG = {
    InterviewType.TEST: {
        "question_count":      2,
        "time_per_question":   15 * 60,   # seconds
        "retakeable":          False,
        "is_pass_fail":        False,
        "pass_threshold":      None,
        "tab_switch_limit":    3,
        "similarity_check":    False,
    },
    InterviewType.PRE: {
        "question_count":      3,
        "time_per_question":   10 * 60,
        "retakeable":          True,
        "is_pass_fail":        False,
        "pass_threshold":      None,
        "tab_switch_limit":    2,
        "similarity_check":    True,
    },
    InterviewType.FINAL: {
        "question_count":      4,
        "time_per_question":   20 * 60,
        "retakeable":          True,
        "is_pass_fail":        True,
        "pass_threshold":      3,         # 3 out of 4 accepted = pass
        "tab_switch_limit":    1,
        "similarity_check":    True,
    },
}

# ══════════════════════════════════════════════════════════════
#  INTERVIEW UNLOCK GATES — percentage-based
#
#  Instead of hardcoded counts (25, 50, 75...) which break on
#  small courses, each slot unlocks at a % of the course's
#  total question count.
#
#  A 5-question course:  TEST_1 = 1q, TEST_6 = 4q, FINAL = 5q
#  A 200-question course: TEST_1 = 24q, TEST_6 = 150q, FINAL = 190q
#
#  Always fair regardless of course size.
# ══════════════════════════════════════════════════════════════

UNLOCK_GATE_RATIOS = {
    "TEST_1": 0.12,   # 12% — just getting started
    "TEST_2": 0.25,   # 25% — quarter done
    "TEST_3": 0.38,   # 38% — getting serious
    "TEST_4": 0.50,   # 50% — halfway
    "TEST_5": 0.62,   # 62% — past halfway
    "TEST_6": 0.75,   # 75% — three quarters
    "PRE":    0.85,   # 85% — nearly complete
    "FINAL":  0.95,   # 95% — almost mastered
}

# Keep for backward compat and general (non-course) interviews
# where there's no total_questions to reference
UNLOCK_GATES_FALLBACK = {
    "TEST_1": 5,
    "TEST_2": 10,
    "TEST_3": 15,
    "TEST_4": 20,
    "TEST_5": 25,
    "TEST_6": 30,
    "PRE":    35,
    "FINAL":  40,
}


def compute_unlock_gate(slot: str, total_questions: int) -> int:
    """
    Compute the solved-count threshold to unlock a given interview slot.

    Uses percentage of total_questions. Always at least 1.
    Falls back to UNLOCK_GATES_FALLBACK if total_questions is 0.
    """
    if total_questions <= 0:
        return UNLOCK_GATES_FALLBACK.get(slot, 0)
    ratio = UNLOCK_GATE_RATIOS.get(slot, 0.0)
    return max(1, round(total_questions * ratio))


# ==================== REQUEST MODELS ====================

class InterviewCreate(BaseModel):
    """Teacher creates an interview."""
    title:         str
    interview_type: InterviewType
    course_id:     Optional[str] = None    # None = general interview
    module_id:     Optional[str] = None    # questions pulled from this module only
    question_ids:  List[str]               # teacher hand-picks questions
    slot:          Optional[str] = None    # "TEST_1".."TEST_6" | "PRE" | "FINAL"
                                           # required when course_id is set

    @validator("question_ids")
    def validate_question_count(cls, v, values):
        interview_type = values.get("interview_type")
        if interview_type:
            expected = INTERVIEW_CONFIG[interview_type]["question_count"]
            if len(v) != expected:
                raise ValueError(
                    f"{interview_type} interview must have exactly {expected} questions, got {len(v)}"
                )
        return v

    @validator("slot")
    def validate_slot(cls, v, values):
        course_id = values.get("course_id")
        if course_id and not v:
            raise ValueError("slot is required when interview is tied to a course")
        if v and v not in UNLOCK_GATE_RATIOS:
            raise ValueError(f"slot must be one of {list(UNLOCK_GATE_RATIOS.keys())}")
        return v


class StartSessionRequest(BaseModel):
    language: str    # student picks language at start


class SubmitQuestionRequest(BaseModel):
    code:     str
    language: str


class IntegrityEventRequest(BaseModel):
    event_type: IntegrityEventType
    metadata:   Optional[Dict[str, Any]] = {}   # extra context e.g. {"url": "..."}
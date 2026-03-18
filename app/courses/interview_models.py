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

# Solved count gates per interview slot in a course
# TEST_1 needs 25 solved, TEST_2 needs 50 ... PRE needs 175, FINAL needs 200
UNLOCK_GATES = {
    "TEST_1": 25,
    "TEST_2": 50,
    "TEST_3": 75,
    "TEST_4": 100,
    "TEST_5": 125,
    "TEST_6": 150,
    "PRE":    175,
    "FINAL":  200,
}


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
        if v and v not in UNLOCK_GATES:
            raise ValueError(f"slot must be one of {list(UNLOCK_GATES.keys())}")
        return v


class StartSessionRequest(BaseModel):
    language: str    # student picks language at start


class SubmitQuestionRequest(BaseModel):
    code:     str
    language: str


class IntegrityEventRequest(BaseModel):
    event_type: IntegrityEventType
    metadata:   Optional[Dict[str, Any]] = {}   # extra context e.g. {"url": "..."}
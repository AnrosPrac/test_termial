"""
general_router.py
──────────────────
General practice questions — free, no enrollment, no points,
no certificates. Just solve and get a verdict.

Mount with:
    app.include_router(general_router.router, prefix="/api/general")
"""

from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, validator
from typing import Optional, List
from datetime import datetime
import uuid
import httpx
import os
import asyncio

from app.courses.dependencies import get_db, get_current_user_id

router = APIRouter(tags=["General Practice"])

SOFTWARE_JUDGE_URL = os.getenv("JUDGE_API_URL", "http://localhost:8000")
SOFTWARE_JUDGE_KEY = os.getenv("JUDGE_API_KEY", "")

ALLOWED_LANGUAGES = ["c", "cpp", "python", "java", "javascript"]


def _new_id(prefix: str, length: int = 8) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:length].upper()}"


def _iso(dt) -> Optional[str]:
    return dt.isoformat() if isinstance(dt, datetime) else dt


# ══════════════════════════════════════════════════════════════
#  REQUEST MODELS
# ══════════════════════════════════════════════════════════════

class GeneralQuestionCreate(BaseModel):
    title:       str
    description: str
    difficulty:  str    # easy / medium / hard
    language:    str
    tags:        List[str] = []
    test_cases:  List[dict] = []   # same shape as course questions
    starter_code: Optional[str] = None
    time_limit:  float = 2.0
    memory_limit: int  = 256

    @validator("difficulty")
    def validate_difficulty(cls, v):
        if v not in ["easy", "medium", "hard"]:
            raise ValueError("difficulty must be easy, medium or hard")
        return v

    @validator("language")
    def validate_language(cls, v):
        if v.lower() not in ALLOWED_LANGUAGES:
            raise ValueError(f"language must be one of {ALLOWED_LANGUAGES}")
        return v.lower()


class GeneralSubmitRequest(BaseModel):
    question_id: str
    code:        str
    language:    str

    @validator("language")
    def validate_language(cls, v):
        if v.lower() not in ALLOWED_LANGUAGES:
            raise ValueError(f"language must be one of {ALLOWED_LANGUAGES}")
        return v.lower()


# ══════════════════════════════════════════════════════════════
#  JUDGE HELPER
# ══════════════════════════════════════════════════════════════

async def _judge_code(code: str, language: str, test_cases: list) -> dict:
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{SOFTWARE_JUDGE_URL}/judge",
                json={
                    "language":   language,
                    "sourceCode": code,
                    "testcases":  [{"input": tc["input"], "output": tc["output"]} for tc in test_cases]
                },
                headers={"X-API-Key": SOFTWARE_JUDGE_KEY},
                timeout=30.0
            )
            if resp.status_code != 200:
                return {"verdict": "System Error", "passed": 0, "total": len(test_cases)}

            task_id = resp.json().get("task_id")
            for _ in range(60):
                await asyncio.sleep(1)
                sr = await client.get(
                    f"{SOFTWARE_JUDGE_URL}/status/{task_id}",
                    headers={"X-API-Key": SOFTWARE_JUDGE_KEY},
                    timeout=5.0
                )
                if sr.status_code == 200 and sr.json().get("status") == "completed":
                    return sr.json().get("result", {})

            return {"verdict": "Judging Timeout", "passed": 0, "total": len(test_cases)}
    except Exception as e:
        return {"verdict": "System Error", "error": str(e)}


def _sanitize_test_results(test_results: list, test_cases: list) -> list:
    """Strip private test case outputs — same logic as course questions."""
    sample_indices = {
        idx + 1
        for idx, tc in enumerate(test_cases)
        if tc.get("is_sample", False) or not tc.get("is_hidden", True)
    }
    sanitized = []
    for tr in test_results:
        tc_id = tr.get("test_case_id")
        if tc_id in sample_indices:
            sanitized.append(tr)
        else:
            sanitized.append({
                "test_case_id":      tc_id,
                "passed":            tr.get("passed"),
                "verdict":           tr.get("verdict"),
                "execution_time_ms": tr.get("execution_time_ms"),
                "memory_used_mb":    tr.get("memory_used_mb"),
                "output":            None,
                "expected":          None,
            })
    return sanitized


# ══════════════════════════════════════════════════════════════
#  ADMIN — CREATE QUESTION
# ══════════════════════════════════════════════════════════════

@router.post("/questions/create")
async def create_general_question(
    payload: GeneralQuestionCreate,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    """Admin/teacher creates a general practice question."""
    profile = await db.users_profile.find_one({"user_id": user_id})
    if not profile or profile.get("role") not in ["teacher", "admin"]:
        raise HTTPException(status_code=403, detail="Only teachers/admins can create general questions")

    question_id = _new_id("GQ", 10)
    now = datetime.utcnow()

    doc = {
        "question_id":  question_id,
        "title":        payload.title,
        "description":  payload.description,
        "difficulty":   payload.difficulty,
        "language":     payload.language,
        "tags":         payload.tags,
        "test_cases":   payload.test_cases,
        "starter_code": payload.starter_code,
        "time_limit":   payload.time_limit,
        "memory_limit": payload.memory_limit,
        "is_active":    True,
        "created_by":   user_id,
        "created_at":   now,

        # General questions have NO course, NO module, NO points, NO league
        "course_id":    None,
        "module_id":    None,
        "points":       0,
        "category":     "general",
    }

    await db.general_questions.insert_one(doc)
    return {"success": True, "question_id": question_id}


# ══════════════════════════════════════════════════════════════
#  STUDENT — LIST & FETCH
# ══════════════════════════════════════════════════════════════

@router.get("/questions")
async def list_general_questions(
    difficulty: Optional[str] = None,
    language:   Optional[str] = None,
    tag:        Optional[str] = None,
    skip:       int = 0,
    limit:      int = 20,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    """List all general practice questions with optional filters."""
    filters = {"is_active": True, "category": "general"}
    if difficulty:
        filters["difficulty"] = difficulty
    if language:
        filters["language"] = language.lower()
    if tag:
        filters["tags"] = tag

    cursor = db.general_questions.find(filters).sort("created_at", -1).skip(skip).limit(limit)
    questions = await cursor.to_list(length=limit)
    total = await db.general_questions.count_documents(filters)

    # Fetch user's solved general questions
    solved = await db.general_submissions.distinct(
        "question_id",
        {"user_id": user_id, "verdict": "Accepted"}
    )
    solved_set = set(solved)

    result = []
    for q in questions:
        result.append({
            "question_id": q["question_id"],
            "title":       q["title"],
            "difficulty":  q["difficulty"],
            "language":    q["language"],
            "tags":        q.get("tags", []),
            "is_solved":   q["question_id"] in solved_set,
            "_id":         str(q["_id"]),
        })

    return {
        "questions": result,
        "total":     total,
        "skip":      skip,
        "limit":     limit,
    }


@router.get("/questions/{question_id}")
async def get_general_question(
    question_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    """Get a single general question — only public test cases returned."""
    q = await db.general_questions.find_one({"question_id": question_id, "is_active": True})
    if not q:
        raise HTTPException(status_code=404, detail="Question not found")

    q["_id"] = str(q["_id"])

    # Only return public test cases
    q["test_cases"] = [tc for tc in q.get("test_cases", []) if tc.get("is_sample", False)]

    # Attach solved status
    solved = await db.general_submissions.find_one({
        "question_id": question_id,
        "user_id":     user_id,
        "verdict":     "Accepted"
    })
    q["is_solved"] = bool(solved)

    return q


# ══════════════════════════════════════════════════════════════
#  STUDENT — SUBMIT
# ══════════════════════════════════════════════════════════════

@router.post("/submit")
async def submit_general_question(
    payload: GeneralSubmitRequest,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    """
    Submit code for a general question.
    No points, no league, no enrollment check.
    Returns full verdict immediately (synchronous poll).
    """
    q = await db.general_questions.find_one({
        "question_id": payload.question_id,
        "is_active":   True
    })
    if not q:
        raise HTTPException(status_code=404, detail="Question not found")

    test_cases   = q.get("test_cases", [])
    judge_result = await _judge_code(payload.code, payload.language, test_cases)

    verdict    = judge_result.get("verdict", "System Error")
    passed_cnt = judge_result.get("passed", 0)
    total_cnt  = judge_result.get("total", len(test_cases))

    # Sanitize test results
    raw_results = judge_result.get("test_results", [])
    sanitized   = _sanitize_test_results(raw_results, test_cases)

    # Save submission
    submission_id = _new_id("GSUB", 10)
    now = datetime.utcnow()

    await db.general_submissions.insert_one({
        "submission_id":  submission_id,
        "question_id":    payload.question_id,
        "user_id":        user_id,
        "code":           payload.code,
        "language":       payload.language,
        "verdict":        verdict,
        "passed":         passed_cnt,
        "total":          total_cnt,
        "judge_result":   {**judge_result, "test_results": sanitized},
        "submitted_at":   now,
    })

    return {
        "submission_id":       submission_id,
        "verdict":             verdict,
        "passed":              passed_cnt,
        "total":               total_cnt,
        "test_results":        sanitized,
        "avg_execution_time_ms": judge_result.get("avg_execution_time_ms"),
        "max_execution_time_ms": judge_result.get("max_execution_time_ms"),
        "avg_memory_mb":         judge_result.get("avg_memory_mb"),
        "submitted_at":          _iso(now),
    }


@router.get("/submissions/{question_id}")
async def get_my_submissions(
    question_id: str,
    limit: int = 10,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    """Get submission history for a general question."""
    cursor = db.general_submissions.find(
        {"question_id": question_id, "user_id": user_id}
    ).sort("submitted_at", -1).limit(limit)

    submissions = await cursor.to_list(length=limit)
    return {
        "question_id": question_id,
        "submissions": [
            {
                "submission_id": s["submission_id"],
                "verdict":       s.get("verdict"),
                "passed":        s.get("passed"),
                "total":         s.get("total"),
                "language":      s.get("language"),
                "submitted_at":  _iso(s.get("submitted_at")),
            }
            for s in submissions
        ],
        "count": len(submissions),
    }


@router.get("/my-stats")
async def get_my_general_stats(
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    """Quick stats for the user's general practice activity."""
    total_submissions = await db.general_submissions.count_documents({"user_id": user_id})
    accepted = await db.general_submissions.count_documents({"user_id": user_id, "verdict": "Accepted"})
    solved_ids = await db.general_submissions.distinct("question_id", {"user_id": user_id, "verdict": "Accepted"})

    by_difficulty = await db.general_questions.aggregate([
        {"$match": {"question_id": {"$in": solved_ids}, "is_active": True}},
        {"$group": {"_id": "$difficulty", "count": {"$sum": 1}}}
    ]).to_list(length=None)

    diff_stats = {"easy": 0, "medium": 0, "hard": 0}
    for d in by_difficulty:
        diff_stats[d["_id"]] = d["count"]

    return {
        "total_submissions":  total_submissions,
        "accepted":           accepted,
        "unique_solved":      len(solved_ids),
        "acceptance_rate":    round(accepted / total_submissions * 100, 1) if total_submissions else 0,
        "by_difficulty":      diff_stats,
    }
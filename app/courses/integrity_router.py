"""
integrity_router.py
────────────────────
Lightweight backend integrity system for course practice questions.

Design principles:
- Submit and Run endpoints are NEVER touched or blocked
- Frontend sends ONE call on submit — minimal frontend work
- ALL analysis happens server-side using Motor async (same DB as rest of backend)
- Completely decoupled — if this fails, submissions still work fine

Mount with:
    app.include_router(integrity_router, prefix="/api/integrity")

Endpoints:
    POST /api/integrity/report      — called once on submit, logs integrity data
    GET  /api/integrity/submission/{submission_id}  — get integrity report
    GET  /api/integrity/student/{user_id}           — teacher view per student
    GET  /api/integrity/course/{course_id}          — teacher view per course
"""

from fastapi import APIRouter, Depends, BackgroundTasks
from motor.motor_asyncio import AsyncIOMotorDatabase
from datetime import datetime
from typing import Optional
from pydantic import BaseModel

from app.courses.dependencies import get_db, get_current_user_id

router = APIRouter(tags=["Integrity"])


# ─── Request / Response Models ─────────────────────────────────────────────────

class IntegrityReport(BaseModel):
    submission_id: str
    question_id: str
    course_id: str
    language: str
    code: str

    # Behavioral signals — all sent from frontend on submit, nothing polled
    paste_attempts:   int = 0   # Ctrl+V blocked attempts
    copy_attempts:    int = 0   # Ctrl+C attempts
    cut_attempts:     int = 0   # Ctrl+X attempts
    tab_switches:     int = 0   # window blur/focus events
    edit_time_ms:     int = 0   # time from page load to submit (ms)
    code_length:      int = 0   # final code character count


# ─── Scoring ────────────────────────────────────────────────────────────────────

def _compute_integrity(report: IntegrityReport) -> dict:
    """
    Pure function — takes behavioral signals, returns suspicion score + breakdown.
    Backend-only analysis. No frontend involvement after this point.
    """
    score = 0
    breakdown = {}

    # 1. Paste attempts — heaviest signal (blocked on frontend, but attempts logged)
    if report.paste_attempts > 0:
        pts = min(report.paste_attempts * 25, 60)
        score += pts
        breakdown["paste_attempts"] = pts

    # 2. Copy attempts — moderate signal
    if report.copy_attempts > 0:
        pts = min(report.copy_attempts * 10, 25)
        score += pts
        breakdown["copy_attempts"] = pts

    # 3. Tab switches — left the editor window
    if report.tab_switches > 3:
        pts = min((report.tab_switches - 3) * 5, 20)
        score += pts
        breakdown["tab_switches"] = pts

    # 4. Typing speed analysis — code appeared too fast
    if report.edit_time_ms > 0 and report.code_length > 0:
        edit_secs = max(report.edit_time_ms / 1000, 1)
        chars_per_sec = report.code_length / edit_secs

        # Realistic typing: 3–8 chars/sec with thinking time
        # > 50 chars/sec = likely pasted (even if paste was "blocked", drag-drop etc.)
        if chars_per_sec > 50:
            pts = min(int((chars_per_sec - 50) / 5), 30)
            score += pts
            breakdown["typing_speed"] = {
                "chars_per_sec": round(chars_per_sec, 1),
                "points": pts
            }

    # 5. Zero edit time with non-trivial code — submitted instantly
    if report.edit_time_ms < 5000 and report.code_length > 50:
        score += 20
        breakdown["instant_submit"] = 20

    score = min(score, 100)

    if score >= 70:
        status = "COMPROMISED"
    elif score >= 35:
        status = "SUSPICIOUS"
    else:
        status = "CLEAN"

    return {
        "suspicion_score": score,
        "status": status,
        "flagged": status != "CLEAN",
        "breakdown": breakdown,
    }


async def _save_integrity_record(
    db: AsyncIOMotorDatabase,
    user_id: str,
    report: IntegrityReport,
    analysis: dict,
):
    """Saves to practice_integrity collection — fully async, never blocks submit."""
    await db.practice_integrity.insert_one({
        "submission_id":  report.submission_id,
        "user_id":        user_id,
        "question_id":    report.question_id,
        "course_id":      report.course_id,
        "language":       report.language,

        # behavioral signals
        "paste_attempts": report.paste_attempts,
        "copy_attempts":  report.copy_attempts,
        "cut_attempts":   report.cut_attempts,
        "tab_switches":   report.tab_switches,
        "edit_time_ms":   report.edit_time_ms,
        "code_length":    report.code_length,
        "chars_per_sec":  round(
            report.code_length / max(report.edit_time_ms / 1000, 1), 1
        ),

        # analysis result
        "suspicion_score": analysis["suspicion_score"],
        "status":          analysis["status"],
        "flagged":         analysis["flagged"],
        "breakdown":       analysis["breakdown"],

        "created_at": datetime.utcnow(),
        "reviewed":   False,
        "review_note": None,
    })


# ─── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/report")
async def submit_integrity_report(
    report: IntegrityReport,
    background_tasks: BackgroundTasks,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    """
    Called ONCE by the frontend immediately after hitting Submit.
    Never blocks the actual code submission — runs in background.

    Frontend sends:
        submission_id, question_id, course_id, language, code,
        paste_attempts, copy_attempts, cut_attempts,
        tab_switches, edit_time_ms, code_length
    """
    analysis = _compute_integrity(report)

    # Save in background — submit/run are never affected
    background_tasks.add_task(
        _save_integrity_record, db, user_id, report, analysis
    )

    return {
        "received": True,
        "status":   analysis["status"],
        "flagged":  analysis["flagged"],
    }


@router.get("/submission/{submission_id}")
async def get_submission_integrity(
    submission_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    """Get integrity report for a specific submission."""
    record = await db.practice_integrity.find_one(
        {"submission_id": submission_id},
        {"_id": 0}
    )
    if not record:
        return {"found": False}
    return {"found": True, "report": record}


@router.get("/student/{student_id}")
async def get_student_integrity(
    student_id: str,
    course_id: Optional[str] = None,
    limit: int = 50,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    """
    Teacher view — all integrity records for a student.
    Optionally filter by course.
    """
    query: dict = {"user_id": student_id}
    if course_id:
        query["course_id"] = course_id

    records = await db.practice_integrity.find(
        query, {"_id": 0}
    ).sort("created_at", -1).limit(limit).to_list(length=limit)

    total_flagged = sum(1 for r in records if r.get("flagged"))
    avg_score = (
        round(sum(r.get("suspicion_score", 0) for r in records) / len(records), 1)
        if records else 0
    )

    return {
        "student_id":    student_id,
        "total":         len(records),
        "total_flagged": total_flagged,
        "avg_score":     avg_score,
        "records":       records,
    }


@router.get("/course/{course_id}")
async def get_course_integrity(
    course_id: str,
    flagged_only: bool = False,
    limit: int = 100,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    """
    Teacher view — all integrity records for a course.
    Use flagged_only=true to see only suspicious submissions.
    """
    query: dict = {"course_id": course_id}
    if flagged_only:
        query["flagged"] = True

    records = await db.practice_integrity.find(
        query, {"_id": 0}
    ).sort("suspicion_score", -1).limit(limit).to_list(length=limit)

    return {
        "course_id":     course_id,
        "total":         len(records),
        "flagged_only":  flagged_only,
        "records":       records,
    }
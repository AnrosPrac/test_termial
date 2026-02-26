from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from motor.motor_asyncio import AsyncIOMotorDatabase
from typing import List
import httpx
import os
import asyncio
from app.courses.models import SubmissionCreate, SubmissionResponse
from app.courses.database import (
    create_submission, get_submission, update_submission_result,
    get_question, get_enrollment, mark_question_solved, update_league_points
)
from app.courses.dependencies import get_db,get_current_user_id

router = APIRouter( tags=["Submissions"])

# Judge service URLs from environment
SOFTWARE_JUDGE_URL = os.getenv("JUDGE_API_URL", "http://localhost:8000")
SOFTWARE_JUDGE_KEY = os.getenv("JUDGE_API_KEY", "")
HARDWARE_JUDGE_URL = os.getenv("HDL_JUDGE_URL", "http://localhost:8080")

# ==================== GRADING LOGIC ====================
# Import the difficulty-aware points helper from database
from app.courses.database import get_base_points_for_question

# Efficiency multiplier — time only, no memory correction
# Only applied on Accepted submissions
def _efficiency_multiplier(avg_time_ms: float) -> float:
    if avg_time_ms <= 0:
        return 1.0        # no timing data — neutral
    if avg_time_ms < 50:
        return 1.20       # blazing fast → 20% bonus
    if avg_time_ms < 200:
        return 1.10       # fast → 10% bonus
    if avg_time_ms < 500:
        return 1.00       # normal → baseline
    return 0.90           # slow but accepted → small penalty


def calculate_score_with_efficiency(result: dict, question: dict) -> dict:
    """
    Calculate final league points for a submission.

    Key rules:
    - WRONG ANSWER  → 0 league points. No partial credit farming.
    - ACCEPTED      → base_points (difficulty-weighted) × efficiency multiplier
    - base_points comes from question difficulty, not a flat 100

    Returns:
        {
            "league_points":          int,    ← what gets added to enrollment
            "base_points":            int,    ← raw points before multiplier
            "efficiency_multiplier":  float,
            "verdict":                str,
            "breakdown":              dict,
        }
    """
    verdict   = result.get("verdict", "")
    avg_time  = result.get("avg_execution_time_ms", 0) or 0
    base_pts  = get_base_points_for_question(question)

    if verdict != "Accepted":
        # ── NO league points for wrong/partial answers ────────────────────
        # We still return the partial correctness % for UI display only
        passed = result.get("passed", 0)
        total  = result.get("total", 1) or 1
        return {
            "league_points":         0,
            "base_points":           base_pts,
            "efficiency_multiplier": 0.0,
            "verdict":               verdict,
            "breakdown": {
                "reason":       "No points awarded — solution not fully accepted",
                "passed":       passed,
                "total":        total,
                "correctness":  round(passed / total * 100, 1),
                "avg_time_ms":  avg_time,
            }
        }

    # ── ACCEPTED ─────────────────────────────────────────────────────────
    eff        = _efficiency_multiplier(avg_time)
    pts_earned = round(base_pts * eff)

    return {
        "league_points":         pts_earned,
        "base_points":           base_pts,
        "efficiency_multiplier": eff,
        "verdict":               "Accepted",
        "breakdown": {
            "base_points":  base_pts,
            "efficiency":   eff,
            "avg_time_ms":  avg_time,
            "pts_earned":   pts_earned,
        }
    }

# ==================== SOFTWARE JUDGE INTEGRATION ====================

async def judge_software(submission_id: str, code: str, language: str, test_cases: list, db: AsyncIOMotorDatabase):
    """Submit to SOFTWARE judge service (Python, C, C++)"""
    try:
        async with httpx.AsyncClient() as client:
            # Submit to software judge
            response = await client.post(
                f"{SOFTWARE_JUDGE_URL}/judge",
                json={
                    "language": language,
                    "sourceCode": code,
                    "testcases": [{"input": tc["input"], "output": tc["output"]} for tc in test_cases]
                },
                headers={"X-API-Key": SOFTWARE_JUDGE_KEY},
                timeout=30.0
            )
            
            if response.status_code != 200:
                await update_submission_result(db, submission_id, {
                    "verdict": "System Error",
                    "error": "Judge service unavailable"
                })
                return
            
            judge_response = response.json()
            task_id = judge_response.get("task_id")
            
            # Poll for result
            for _ in range(60):  # 60 seconds max
                await asyncio.sleep(1)
                
                status_response = await client.get(
                    f"{SOFTWARE_JUDGE_URL}/status/{task_id}",
                    headers={"X-API-Key": SOFTWARE_JUDGE_KEY},
                    timeout=5.0
                )
                
                if status_response.status_code == 200:
                    status_data = status_response.json()
                    
                    if status_data.get("status") == "completed":
                        result = status_data.get("result", {})
                        await process_result(db, submission_id, result)
                        return
            
            # Timeout
            await update_submission_result(db, submission_id, {
                "verdict": "Judging Timeout",
                "error": "Evaluation timed out"
            })
            
    except Exception as e:
        await update_submission_result(db, submission_id, {
            "verdict": "System Error",
            "error": str(e)
        })

# ==================== HARDWARE JUDGE INTEGRATION ====================

async def judge_hardware(submission_id: str, code: str, language: str, problem_id: str, db: AsyncIOMotorDatabase):
    """Submit to HARDWARE judge service (Verilog, VHDL, SystemVerilog)"""
    try:
        async with httpx.AsyncClient() as client:
            # Hardware judge uses synchronous evaluation
            response = await client.post(
                f"{HARDWARE_JUDGE_URL}/evaluate",
                json={
                    "submission_id": submission_id,
                    "problem_id": problem_id,
                    "language": language,
                    "code": code
                },
                timeout=120.0  # Hardware simulation can take longer
            )
            
            if response.status_code != 200:
                await update_submission_result(db, submission_id, {
                    "verdict": "System Error",
                    "error": "HDL Judge service unavailable"
                })
                return
            
            hdl_result = response.json()
            
            # Convert HDL judge response to standard format
            result = {
                "verdict": hdl_result.get("status", "FAIL"),
                "passed": 1 if hdl_result.get("status") == "PASS" else 0,
                "total": 1,
                "test_results": [{
                    "test_case_id": 1,
                    "passed": hdl_result.get("status") == "PASS",
                    "verdict": hdl_result.get("status"),
                    "logs": hdl_result.get("logs", "")
                }],
                "logs": hdl_result.get("logs", "")
            }
            
            # Map HDL statuses to standard verdicts
            if result["verdict"] == "PASS":
                result["verdict"] = "Accepted"
            elif result["verdict"] == "COMPILE_ERROR":
                result["verdict"] = "Compilation Error"
            elif result["verdict"] == "TIMEOUT":
                result["verdict"] = "Time Limit Exceeded"
            
            await process_result(db, submission_id, result)
            
    except Exception as e:
        await update_submission_result(db, submission_id, {
            "verdict": "System Error",
            "error": str(e)
        })

# ==================== COMMON RESULT PROCESSING ====================

async def process_result(db: AsyncIOMotorDatabase, submission_id: str, result: dict):
    """
    Process judge result and update database.

    Handles three cases:
    1. Wrong answer      → save result, 0 league points
    2. First-time solve  → save result, award full points, mark solved
    3. Re-submission on already-solved question (efficiency improvement)
                         → save result, award only the DELTA if efficiency improved
    """
    submission = await get_submission(db, submission_id)
    if not submission:
        return

    question = await get_question(db, submission["question_id"])
    if not question:
        await update_submission_result(db, submission_id, result)
        return

    scoring = calculate_score_with_efficiency(result, question)

    # Persist scoring breakdown onto the submission record
    enriched_result = {
        **result,
        "league_points_awarded": scoring["league_points"],
        "efficiency_multiplier": scoring["efficiency_multiplier"],
        "base_points":           scoring["base_points"],
        "breakdown":             scoring["breakdown"],
    }
    await update_submission_result(db, submission_id, enriched_result)

    if result.get("verdict") != "Accepted":
        # No further action for wrong answers
        return

    user_id   = submission["user_id"]
    course_id = submission["course_id"]
    question_id = submission["question_id"]

    enrollment = await get_enrollment(db, course_id, user_id)
    if not enrollment:
        return

    already_solved = question_id in enrollment.get("solved_questions", [])

    if not already_solved:
        # ── FIRST SOLVE ──────────────────────────────────────────────────
        await mark_question_solved(db, course_id, user_id, question_id)

        league_result = await update_league_points(
            db,
            user_id,
            scoring["league_points"],
            course_id=course_id,
            efficiency_multiplier=scoring["efficiency_multiplier"],
        )

        # Stamp the submission with league context for frontend polling
        await db.course_submissions.update_one(
            {"submission_id": submission_id},
            {"$set": {
                "is_first_solve": True,
                "league_up":      league_result["league_up"],
                "new_league":     league_result["new_league"],
                "is_legend":      league_result["is_legend"],
            }}
        )

    else:
        # ── RE-SUBMISSION ON ALREADY-SOLVED QUESTION ─────────────────────
        # Allow efficiency improvement: award DELTA points only if
        # the new efficiency multiplier is better than what we stored.
        # We look at the previous best submission for this question.
        prev_best = await db.course_submissions.find_one(
            {
                "course_id":   course_id,
                "user_id":     user_id,
                "question_id": question_id,
                "verdict":     "Accepted",
                "submission_id": {"$ne": submission_id},
            },
            sort=[("league_points_awarded", -1)]
        )

        prev_pts = prev_best.get("league_points_awarded", 0) if prev_best else 0
        delta    = scoring["league_points"] - prev_pts

        await db.course_submissions.update_one(
            {"submission_id": submission_id},
            {"$set": {"is_first_solve": False, "efficiency_delta": delta}}
        )

        if delta > 0:
            # They genuinely improved — award the difference
            league_result = await update_league_points(
                db,
                user_id,
                delta,
                course_id=course_id,
                efficiency_multiplier=scoring["efficiency_multiplier"],
            )
            await db.course_submissions.update_one(
                {"submission_id": submission_id},
                {"$set": {
                    "league_points_awarded": delta,
                    "league_up":  league_result["league_up"],
                    "new_league": league_result["new_league"],
                }}
            )
        # If delta <= 0 (same or worse efficiency) — no points, no penalty


# ==================== JUDGE ROUTER ====================

async def judge_code(submission_id: str, code: str, language: str, question: dict, db: AsyncIOMotorDatabase):
    """Route to appropriate judge based on language"""
    
    # Determine which judge to use
    software_languages = ["c", "cpp", "python", "java", "javascript"]
    hardware_languages = ["verilog", "vhdl", "systemverilog"]
    
    if language in software_languages:
        # Use software judge with test cases
        test_cases = question.get("test_cases", [])
        await judge_software(submission_id, code, language, test_cases, db)
    
    elif language in hardware_languages:
        # Use hardware judge with problem_id
        problem_id = question.get("question_id")
        await judge_hardware(submission_id, code, language, problem_id, db)
    
    else:
        # Unknown language
        await update_submission_result(db, submission_id, {
            "verdict": "System Error",
            "error": f"Unsupported language: {language}"
        })

# ==================== ENDPOINTS ====================

@router.post("/submit")
async def submit_solution(
    submission: SubmissionCreate,
    background_tasks: BackgroundTasks,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    """
    Submit solution for grading.
    Re-submissions on already-solved questions are ALLOWED —
    students can improve their efficiency score and earn delta points.
    """
    enrollment = await get_enrollment(db, submission.course_id, user_id)
    if not enrollment:
        raise HTTPException(status_code=403, detail="Not enrolled in course")

    question = await get_question(db, submission.question_id)
    if not question:
        raise HTTPException(status_code=404, detail="Question not found")

    already_solved = submission.question_id in enrollment.get("solved_questions", [])

    submission_id = await create_submission(db, {
        **submission.dict(),
        "user_id": user_id
    })

    background_tasks.add_task(
        judge_code,
        submission_id,
        submission.code,
        submission.language,
        question,
        db
    )

    return {
        "success":       True,
        "submission_id": submission_id,
        "status":        "queued",
        "is_retry":      already_solved,
        "message":       "Improving efficiency — delta points awarded if you beat your best" if already_solved else "Submission queued for evaluation"
    }

@router.get("/{submission_id}/status")
async def get_submission_status(
    submission_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    """Get submission status"""
    submission = await get_submission(db, submission_id)
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")
    
    if submission["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    response = {
        "submission_id": submission_id,
        "status": submission["status"],
        "submitted_at": submission["submitted_at"]
    }
    
    if submission["status"] == "completed":
        response.update({
            "verdict":               submission.get("verdict"),
            "score":                 submission.get("score"),
            "league_points_awarded": submission.get("league_points_awarded", 0),
            "efficiency_multiplier": submission.get("efficiency_multiplier"),
            "base_points":           submission.get("base_points"),
            "is_first_solve":        submission.get("is_first_solve"),
            "league_up":             submission.get("league_up", False),
            "new_league":            submission.get("new_league"),
            "is_legend":             submission.get("is_legend", False),
            "efficiency_delta":      submission.get("efficiency_delta"),
            "breakdown":             submission.get("breakdown"),
        })

    return response

@router.get("/course/{course_id}/history")
async def get_submission_history(
    course_id: str,
    skip: int = 0,
    limit: int = 20,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    """Get submission history for course"""
    cursor = db.course_submissions.find({
        "course_id": course_id,
        "user_id": user_id
    }).sort("submitted_at", -1).skip(skip).limit(limit)
    
    submissions = await cursor.to_list(length=limit)
    
    return {
        "submissions": [
            {k: str(v) if hasattr(v, '__class__') and v.__class__.__name__ == 'ObjectId' else v
             for k, v in {**s, "_id": str(s["_id"])}.items()}
            for s in submissions
        ],
        "count": len(submissions)
    }

@router.get("/course/{course_id}/question/{question_id}/my-submissions")
async def get_question_submission_history(
    course_id: str,
    question_id: str,
    limit: int = 10,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    """Get a student's submission history for a specific question"""
    cursor = db.course_submissions.find({
        "course_id": course_id,
        "question_id": question_id,
        "user_id": user_id
    }).sort("submitted_at", -1).limit(limit)

    submissions = await cursor.to_list(length=limit)

    return {
        "question_id": question_id,
        "submissions": [
            {
                "submission_id": s["submission_id"],
                "status": s.get("status"),
                "verdict": s.get("verdict"),
                "score": s.get("score"),
                "language": s.get("language"),
                "submitted_at": s["submitted_at"]
            }
            for s in submissions
        ],
        "count": len(submissions)
    }
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

def calculate_score_with_efficiency(result: dict, base_points: int = 100) -> dict:
    """
    Calculate final score with efficiency multipliers
    
    Returns:
        {
            "final_score": float,
            "correctness_score": float,
            "efficiency_multiplier": float,
            "breakdown": {...}
        }
    """
    if result.get("verdict") != "Accepted":
        partial = (result.get("passed", 0) / result.get("total", 1)) if result.get("total") else 0
        return {
            "final_score": partial * base_points * 0.6,  # Max 60% for partial
            "correctness_score": partial * 100,
            "efficiency_multiplier": 0.6,
            "breakdown": {"partial_credit": True}
        }
    
    # Full correctness
    correctness = 1.0
    
    # Efficiency multiplier
    avg_time = result.get("avg_execution_time_ms", 0)
    avg_memory = result.get("avg_memory_mb", 0)
    
    # For now, just give bonus for fast execution
    if avg_time < 50:
        efficiency = 1.2
    elif avg_time < 200:
        efficiency = 1.0
    else:
        efficiency = 0.85
    
    final_score = base_points * correctness * efficiency
    
    return {
        "final_score": round(final_score, 2),
        "correctness_score": 100.0,
        "efficiency_multiplier": efficiency,
        "breakdown": {
            "avg_time_ms": avg_time,
            "avg_memory_mb": avg_memory
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
    """Process judge result and update database"""
    # Update submission with result
    await update_submission_result(db, submission_id, result)
    
    # If accepted, mark question as solved and award points
    if result.get("verdict") == "Accepted":
        submission = await get_submission(db, submission_id)
        
        # Mark question as solved
        await mark_question_solved(
            db,
            submission["course_id"],
            submission["user_id"],
            submission["question_id"]
        )
        
        # Award league points
        question = await get_question(db, submission["question_id"])
        points = question.get("points", 100)
        scoring = calculate_score_with_efficiency(result, points)
        
        await update_league_points(
            db,
            submission["user_id"],
            int(scoring["final_score"])
        )

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
    """Submit solution for grading"""
    # Verify enrollment
    enrollment = await get_enrollment(db, submission.course_id, user_id)
    if not enrollment:
        raise HTTPException(status_code=403, detail="Not enrolled in course")
    
    # Check if already solved
    if submission.question_id in enrollment.get("solved_questions", []):
        raise HTTPException(status_code=400, detail="Question already solved")
    
    # Get question
    question = await get_question(db, submission.question_id)
    if not question:
        raise HTTPException(status_code=404, detail="Question not found")
    
    # Create submission record
    submission_id = await create_submission(db, {
        **submission.dict(),
        "user_id": user_id
    })
    
    # Start judging in background
    background_tasks.add_task(
        judge_code,
        submission_id,
        submission.code,
        submission.language,
        question,
        db
    )
    
    return {
        "success": True,
        "submission_id": submission_id,
        "status": "queued",
        "message": "Submission queued for evaluation"
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
            "verdict": submission.get("verdict"),
            "result": submission.get("result"),
            "score": submission.get("score")
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
        "submissions": submissions,
        "count": len(submissions)
    }

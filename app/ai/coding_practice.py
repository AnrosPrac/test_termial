# app/coding/router.py - COMPLETE VERSION

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, validator
from typing import List, Optional
from datetime import datetime, timedelta
from app.ai.client_bound_guard import verify_client_bound_request
from motor.motor_asyncio import AsyncIOMotorClient
import os
import uuid
import httpx
from collections import defaultdict

router = APIRouter()

# Database connection
MONGO_URL = os.getenv("MONGO_URL")
client = AsyncIOMotorClient(MONGO_URL)
db = client.lumetrics_db

# Judge service config
JUDGE_API_URL = os.getenv("JUDGE_API_URL")
JUDGE_API_KEY = os.getenv("JUDGE_API_KEY")


# ==================== REQUEST MODELS ====================

class SubmissionRequest(BaseModel):
    question_id: str
    language: str
    source_code: str
    
    @validator('language')
    def validate_language(cls, v):
        allowed = ['c', 'cpp', 'python']
        if v.lower() not in allowed:
            raise ValueError(f'Language must be one of: {allowed}')
        return v.lower()
    
    @validator('source_code')
    def validate_source(cls, v):
        if len(v.encode('utf-8')) > 100 * 1024:  # 100KB
            raise ValueError('Source code too large (max 100KB)')
        return v


class BookmarkRequest(BaseModel):
    question_id: str


class NoteRequest(BaseModel):
    question_id: str
    content: str


# ==================== ENDPOINTS ====================

@router.get("/questions")
async def get_all_questions(
    user: dict = Depends(verify_client_bound_request),
    difficulty: Optional[str] = None,
    topic: Optional[str] = None,
    status: Optional[str] = None,  # solved, attempted, not_started
    sort_by: Optional[str] = "question_id"  # question_id, difficulty, acceptance_rate
):
    """
    Get list of all coding questions with user progress
    """
    try:
        sidhi_id = user.get("sub")
        
        # Build query
        query = {"is_active": True}
        if difficulty:
            query["difficulty"] = difficulty.lower()
        if topic:
            query["topic"] = topic.lower()
        
        # Get questions
        questions_cursor = db.coding_questions.find(query, {"_id": 0})
        
        # Apply sorting
        if sort_by == "acceptance_rate":
            questions_cursor = questions_cursor.sort("acceptance_rate", -1)
        elif sort_by == "difficulty":
            difficulty_order = {"easy": 1, "medium": 2, "hard": 3}
            questions = await questions_cursor.to_list(length=None)
            questions.sort(key=lambda x: difficulty_order.get(x["difficulty"], 0))
        else:
            questions_cursor = questions_cursor.sort("question_id", 1)
            questions = await questions_cursor.to_list(length=None)
        
        if sort_by != "difficulty":
            questions = await questions_cursor.to_list(length=None)
        
        # Get user progress for all questions
        progress_cursor = db.user_progress.find(
            {"sidhi_id": sidhi_id},
            {"_id": 0, "question_id": 1, "status": 1, "attempts": 1}
        )
        progress_list = await progress_cursor.to_list(length=None)
        progress_map = {p["question_id"]: p for p in progress_list}
        
        # Get user bookmarks
        bookmarks = await db.user_bookmarks.find_one({"sidhi_id": sidhi_id})
        bookmarked_ids = set(bookmarks.get("question_ids", [])) if bookmarks else set()
        
        # Combine data
        result = []
        for q in questions:
            qid = q["question_id"]
            progress = progress_map.get(qid, {})
            user_status = progress.get("status", "not_started")
            
            # Filter by status if requested
            if status and user_status != status:
                continue
            
            result.append({
                "question_id": qid,
                "title": q["title"],
                "difficulty": q["difficulty"],
                "topic": q["topic"],
                "total_testcases": q.get("total_testcases", 0),
                "acceptance_rate": q.get("acceptance_rate", 0),
                "total_submissions": q.get("total_submissions", 0),
                "total_accepted": q.get("total_accepted", 0),
                "user_status": user_status,
                "user_attempts": progress.get("attempts", 0),
                "is_bookmarked": qid in bookmarked_ids
            })
        
        return {
            "status": "success",
            "questions": result,
            "count": len(result)
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/questions/{question_id}")
async def get_question_detail(
    question_id: str,
    user: dict = Depends(verify_client_bound_request)
):
    """
    Get detailed question with sample test cases
    """
    try:
        sidhi_id = user.get("sub")
        
        # Get question
        question = await db.coding_questions.find_one(
            {"question_id": question_id, "is_active": True},
            {"_id": 0}
        )
        
        if not question:
            raise HTTPException(status_code=404, detail="Question not found")
        
        # Filter: Only show sample test cases
        question["testcases"] = [
            tc for tc in question.get("testcases", [])
            if tc.get("is_sample", False)
        ]
        
        # Get user progress
        progress = await db.user_progress.find_one(
            {"sidhi_id": sidhi_id, "question_id": question_id},
            {"_id": 0}
        )
        
        # Get user's note for this question
        note = await db.user_notes.find_one(
            {"sidhi_id": sidhi_id, "question_id": question_id}
        )
        
        # Check if bookmarked
        bookmark = await db.user_bookmarks.find_one({"sidhi_id": sidhi_id})
        is_bookmarked = question_id in bookmark.get("question_ids", []) if bookmark else False
        
        # Get related problems (same topic, similar difficulty)
        related_cursor = db.coding_questions.find(
            {
                "topic": question["topic"],
                "difficulty": question["difficulty"],
                "question_id": {"$ne": question_id},
                "is_active": True
            },
            {"_id": 0, "question_id": 1, "title": 1, "difficulty": 1}
        ).limit(3)
        related_problems = await related_cursor.to_list(length=3)
        
        return {
            "status": "success",
            "question": question,
            "user_progress": progress,
            "user_note": note.get("content") if note else None,
            "is_bookmarked": is_bookmarked,
            "related_problems": related_problems
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/submit")
async def submit_code(
    submission: SubmissionRequest,
    user: dict = Depends(verify_client_bound_request)
):
    """
    Submit code for judging - Returns task_id immediately
    """
    try:
        sidhi_id = user.get("sub")
        
        # Get question with ALL test cases
        question = await db.coding_questions.find_one(
            {"question_id": submission.question_id, "is_active": True}
        )
        
        if not question:
            raise HTTPException(status_code=404, detail="Question not found")
        
        # Prepare test cases for judge
        testcases = [
            {"input": tc["input"], "output": tc["output"]}
            for tc in question.get("testcases", [])
        ]
        
        # Submit to judge service
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{JUDGE_API_URL}/judge",
                json={
                    "language": submission.language,
                    "sourceCode": submission.source_code,
                    "testcases": testcases
                },
                headers={
                    "X-API-Key": JUDGE_API_KEY,
                    "Content-Type": "application/json"
                },
                timeout=10.0
            )
            
            if response.status_code != 200:
                raise HTTPException(
                    status_code=502,
                    detail="Judge service error"
                )
            
            judge_response = response.json()
        
        # Create submission record
        submission_id = f"SUB_{uuid.uuid4()}"
        submission_doc = {
            "submission_id": submission_id,
            "sidhi_id": sidhi_id,
            "question_id": submission.question_id,
            "language": submission.language,
            "source_code": submission.source_code,
            "status": "queued",
            "task_id": judge_response.get("task_id"),
            "submitted_at": datetime.utcnow(),
            "completed_at": None
        }
        
        await db.user_submissions.insert_one(submission_doc)
        
        # Update progress - mark as attempted
        await db.user_progress.update_one(
            {"sidhi_id": sidhi_id, "question_id": submission.question_id},
            {
                "$set": {
                    "status": "attempted",
                    "last_attempted_at": datetime.utcnow()
                },
                "$inc": {"attempts": 1}
            },
            upsert=True
        )
        
        # Increment total submissions for question stats
        await db.coding_questions.update_one(
            {"question_id": submission.question_id},
            {"$inc": {"total_submissions": 1}}
        )
        
        return {
            "status": "success",
            "submission_id": submission_id,
            "task_id": judge_response.get("task_id"),
            "message": "Code submitted successfully. Use /status endpoint to check result."
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status/{submission_id}")
async def get_submission_status(
    submission_id: str,
    user: dict = Depends(verify_client_bound_request)
):
    """
    Check submission status and get result
    """
    try:
        sidhi_id = user.get("sub")
        
        # Get submission
        submission = await db.user_submissions.find_one(
            {"submission_id": submission_id, "sidhi_id": sidhi_id}
        )
        
        if not submission:
            raise HTTPException(status_code=404, detail="Submission not found")
        
        # If already completed, return cached result
        if submission.get("status") != "queued":
            return {
                "status": "success",
                "submission": {
                    "submission_id": submission_id,
                    "question_id": submission["question_id"],
                    "status": submission["status"],
                    "verdict": submission.get("verdict"),
                    "passed_tests": submission.get("passed_tests"),
                    "total_tests": submission.get("total_tests"),
                    "execution_time": submission.get("execution_time"),
                    "error_message": submission.get("error_message"),
                    "submitted_at": submission["submitted_at"],
                    "completed_at": submission.get("completed_at")
                }
            }
        
        # Check judge service
        task_id = submission.get("task_id")
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{JUDGE_API_URL}/status/{task_id}",
                headers={"X-API-Key": JUDGE_API_KEY},
                timeout=5.0
            )
            
            if response.status_code != 200:
                raise HTTPException(status_code=502, detail="Judge service error")
            
            judge_result = response.json()
        
        # If still pending/processing, return status
        if judge_result.get("status") in ["pending", "processing"]:
            return {
                "status": "success",
                "submission": {
                    "submission_id": submission_id,
                    "status": judge_result["status"],
                    "message": "Submission is being processed..."
                }
            }
        
        # Result is ready - update database
        result = judge_result.get("result", {})
        verdict = result.get("verdict", "Unknown")
        
        # Map verdict to status
        status_map = {
            "Accepted": "accepted",
            "Wrong Answer": "wrong_answer",
            "Time Limit Exceeded": "tle",
            "Runtime Error": "runtime_error",
            "Compilation Error": "compilation_error",
            "Memory Limit Exceeded": "mle"
        }
        
        submission_status = status_map.get(verdict, "error")
        
        # Update submission
        update_data = {
            "status": submission_status,
            "verdict": verdict,
            "passed_tests": result.get("passed", 0),
            "total_tests": result.get("total", 0),
            "error_message": result.get("error"),
            "completed_at": datetime.utcnow()
        }
        
        await db.user_submissions.update_one(
            {"submission_id": submission_id},
            {"$set": update_data}
        )
        
        # Update progress if accepted
        if submission_status == "accepted":
            # Increment total accepted for question stats
            await db.coding_questions.update_one(
                {"question_id": submission["question_id"]},
                {"$inc": {"total_accepted": 1}}
            )
            
            # Update acceptance rate
            question = await db.coding_questions.find_one(
                {"question_id": submission["question_id"]},
                {"total_submissions": 1, "total_accepted": 1}
            )
            if question:
                acceptance_rate = (question["total_accepted"] / question["total_submissions"]) * 100
                await db.coding_questions.update_one(
                    {"question_id": submission["question_id"]},
                    {"$set": {"acceptance_rate": round(acceptance_rate, 2)}}
                )
            
            await db.user_progress.update_one(
                {"sidhi_id": sidhi_id, "question_id": submission["question_id"]},
                {
                    "$set": {
                        "status": "solved",
                        "best_submission_id": submission_id,
                        "first_solved_at": datetime.utcnow(),
                        "language_used": submission["language"]
                    }
                },
                upsert=True
            )
        
        return {
            "status": "success",
            "submission": {
                "submission_id": submission_id,
                "question_id": submission["question_id"],
                "status": submission_status,
                "verdict": verdict,
                "passed_tests": result.get("passed", 0),
                "total_tests": result.get("total", 0),
                "error_message": result.get("error"),
                "submitted_at": submission["submitted_at"],
                "completed_at": update_data["completed_at"]
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/my-submissions")
async def get_my_submissions(
    user: dict = Depends(verify_client_bound_request),
    question_id: Optional[str] = None,
    limit: int = Query(50, le=100)
):
    """
    Get user's submission history
    """
    try:
        sidhi_id = user.get("sub")
        
        query = {"sidhi_id": sidhi_id}
        if question_id:
            query["question_id"] = question_id
        
        submissions_cursor = db.user_submissions.find(
            query,
            {
                "_id": 0,
                "source_code": 0  # Don't return source code in list
            }
        ).sort("submitted_at", -1).limit(limit)
        
        submissions = await submissions_cursor.to_list(length=limit)
        
        return {
            "status": "success",
            "submissions": submissions,
            "count": len(submissions)
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/submission/{submission_id}/code")
async def get_submission_code(
    submission_id: str,
    user: dict = Depends(verify_client_bound_request)
):
    """
    Get source code of a specific submission (for viewing previous attempts)
    """
    try:
        sidhi_id = user.get("sub")
        
        submission = await db.user_submissions.find_one(
            {"submission_id": submission_id, "sidhi_id": sidhi_id},
            {"_id": 0, "source_code": 1, "language": 1, "submitted_at": 1, "verdict": 1}
        )
        
        if not submission:
            raise HTTPException(status_code=404, detail="Submission not found")
        
        return {
            "status": "success",
            "submission": submission
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/my-progress")
async def get_my_progress(user: dict = Depends(verify_client_bound_request)):
    """
    Get user's overall progress statistics
    """
    try:
        sidhi_id = user.get("sub")
        
        # Get progress
        progress_cursor = db.user_progress.find({"sidhi_id": sidhi_id})
        progress = await progress_cursor.to_list(length=None)
        
        # Calculate stats
        stats = {
            "total_solved": sum(1 for p in progress if p.get("status") == "solved"),
            "total_attempted": sum(1 for p in progress if p.get("status") in ["attempted", "solved"]),
            "by_difficulty": {
                "easy": 0,
                "medium": 0,
                "hard": 0
            },
            "by_language": {
                "c": 0,
                "cpp": 0,
                "python": 0
            }
        }
        
        # Get difficulty breakdown and language stats
        for p in progress:
            if p.get("status") == "solved":
                q = await db.coding_questions.find_one(
                    {"question_id": p["question_id"]},
                    {"difficulty": 1}
                )
                if q:
                    stats["by_difficulty"][q["difficulty"]] += 1
                
                # Language stats
                lang = p.get("language_used")
                if lang in stats["by_language"]:
                    stats["by_language"][lang] += 1
        
        # Get streak info
        streak_info = await calculate_streak(sidhi_id)
        stats["current_streak"] = streak_info["current_streak"]
        stats["longest_streak"] = streak_info["longest_streak"]
        
        # Get acceptance rate
        total_submissions = await db.user_submissions.count_documents({"sidhi_id": sidhi_id})
        accepted_submissions = await db.user_submissions.count_documents({
            "sidhi_id": sidhi_id,
            "status": "accepted"
        })
        stats["acceptance_rate"] = round((accepted_submissions / total_submissions * 100), 2) if total_submissions > 0 else 0
        
        return {
            "status": "success",
            "sidhi_id": sidhi_id,
            "stats": stats,
            "progress": progress
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/activity-heatmap")
async def get_activity_heatmap(
    user: dict = Depends(verify_client_bound_request),
    days: int = Query(365, le=365)
):
    """
    Get GitHub-style activity heatmap data
    """
    try:
        sidhi_id = user.get("sub")
        
        start_date = datetime.utcnow() - timedelta(days=days)
        
        # Get all submissions in date range
        submissions = await db.user_submissions.find(
            {
                "sidhi_id": sidhi_id,
                "submitted_at": {"$gte": start_date}
            },
            {"submitted_at": 1, "status": 1}
        ).to_list(length=None)
        
        # Group by date
        activity_map = defaultdict(lambda: {"total": 0, "accepted": 0})
        
        for sub in submissions:
            date_key = sub["submitted_at"].strftime("%Y-%m-%d")
            activity_map[date_key]["total"] += 1
            if sub.get("status") == "accepted":
                activity_map[date_key]["accepted"] += 1
        
        # Convert to list format
        heatmap_data = [
            {
                "date": date,
                "count": data["total"],
                "accepted": data["accepted"]
            }
            for date, data in activity_map.items()
        ]
        
        return {
            "status": "success",
            "heatmap": sorted(heatmap_data, key=lambda x: x["date"])
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/leaderboard")
async def get_leaderboard(
    user: dict = Depends(verify_client_bound_request),
    scope: str = Query("global", regex="^(global|friends)$"),
    limit: int = Query(100, le=100)
):
    """
    Get leaderboard rankings
    """
    try:
        sidhi_id = user.get("sub")
        
        # Aggregate user stats
        pipeline = [
            {
                "$group": {
                    "_id": "$sidhi_id",
                    "total_solved": {
                        "$sum": {"$cond": [{"$eq": ["$status", "solved"]}, 1, 0]}
                    },
                    "easy_solved": {
                        "$sum": {"$cond": [
                            {"$and": [
                                {"$eq": ["$status", "solved"]},
                                # We'll need to lookup difficulty from questions
                            ]}, 1, 0
                        ]}
                    }
                }
            },
            {"$sort": {"total_solved": -1}},
            {"$limit": limit}
        ]
        
        # Simplified: Get top users by solved count
        users_progress = await db.user_progress.aggregate([
            {"$match": {"status": "solved"}},
            {"$group": {
                "_id": "$sidhi_id",
                "solved_count": {"$sum": 1}
            }},
            {"$sort": {"solved_count": -1}},
            {"$limit": limit}
        ]).to_list(length=limit)
        
        # Get user details
        leaderboard = []
        for idx, up in enumerate(users_progress, 1):
            user_profile = await db.users_profile.find_one(
                {"sidhi_id": up["_id"]},
                {"username": 1, "email_id": 1}
            )
            
            leaderboard.append({
                "rank": idx,
                "sidhi_id": up["_id"],
                "username": user_profile.get("username", "Anonymous") if user_profile else "Anonymous",
                "solved_count": up["solved_count"],
                "is_me": up["_id"] == sidhi_id
            })
        
        # Get current user's rank
        user_rank = next((item["rank"] for item in leaderboard if item["is_me"]), None)
        
        return {
            "status": "success",
            "leaderboard": leaderboard,
            "my_rank": user_rank,
            "total_users": len(leaderboard)
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/bookmark")
async def toggle_bookmark(
    data: BookmarkRequest,
    user: dict = Depends(verify_client_bound_request)
):
    """
    Bookmark/unbookmark a question
    """
    try:
        sidhi_id = user.get("sub")
        
        # Check if already bookmarked
        bookmark_doc = await db.user_bookmarks.find_one({"sidhi_id": sidhi_id})
        
        if bookmark_doc and data.question_id in bookmark_doc.get("question_ids", []):
            # Remove bookmark
            await db.user_bookmarks.update_one(
                {"sidhi_id": sidhi_id},
                {"$pull": {"question_ids": data.question_id}}
            )
            action = "removed"
        else:
            # Add bookmark
            await db.user_bookmarks.update_one(
                {"sidhi_id": sidhi_id},
                {
                    "$addToSet": {"question_ids": data.question_id},
                    "$setOnInsert": {"created_at": datetime.utcnow()}
                },
                upsert=True
            )
            action = "added"
        
        return {
            "status": "success",
            "action": action,
            "question_id": data.question_id
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/bookmarks")
async def get_bookmarks(user: dict = Depends(verify_client_bound_request)):
    """
    Get all bookmarked questions
    """
    try:
        sidhi_id = user.get("sub")
        
        bookmark_doc = await db.user_bookmarks.find_one({"sidhi_id": sidhi_id})
        
        if not bookmark_doc or not bookmark_doc.get("question_ids"):
            return {
                "status": "success",
                "bookmarks": [],
                "count": 0
            }
        
        # Get question details
        questions = await db.coding_questions.find(
            {"question_id": {"$in": bookmark_doc["question_ids"]}, "is_active": True},
            {"_id": 0, "question_id": 1, "title": 1, "difficulty": 1, "topic": 1}
        ).to_list(length=None)
        
        return {
            "status": "success",
            "bookmarks": questions,
            "count": len(questions)
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/notes")
async def save_note(
    data: NoteRequest,
    user: dict = Depends(verify_client_bound_request)
):
    """
    Save personal note for a question
    """
    try:
        sidhi_id = user.get("sub")
        
        await db.user_notes.update_one(
            {"sidhi_id": sidhi_id, "question_id": data.question_id},
            {
                "$set": {
                    "content": data.content,
                    "updated_at": datetime.utcnow()
                },
                "$setOnInsert": {
                    "created_at": datetime.utcnow()
                }
            },
            upsert=True
        )
        
        return {
            "status": "success",
            "message": "Note saved successfully"
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/topics")
async def get_all_topics(user: dict = Depends(verify_client_bound_request)):
    """
    Get all unique topics with question counts
    """
    try:
        # Aggregate topics
        pipeline = [
            {"$match": {"is_active": True}},
            {"$group": {
                "_id": "$topic",
                "count": {"$sum": 1}
            }},
            {"$sort": {"count": -1}}
        ]
        
        topics = await db.coding_questions.aggregate(pipeline).to_list(length=None)
        
        result = [
            {"topic": t["_id"], "count": t["count"]}
            for t in topics
        ]
        
        return {
            "status": "success",
            "topics": result
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/recent-activity")
async def get_recent_activity(
    user: dict = Depends(verify_client_bound_request),
    limit: int = Query(10, le=50)
):
    """
    Get recent submission activity for dashboard
    """
    try:
        sidhi_id = user.get("sub")
        
        submissions = await db.user_submissions.find(
            {"sidhi_id": sidhi_id},
            {"_id": 0, "question_id": 1, "language": 1, "verdict": 1, "submitted_at": 1}
        ).sort("submitted_at", -1).limit(limit).to_list(length=limit)
        
        # Enrich with question titles
        for sub in submissions:
            question = await db.coding_questions.find_one(
                {"question_id": sub["question_id"]},
                {"title": 1}
            )
            sub["question_title"] = question.get("title", "Unknown") if question else "Unknown"
        
        return {
            "status": "success",
            "activities": submissions
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==================== HELPER FUNCTIONS ====================
# Helper function continuation for router.py

async def calculate_streak(sidhi_id: str):
    """Calculate current and longest streak"""
    submissions = await db.user_submissions.find(
        {"sidhi_id": sidhi_id, "status": "accepted"},
        {"submitted_at": 1}
    ).sort("submitted_at", -1).to_list(length=None)
    
    if not submissions:
        return {"current_streak": 0, "longest_streak": 0}
    
    dates = sorted(set(sub["submitted_at"].date() for sub in submissions), reverse=True)
    
    current_streak = 0
    today = datetime.utcnow().date()
    
    for i, date in enumerate(dates):
        expected_date = today - timedelta(days=i)
        if date == expected_date:
            current_streak += 1
        else:
            break
    
    # Calculate longest streak
    longest_streak = 0
    temp_streak = 1
    
    for i in range(1, len(dates)):
        if (dates[i-1] - dates[i]).days == 1:
            temp_streak += 1
            longest_streak = max(longest_streak, temp_streak)
        else:
            temp_streak = 1
    
    longest_streak = max(longest_streak, temp_streak)
    
    return {
        "current_streak": current_streak,
        "longest_streak": longest_streak
    }
"""
Plagiarism Detection API Router
Endpoints for code similarity analysis

Add to main.py:
from app.plagiarism.router import router as plagiarism_router
app.include_router(plagiarism_router, prefix="/plagiarism")
"""

from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from pydantic import BaseModel, validator
from typing import List, Optional
from datetime import datetime
from motor.motor_asyncio import AsyncIOMotorClient
import os

from app.ai.client_bound_guard import verify_client_bound_request
from app.plagiarism.plagiarism_main import PlagiarismDetector, BatchDetector 
router = APIRouter(tags=["Plagiarism Detection"])

# MongoDB connection
MONGO_URL = os.getenv("MONGO_URL")
client = AsyncIOMotorClient(MONGO_URL)
db = client.lumetrics_db


# ==================== MODELS ====================

class CodeComparisonRequest(BaseModel):
    """Compare two code submissions with AI"""
    code1: str
    code2: str
    language: str
    submission1_id: Optional[str] = "code1"
    submission2_id: Optional[str] = "code2"
    problem_context: Optional[str] = None  # Problem description for better AI analysis
    use_ai: Optional[bool] = True  # Enable/disable AI semantic analysis
    
    @validator('language')
    def validate_language(cls, v):
        if v.lower() not in ['c', 'cpp', 'python']:
            raise ValueError('Language must be c, cpp, or python')
        return v.lower()
    
    @validator('code1', 'code2')
    def validate_code(cls, v):
        if len(v.strip()) == 0:
            raise ValueError('Code cannot be empty')
        if len(v.encode('utf-8')) > 500 * 1024:  # 500KB
            raise ValueError('Code too large (max 500KB)')
        return v


class BatchComparisonRequest(BaseModel):
    """Batch compare multiple submissions"""
    assignment_id: str
    question_number: int
    recompute: bool = False  # Force recomputation


class PlagiarismReportResponse(BaseModel):
    """Enhanced response with AI reasoning"""
    submission1_id: str
    submission2_id: str
    overall_similarity: float
    similarity_level: str
    flag_color: str
    is_likely_ai_generated: bool
    ai_probability: float
    recommendations: List[str]
    confidence: float
    processing_time: float
    layer_results: List[dict]
    # NEW fields
    is_natural_similarity: Optional[bool] = False
    ai_reasoning: Optional[str] = ""


# ==================== ENDPOINTS ====================

@router.post("/compare", response_model=PlagiarismReportResponse)
async def compare_code_submissions(
    request: CodeComparisonRequest,
    user: dict = Depends(verify_client_bound_request)
):
    """
    Compare two code submissions for plagiarism WITH AI SEMANTIC ANALYSIS
    
    Now includes:
    - AI-powered semantic comparison (50% weight)
    - Intelligent boilerplate filtering
    - Natural similarity detection
    - Problem context awareness
    
    The AI understands that similarities in basic constructs (print, def, for, if)
    are natural and not plagiarism. It focuses on unique algorithmic logic.
    """
    try:
        detector = PlagiarismDetector(use_ai=request.use_ai)
        
        report = await detector.compare_submissions(
            code1=request.code1,
            code2=request.code2,
            language=request.language,
            submission1_id=request.submission1_id,
            submission2_id=request.submission2_id,
            problem_context=request.problem_context,
            use_ai_semantic=request.use_ai
        )
        
        # Convert layer results to dict
        layer_dicts = []
        for layer in report.layer_results:
            layer_dicts.append({
                "layer_name": layer.layer_name,
                "similarity_score": round(layer.similarity_score, 4),
                "confidence": round(layer.confidence, 4),
                "execution_time": round(layer.execution_time, 4),
                "details": layer.details
            })
        
        return PlagiarismReportResponse(
            submission1_id=report.submission1_id,
            submission2_id=report.submission2_id,
            overall_similarity=round(report.overall_similarity, 4),
            similarity_level=report.similarity_level.value,
            flag_color=report.flag_color.value,
            is_likely_ai_generated=report.is_likely_ai_generated,
            ai_probability=round(report.ai_probability, 4),
            recommendations=report.recommendations,
            confidence=round(report.confidence, 4),
            processing_time=round(report.processing_time, 4),
            layer_results=layer_dicts,
            is_natural_similarity=report.is_natural_similarity,
            ai_reasoning=report.ai_reasoning
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



@router.post("/batch-analyze/{assignment_id}")
async def batch_analyze_assignment(
    assignment_id: str,
    question_number: int,
    background_tasks: BackgroundTasks,
    user: dict = Depends(verify_client_bound_request),
    recompute: bool = False
):
    """
    Analyze all submissions for an assignment question for plagiarism
    
    This is a background task - returns immediately with task_id
    Use /batch-status/{task_id} to check progress
    """
    try:
        sidhi_id = user.get("sub")
        
        # Check if user is teacher of this assignment
        assignment = await db.assignments.find_one({
            "assignment_id": assignment_id,
            "teacher_sidhi_id": sidhi_id
        })
        
        if not assignment:
            raise HTTPException(
                status_code=403,
                detail="Not authorized to analyze this assignment"
            )
        
        # Check cache if not forcing recompute
        if not recompute:
            cached = await db.plagiarism_cache.find_one({
                "assignment_id": assignment_id,
                "question_number": question_number
            })
            
            if cached:
                # Check if cache is recent (< 1 hour old)
                cache_age = (datetime.utcnow() - cached.get("last_computed", datetime.min)).total_seconds()
                if cache_age < 3600:
                    return {
                        "status": "success",
                        "message": "Using cached results",
                        "cache_age_seconds": int(cache_age),
                        "results": cached.get("submission_pairs", [])
                    }
        
        # Get all submissions for this question
        submissions_cursor = db.assignment_submissions.find({
            "assignment_id": assignment_id
        })
        
        submissions_list = await submissions_cursor.to_list(length=None)
        
        # Extract code for the specific question
        submission_data = []
        for sub in submissions_list:
            files = sub.get("files", [])
            for file in files:
                if file.get("question_number") == question_number:
                    submission_data.append((
                        sub["submission_id"],
                        file["file_content"],
                        file["language"]
                    ))
        
        if len(submission_data) < 2:
            return {
                "status": "success",
                "message": "Not enough submissions to compare",
                "submission_count": len(submission_data)
            }
        
        # Schedule background task
        task_id = f"BATCH_{assignment_id}_Q{question_number}_{int(datetime.utcnow().timestamp())}"
        
        background_tasks.add_task(
            run_batch_analysis,
            task_id,
            assignment_id,
            question_number,
            submission_data
        )
        
        return {
            "status": "queued",
            "task_id": task_id,
            "message": f"Analyzing {len(submission_data)} submissions",
            "total_pairs": len(submission_data) * (len(submission_data) - 1) // 2,
            "estimated_time_seconds": len(submission_data) * 2
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/batch-status/{task_id}")
async def get_batch_status(
    task_id: str,
    user: dict = Depends(verify_client_bound_request)
):
    """
    Check status of batch plagiarism analysis
    """
    try:
        # Check task status in database
        task = await db.plagiarism_tasks.find_one({"task_id": task_id})
        
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        
        # Check authorization
        if task.get("requester_id") != user.get("sub"):
            raise HTTPException(status_code=403, detail="Not authorized")
        
        return {
            "status": "success",
            "task_id": task_id,
            "task_status": task.get("status"),  # queued, processing, completed, failed
            "progress": task.get("progress", 0),
            "total": task.get("total", 0),
            "started_at": task.get("started_at"),
            "completed_at": task.get("completed_at"),
            "error": task.get("error"),
            "flagged_count": task.get("flagged_count", 0),
            "results_url": f"/plagiarism/report/{task.get('assignment_id')}/q{task.get('question_number')}"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/report/{assignment_id}/q{question_number}")
async def get_plagiarism_report(
    assignment_id: str,
    question_number: int,
    user: dict = Depends(verify_client_bound_request),
    min_similarity: float = 0.60
):
    """
    Get plagiarism report for an assignment question
    
    Shows only submissions above similarity threshold
    """
    try:
        sidhi_id = user.get("sub")
        
        # Verify authorization
        assignment = await db.assignments.find_one({
            "assignment_id": assignment_id,
            "teacher_sidhi_id": sidhi_id
        })
        
        if not assignment:
            raise HTTPException(status_code=403, detail="Not authorized")
        
        # Get cached results
        cache = await db.plagiarism_cache.find_one({
            "assignment_id": assignment_id,
            "question_number": question_number
        })
        
        if not cache:
            return {
                "status": "success",
                "message": "No analysis found. Run /batch-analyze first",
                "flagged_pairs": []
            }
        
        # Filter by similarity threshold
        all_pairs = cache.get("submission_pairs", [])
        flagged_pairs = [
            p for p in all_pairs
            if p.get("similarity_score", 0) >= min_similarity
        ]
        
        # Sort by similarity (highest first)
        flagged_pairs.sort(key=lambda x: x.get("similarity_score", 0), reverse=True)
        
        return {
            "status": "success",
            "assignment_id": assignment_id,
            "question_number": question_number,
            "total_submissions": cache.get("total_submissions", 0),
            "total_pairs_analyzed": len(all_pairs),
            "flagged_count": len(flagged_pairs),
            "last_analyzed": cache.get("last_computed"),
            "flagged_pairs": flagged_pairs
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/compare-detail/{sub1_id}/{sub2_id}")
async def get_detailed_comparison(
    sub1_id: str,
    sub2_id: str,
    assignment_id: str,
    user: dict = Depends(verify_client_bound_request)
):
    """
    Get detailed side-by-side comparison of two submissions
    """
    try:
        sidhi_id = user.get("sub")
        
        # Verify authorization
        assignment = await db.assignments.find_one({
            "assignment_id": assignment_id,
            "teacher_sidhi_id": sidhi_id
        })
        
        if not assignment:
            raise HTTPException(status_code=403, detail="Not authorized")
        
        # Get submissions
        sub1 = await db.assignment_submissions.find_one({"submission_id": sub1_id})
        sub2 = await db.assignment_submissions.find_one({"submission_id": sub2_id})
        
        if not sub1 or not sub2:
            raise HTTPException(status_code=404, detail="Submissions not found")
        
        # Get cached comparison
        cache = await db.plagiarism_cache.find_one({
            "assignment_id": assignment_id
        })
        
        if cache:
            # Find comparison in cache
            pairs = cache.get("submission_pairs", [])
            for pair in pairs:
                if (pair.get("sub1_id") == sub1_id and pair.get("sub2_id") == sub2_id) or \
                   (pair.get("sub1_id") == sub2_id and pair.get("sub2_id") == sub1_id):
                    return {
                        "status": "success",
                        "submission1": {
                            "id": sub1_id,
                            "student_id": sub1.get("student_sidhi_id"),
                            "submitted_at": sub1.get("submitted_at")
                        },
                        "submission2": {
                            "id": sub2_id,
                            "student_id": sub2.get("student_sidhi_id"),
                            "submitted_at": sub2.get("submitted_at")
                        },
                        "comparison": pair
                    }
        
        return {
            "status": "success",
            "message": "Comparison not found in cache. Run batch analysis first."
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==================== BACKGROUND TASK ====================

async def run_batch_analysis(
    task_id: str,
    assignment_id: str,
    question_number: int,
    submissions: List[tuple]
):
    """
    Background task to run batch plagiarism detection
    """
    try:
        # Create task record
        task_doc = {
            "task_id": task_id,
            "assignment_id": assignment_id,
            "question_number": question_number,
            "status": "processing",
            "progress": 0,
            "total": len(submissions) * (len(submissions) - 1) // 2,
            "started_at": datetime.utcnow(),
            "flagged_count": 0
        }
        await db.plagiarism_tasks.insert_one(task_doc)
        
        # Run batch detection
        batch_detector = BatchDetector()
        
        def update_progress(completed, total):
            # Update progress in database
            db.plagiarism_tasks.update_one(
                {"task_id": task_id},
                {"$set": {"progress": completed}}
            )
        
        reports = await batch_detector.compare_all_pairs(
            submissions,
            progress_callback=update_progress
        )
        
        # Convert reports to storable format
        pairs = []
        flagged_count = 0
        
        for report in reports:
            pair_doc = {
                "sub1_id": report.submission1_id,
                "sub2_id": report.submission2_id,
                "similarity_score": report.overall_similarity,
                "similarity_level": report.similarity_level.value,
                "flag_color": report.flag_color.value,
                "is_ai_generated": report.is_likely_ai_generated,
                "ai_probability": report.ai_probability,
                "confidence": report.confidence
            }
            pairs.append(pair_doc)
            
            if report.overall_similarity >= 0.60:
                flagged_count += 1
        
        # Store results in cache
        cache_doc = {
            "assignment_id": assignment_id,
            "question_number": question_number,
            "total_submissions": len(submissions),
            "submission_pairs": pairs,
            "last_computed": datetime.utcnow(),
            "flagged_count": flagged_count
        }
        
        await db.plagiarism_cache.update_one(
            {
                "assignment_id": assignment_id,
                "question_number": question_number
            },
            {"$set": cache_doc},
            upsert=True
        )
        
        # Update task status
        await db.plagiarism_tasks.update_one(
            {"task_id": task_id},
            {
                "$set": {
                    "status": "completed",
                    "completed_at": datetime.utcnow(),
                    "flagged_count": flagged_count
                }
            }
        )
        
    except Exception as e:
        # Update task with error
        await db.plagiarism_tasks.update_one(
            {"task_id": task_id},
            {
                "$set": {
                    "status": "failed",
                    "error": str(e),
                    "completed_at": datetime.utcnow()
                }
            }
        )
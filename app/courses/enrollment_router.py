from fastapi import APIRouter, HTTPException, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase
from typing import List
from app.courses.models import EnrollmentCreate, EnrollmentResponse
from app.courses.database import (
    enroll_user, get_enrollment, get_user_enrollments,
    get_course, get_course_questions
)
from app.courses.dependencies import get_db,get_current_user_id,get_sidhi_id

router = APIRouter(prefix="/api/enrollments", tags=["Enrollments"])

# ==================== ENROLLMENT ====================

@router.post("/enroll")
async def enroll_endpoint(
    enrollment: EnrollmentCreate,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
    sidhi_id: str = Depends(get_sidhi_id)
):
    """Enroll in course"""
    # Verify course exists and is published
    course = await get_course(db, enrollment.course_id)
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    
    if course["status"] not in ["PUBLISHED", "ACTIVE"]:
        raise HTTPException(status_code=400, detail="Course not available for enrollment")
    
    enrollment_id = await enroll_user(db, enrollment.course_id, user_id, sidhi_id)
    
    return {
        "success": True,
        "enrollment_id": enrollment_id,
        "certificate_id": f"CERT_{enrollment_id.split('_')[1]}",
        "message": "Enrolled successfully"
    }

@router.get("/my-courses")
async def get_my_courses(
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    """Get all enrolled courses for user"""
    enrollments = await get_user_enrollments(db, user_id)
    
    # Enrich with course data
    result = []
    for enr in enrollments:
        course = await get_course(db, enr["course_id"])
        if course:
            result.append({
                "enrollment_id": enr["enrollment_id"],
                "course": {
                    "course_id": course["course_id"],
                    "title": course["title"],
                    "description": course["description"],
                    "domain": course["domain"],
                    "thumbnail_url": course.get("thumbnail_url")
                },
                "progress": enr.get("progress", 0.0),
                "league": enr.get("current_league", "BRONZE"),
                "league_points": enr.get("league_points", 0),
                "certificate_id": enr.get("certificate_id"),
                "enrolled_at": enr["enrolled_at"]
            })
    
    return {
        "enrollments": result,
        "count": len(result)
    }

@router.get("/course/{course_id}/progress")
async def get_course_progress(
    course_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    """Get progress in specific course"""
    enrollment = await get_enrollment(db, course_id, user_id)
    if not enrollment:
        raise HTTPException(status_code=404, detail="Not enrolled in this course")
    
    # Get total questions
    total_questions = await db.course_questions.count_documents({
        "course_id": course_id,
        "is_active": True
    })
    
    solved_count = len(enrollment.get("solved_questions", []))
    progress = (solved_count / total_questions * 100) if total_questions > 0 else 0
    
    return {
        "course_id": course_id,
        "total_questions": total_questions,
        "solved_questions": solved_count,
        "progress": round(progress, 2),
        "league": enrollment.get("current_league", "BRONZE"),
        "league_points": enrollment.get("league_points", 0),
        "certificate_id": enrollment.get("certificate_id")
    }

@router.get("/course/{course_id}/questions")
async def get_available_questions(
    course_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    """Get available questions (excluding solved)"""
    # Verify enrollment
    enrollment = await get_enrollment(db, course_id, user_id)
    if not enrollment:
        raise HTTPException(status_code=404, detail="Not enrolled in this course")
    
    questions = await get_course_questions(db, course_id, user_id)
    
    return {
        "course_id": course_id,
        "questions": questions,
        "count": len(questions),
        "solved_count": len(enrollment.get("solved_questions", []))
    }

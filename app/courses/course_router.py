from fastapi import APIRouter, HTTPException, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase
from typing import List
from app.courses.models import CourseCreate, CourseUpdate, CoursePublish, ModuleCreate, QuestionCreate
from app.courses.database import (
    create_course, get_course, update_course, publish_course, list_courses,
    create_question, get_question
)

from app.courses.dependencies import get_db,get_current_user_id
router = APIRouter(prefix="/api/courses", tags=["Course Management"])

# ==================== COURSE CRUD ====================

@router.post("/create")
async def create_course_endpoint(
    course: CourseCreate,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    """Create new course (admin/instructor only)"""
    try:
        course_id = await create_course(db, course.dict(), user_id)
        return {
            "success": True,
            "course_id": course_id,
            "message": "Course created successfully"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{course_id}")
async def get_course_endpoint(
    course_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db)
):
    """Get course details"""
    course = await get_course(db, course_id)
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    return course

@router.put("/{course_id}")
async def update_course_endpoint(
    course_id: str,
    updates: CourseUpdate,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    """Update course (DRAFT only)"""
    course = await get_course(db, course_id)
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    
    if course["creator_id"] != user_id:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    success = await update_course(db, course_id, {k: v for k, v in updates.dict().items() if v is not None})
    if not success:
        raise HTTPException(status_code=400, detail="Cannot update published course")
    
    return {"success": True, "message": "Course updated"}

@router.post("/{course_id}/publish")
async def publish_course_endpoint(
    course_id: str,
    confirm: CoursePublish,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    """Publish course (locks editing)"""
    course = await get_course(db, course_id)
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    
    if course["creator_id"] != user_id:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    success = await publish_course(db, course_id)
    if not success:
        raise HTTPException(status_code=400, detail="Course already published")
    
    return {
        "success": True,
        "message": "Course published successfully",
        "course_id": course_id
    }

@router.get("/")
async def list_courses_endpoint(
    course_type: str = None,
    domain: str = None,
    skip: int = 0,
    limit: int = 20,
    db: AsyncIOMotorDatabase = Depends(get_db)
):
    """List all courses with filters"""
    filters = {}
    if course_type:
        filters["course_type"] = course_type
    if domain:
        filters["domain"] = domain
    
    courses = await list_courses(db, filters, skip, limit)
    return {
        "courses": courses,
        "count": len(courses),
        "skip": skip,
        "limit": limit
    }

# ==================== QUESTION MANAGEMENT ====================

@router.post("/questions/create")
async def create_question_endpoint(
    question: QuestionCreate,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    """Create question for course"""
    # Verify course ownership
    course = await get_course(db, question.course_id)
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    
    if course["creator_id"] != user_id:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    if course["status"] not in ["DRAFT", "PUBLISHED"]:
        raise HTTPException(status_code=400, detail="Cannot add questions to active course")
    
    question_id = await create_question(db, question.dict())
    return {
        "success": True,
        "question_id": question_id,
        "message": "Question created successfully"
    }

@router.get("/questions/{question_id}")
async def get_question_endpoint(
    question_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db)
):
    """Get question details (sample only for students)"""
    question = await get_question(db, question_id)
    if not question:
        raise HTTPException(status_code=404, detail="Question not found")
    
    # Hide test case outputs
    if "test_cases" in question:
        for tc in question["test_cases"]:
            if not tc.get("is_sample", False):
                tc.pop("output", None)
    
    return question

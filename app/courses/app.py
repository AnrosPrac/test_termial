"""
Lumetrix Course System - Main Application
Handles OFFICIAL and CREATOR courses with gamified learning
"""

from fastapi import FastAPI, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase
import os

# Import routers
from app.courses.course_router import router as course_router
from app.courses.enrollment_router import router as enrollment_router
from app.courses.submission_router import router as submission_router
from app.courses.leaderboard_router import router as leaderboard_router
from app.courses.certificate_router import router as certificate_router

# Import auth from client_bound_guard (matches your main.py pattern)
from app.ai.client_bound_guard import verify_client_bound_request
from app.courses.dependencies import get_db_instance
# Get MongoDB instance from main
# This will be imported where needed


async def create_course_indexes():
    """Create MongoDB indexes for performance"""
    db = get_db_instance()
    
    # Courses
    await db.courses.create_index("course_id", unique=True)
    await db.courses.create_index([("course_type", 1), ("status", 1)])
    await db.courses.create_index("creator_id")
    
    # Course Questions
    await db.course_questions.create_index("question_id", unique=True)
    await db.course_questions.create_index([("course_id", 1), ("is_active", 1)])
    await db.course_questions.create_index("difficulty")
    
    # Enrollments
    await db.course_enrollments.create_index("enrollment_id", unique=True)
    await db.course_enrollments.create_index([("user_id", 1), ("course_id", 1)], unique=True)
    await db.course_enrollments.create_index("certificate_id", unique=True)
    await db.course_enrollments.create_index([("course_id", 1), ("league_points", -1)])
    
    # Submissions
    await db.course_submissions.create_index("submission_id", unique=True)
    await db.course_submissions.create_index([("user_id", 1), ("course_id", 1)])
    await db.course_submissions.create_index([("question_id", 1), ("user_id", 1)])
    await db.course_submissions.create_index("submitted_at")
    
    # Alumni Board
    await db.alumni_board.create_index([("final_points", -1), ("graduation_date", 1)])
    await db.alumni_board.create_index("user_id", unique=True)
    
    print("✅ Course system indexes created")

# ==================== ROUTER SETUP ====================

def setup_course_routes(app: FastAPI):
    """Register all course-related routers"""
    
    app.include_router(course_router, prefix="/api/courses")
    app.include_router(enrollment_router, prefix="/courses")
    app.include_router(submission_router, prefix="/courses")
    app.include_router(leaderboard_router, prefix="/courses")
    app.include_router(certificate_router, prefix="/courses")
    
    print("✅ Course routes registered")

# ==================== STARTUP ====================

async def startup_course_system():
    """Initialize course system on app startup"""
    await create_course_indexes()
    print("🚀 Course system initialized")

from fastapi import Depends
from motor.motor_asyncio import AsyncIOMotorDatabase
from app.ai.client_bound_guard import verify_client_bound_request

def get_db_instance():
    """Get database from main module"""
    from app.main import db
    return db

# ==================== DEPENDENCY FUNCTIONS ====================

async def get_db() -> AsyncIOMotorDatabase:
    """Database dependency"""
    return get_db_instance()

async def get_current_user_id(user: str = Depends(verify_client_bound_request)):
    """
    Extract user_id from authenticated request
    user is the public_key from verify_client_bound_request
    """
    # In your system, the public_key IS the user identifier
    # You can fetch user details from DB if needed
    db = get_db_instance()
    user_id = user.get("sub")
    user_record = await db.user_profiles.find_one({"user_id": user_id})
    if user_record:
        return user_record.get("user_id")
    # Fallback: use public_key as user_id
    return user_id

async def get_sidhi_id(user: str = Depends(verify_client_bound_request)):
    """Extract sidhi_id from authenticated request"""
    db = get_db_instance()
    user_id = user.get("sub")
    user_record = await db.user_profiles.find_one({"user_id": user_id})
    if user_record:
        return user_record.get("sidhi_id")
    return None
# app/courses/dependencies.py

from fastapi import Depends, HTTPException
from motor.motor_asyncio import AsyncIOMotorDatabase

async def verify_enrollment(
    course_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
) -> dict:
    """Verify user is enrolled in course"""
    enrollment = await db.course_enrollments.find_one({
        "course_id": course_id,
        "user_id": user_id,
        "is_active": True
    })
    
    if not enrollment:
        raise HTTPException(
            status_code=403,
            detail="Not enrolled in this course. Please enroll first."
        )
    
    return enrollment
from fastapi import Depends
from motor.motor_asyncio import AsyncIOMotorDatabase
from app.ai.client_bound_guard import verify_client_bound_request

def get_db_instance():
    """Get database from main module"""
    from main import db
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
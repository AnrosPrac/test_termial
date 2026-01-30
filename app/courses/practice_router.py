from fastapi import APIRouter, HTTPException, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase
from typing import List, Optional
from pydantic import BaseModel
from app.courses.app import get_db, get_current_user_id

router = APIRouter(prefix="/api/practice", tags=["Practice Samples"])

# ==================== MODELS ====================

class SampleQuestionResponse(BaseModel):
    sample_id: str
    chapter: int
    type: str
    difficulty: str
    question: str
    answer: Optional[str] = None  # Only for read questions

# ==================== ENDPOINTS ====================

@router.get("/samples")
async def get_practice_samples(
    chapter: Optional[int] = None,
    difficulty: Optional[str] = None,
    skip: int = 0,
    limit: int = 20,
    show_unread_first: bool = True,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    """
    Get practice sample questions (5000 theory/MCQ questions)
    
    Features:
    - Unread questions appear first
    - Read questions go to last pages
    - No grading, just read tracking
    """
    
    # Build query
    query = {}
    if chapter:
        query["chapter"] = chapter
    if difficulty:
        query["difficulty"] = difficulty
    
    # Get user's read samples
    user_progress = await db.user_sample_progress.find_one({"user_id": user_id})
    read_samples = set(user_progress.get("read_samples", [])) if user_progress else set()
    
    if show_unread_first:
        # Get unread samples first
        query["sample_id"] = {"$nin": list(read_samples)}
        unread_cursor = db.training_samples.find(query).skip(skip).limit(limit)
        samples = await unread_cursor.to_list(length=limit)
        
        # If we need more to fill the page, get read samples
        if len(samples) < limit:
            remaining = limit - len(samples)
            read_query = query.copy()
            read_query["sample_id"] = {"$in": list(read_samples)}
            read_cursor = db.training_samples.find(read_query).limit(remaining)
            read_samples_list = await read_cursor.to_list(length=remaining)
            samples.extend(read_samples_list)
    else:
        # Just get samples normally
        cursor = db.training_samples.find(query).skip(skip).limit(limit)
        samples = await cursor.to_list(length=limit)
    
    # Mark which ones are read
    for sample in samples:
        sample["is_read"] = sample["sample_id"] in read_samples
        # Don't show answer unless it's been read
        if not sample["is_read"]:
            sample.pop("answer", None)
    
    total = await db.training_samples.count_documents(query)
    
    return {
        "samples": samples,
        "count": len(samples),
        "total": total,
        "unread_count": total - len(read_samples),
        "skip": skip,
        "limit": limit
    }

@router.post("/samples/{sample_id}/mark-read")
async def mark_sample_read(
    sample_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    """Mark a sample question as read"""
    
    # Verify sample exists
    sample = await db.training_samples.find_one({"sample_id": sample_id})
    if not sample:
        raise HTTPException(status_code=404, detail="Sample not found")
    
    # Add to user's read list
    await db.user_sample_progress.update_one(
        {"user_id": user_id},
        {
            "$addToSet": {"read_samples": sample_id},
            "$setOnInsert": {"user_id": user_id}
        },
        upsert=True
    )
    
    return {
        "success": True,
        "sample_id": sample_id,
        "answer": sample.get("answer"),  # Now they can see the answer
        "message": "Sample marked as read"
    }

@router.get("/samples/{sample_id}")
async def get_sample_detail(
    sample_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    """Get specific sample question"""
    
    sample = await db.training_samples.find_one({"sample_id": sample_id})
    if not sample:
        raise HTTPException(status_code=404, detail="Sample not found")
    
    # Check if read
    user_progress = await db.user_sample_progress.find_one({"user_id": user_id})
    is_read = sample_id in user_progress.get("read_samples", []) if user_progress else False
    
    sample["is_read"] = is_read
    
    # Only show answer if read
    if not is_read:
        sample.pop("answer", None)
    
    return sample

@router.get("/stats")
async def get_practice_stats(
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    """Get user's practice statistics"""
    
    user_progress = await db.user_sample_progress.find_one({"user_id": user_id})
    read_count = len(user_progress.get("read_samples", [])) if user_progress else 0
    
    total_samples = await db.training_samples.count_documents({})
    
    # Get breakdown by difficulty
    easy_total = await db.training_samples.count_documents({"difficulty": "easy"})
    medium_total = await db.training_samples.count_documents({"difficulty": "medium"})
    hard_total = await db.training_samples.count_documents({"difficulty": "hard"})
    
    read_samples = user_progress.get("read_samples", []) if user_progress else []
    
    easy_read = await db.training_samples.count_documents({
        "difficulty": "easy",
        "sample_id": {"$in": read_samples}
    })
    medium_read = await db.training_samples.count_documents({
        "difficulty": "medium",
        "sample_id": {"$in": read_samples}
    })
    hard_read = await db.training_samples.count_documents({
        "difficulty": "hard",
        "sample_id": {"$in": read_samples}
    })
    
    return {
        "total_samples": total_samples,
        "read_samples": read_count,
        "unread_samples": total_samples - read_count,
        "progress_percentage": round((read_count / total_samples * 100) if total_samples > 0 else 0, 2),
        "by_difficulty": {
            "easy": {"total": easy_total, "read": easy_read},
            "medium": {"total": medium_total, "read": medium_read},
            "hard": {"total": hard_total, "read": hard_read}
        }
    }

# app/training/router.py
from fastapi import APIRouter, HTTPException, Depends, Query
from typing import Optional, List
from pydantic import BaseModel
from motor.motor_asyncio import AsyncIOMotorClient
import os
from app.ai.client_bound_guard import verify_client_bound_request

router = APIRouter()

# MongoDB Configuration
MONGO_URL = os.getenv("MONGO_URL")
client = AsyncIOMotorClient(MONGO_URL)
db = client.lumetrics_db  # Your database name


class TrainingSample(BaseModel):
    sample_id: str
    chapter: int
    type: str
    difficulty: str
    question: str
    answer: str


@router.get("/samples")
async def get_training_samples(
    user: dict = Depends(verify_client_bound_request),
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(20, ge=1, le=100, description="Items per page"),
    chapter: Optional[int] = Query(None, description="Filter by chapter"),
    type: Optional[str] = Query(None, description="Filter by type (program/realworld)"),
    difficulty: Optional[str] = Query(None, description="Filter by difficulty (easy/medium/hard)")
):
    """
    Get paginated training samples with optional filters.
    
    Query Parameters:
    - page: Page number (default: 1)
    - limit: Items per page (default: 20, max: 100)
    - chapter: Filter by chapter number
    - type: Filter by type (program/realworld)
    - difficulty: Filter by difficulty (easy/medium/hard)
    """
    try:
        # Build filter query
        filter_query = {}
        
        if chapter is not None:
            filter_query["chapter"] = chapter
        
        if type:
            if type not in ["program", "realworld"]:
                raise HTTPException(
                    status_code=400,
                    detail="Type must be 'program' or 'realworld'"
                )
            filter_query["type"] = type
        
        if difficulty:
            if difficulty not in ["easy", "medium", "hard"]:
                raise HTTPException(
                    status_code=400,
                    detail="Difficulty must be 'easy', 'medium', or 'hard'"
                )
            filter_query["difficulty"] = difficulty
        
        # Calculate skip
        skip = (page - 1) * limit
        
        # Get total count
        total_count = await db.training_samples.count_documents(filter_query)
        
        # Fetch samples
        samples_cursor = db.training_samples.find(
            filter_query,
            {"_id": 0}
        ).skip(skip).limit(limit)
        
        samples = await samples_cursor.to_list(length=limit)
        
        # Calculate pagination metadata
        total_pages = (total_count + limit - 1) // limit
        has_next = page < total_pages
        has_prev = page > 1
        
        return {
            "status": "success",
            "data": samples,
            "pagination": {
                "current_page": page,
                "total_pages": total_pages,
                "total_items": total_count,
                "items_per_page": limit,
                "has_next": has_next,
                "has_prev": has_prev
            },
            "filters_applied": filter_query
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/chapters")
async def get_available_chapters(
    user: dict = Depends(verify_client_bound_request)
):
    """
    Get list of all available chapters with their question counts.
    Useful for building chapter navigation in the frontend.
    """
    try:
        chapter_stats = await db.training_samples.aggregate([
            {
                "$group": {
                    "_id": "$chapter",
                    "total_count": {"$sum": 1},
                    "program_count": {
                        "$sum": {"$cond": [{"$eq": ["$type", "program"]}, 1, 0]}
                    },
                    "realworld_count": {
                        "$sum": {"$cond": [{"$eq": ["$type", "realworld"]}, 1, 0]}
                    },
                    "easy_count": {
                        "$sum": {"$cond": [{"$eq": ["$difficulty", "easy"]}, 1, 0]}
                    },
                    "medium_count": {
                        "$sum": {"$cond": [{"$eq": ["$difficulty", "medium"]}, 1, 0]}
                    },
                    "hard_count": {
                        "$sum": {"$cond": [{"$eq": ["$difficulty", "hard"]}, 1, 0]}
                    }
                }
            },
            {"$sort": {"_id": 1}}
        ]).to_list(length=None)
        
        chapters = [
            {
                "chapter": item["_id"],
                "total_questions": item["total_count"],
                "breakdown": {
                    "by_type": {
                        "program": item["program_count"],
                        "realworld": item["realworld_count"]
                    },
                    "by_difficulty": {
                        "easy": item["easy_count"],
                        "medium": item["medium_count"],
                        "hard": item["hard_count"]
                    }
                }
            }
            for item in chapter_stats
        ]
        
        return {
            "status": "success",
            "total_chapters": len(chapters),
            "chapters": chapters
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/filters")
async def get_available_filters(
    user: dict = Depends(verify_client_bound_request)
):
    """
    Get all available filter options for the frontend.
    Returns unique values for chapters, types, and difficulties.
    """
    try:
        # Get unique chapters
        chapters = await db.training_samples.distinct("chapter")
        chapters = sorted(chapters)
        
        # Get unique types
        types = await db.training_samples.distinct("type")
        types = sorted(types)
        
        # Get unique difficulties
        difficulties = await db.training_samples.distinct("difficulty")
        # Sort difficulties by severity
        difficulty_order = {"easy": 1, "medium": 2, "hard": 3}
        difficulties = sorted(difficulties, key=lambda x: difficulty_order.get(x, 99))
        
        return {
            "status": "success",
            "filters": {
                "chapters": chapters,
                "types": types,
                "difficulties": difficulties
            },
            "metadata": {
                "chapter_count": len(chapters),
                "type_count": len(types),
                "difficulty_count": len(difficulties)
            }
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/samples/{sample_id}")
async def get_sample_by_id(
    sample_id: str,
    user: dict = Depends(verify_client_bound_request)
):
    """
    Get a specific training sample by its sample_id.
    
    Path Parameters:
    - sample_id: The sample_id to fetch (e.g., c_ch1_q_000122)
    """
    try:
        sample = await db.training_samples.find_one(
            {"sample_id": sample_id},
            {"_id": 0}
        )
        
        if not sample:
            raise HTTPException(
                status_code=404,
                detail=f"Sample with id '{sample_id}' not found"
            )
        
        return {
            "status": "success",
            "data": sample
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stats")
async def get_training_stats(
    user: dict = Depends(verify_client_bound_request)
):
    """
    Get statistics about the training dataset.
    
    Returns counts by:
    - Total samples
    - Samples by type (program/realworld)
    - Samples by difficulty (easy/medium/hard)
    - Samples by chapter
    - Available chapters list
    - Available difficulties list
    - Available types list
    """
    try:
        # Total count
        total_count = await db.training_samples.count_documents({})
        
        # Count by type
        type_stats = await db.training_samples.aggregate([
            {"$group": {"_id": "$type", "count": {"$sum": 1}}},
            {"$sort": {"_id": 1}}
        ]).to_list(length=None)
        
        # Count by difficulty
        difficulty_stats = await db.training_samples.aggregate([
            {"$group": {"_id": "$difficulty", "count": {"$sum": 1}}},
            {"$sort": {"_id": 1}}
        ]).to_list(length=None)
        
        # Count by chapter
        chapter_stats = await db.training_samples.aggregate([
            {"$group": {"_id": "$chapter", "count": {"$sum": 1}}},
            {"$sort": {"_id": 1}}
        ]).to_list(length=None)
        
        # Count by type and difficulty combined
        type_difficulty_stats = await db.training_samples.aggregate([
            {
                "$group": {
                    "_id": {"type": "$type", "difficulty": "$difficulty"},
                    "count": {"$sum": 1}
                }
            },
            {"$sort": {"_id.type": 1, "_id.difficulty": 1}}
        ]).to_list(length=None)
        
        # Count by chapter and difficulty
        chapter_difficulty_stats = await db.training_samples.aggregate([
            {
                "$group": {
                    "_id": {"chapter": "$chapter", "difficulty": "$difficulty"},
                    "count": {"$sum": 1}
                }
            },
            {"$sort": {"_id.chapter": 1, "_id.difficulty": 1}}
        ]).to_list(length=None)
        
        # Count by chapter and type
        chapter_type_stats = await db.training_samples.aggregate([
            {
                "$group": {
                    "_id": {"chapter": "$chapter", "type": "$type"},
                    "count": {"$sum": 1}
                }
            },
            {"$sort": {"_id.chapter": 1, "_id.type": 1}}
        ]).to_list(length=None)
        
        # Get all unique chapters, difficulties, and types
        available_chapters = sorted([item["_id"] for item in chapter_stats])
        available_difficulties = sorted([item["_id"] for item in difficulty_stats])
        available_types = sorted([item["_id"] for item in type_stats])
        
        return {
            "status": "success",
            "total_samples": total_count,
            "available_filters": {
                "chapters": available_chapters,
                "difficulties": available_difficulties,
                "types": available_types
            },
            "by_type": {item["_id"]: item["count"] for item in type_stats},
            "by_difficulty": {item["_id"]: item["count"] for item in difficulty_stats},
            "by_chapter": {str(item["_id"]): item["count"] for item in chapter_stats},
            "by_type_and_difficulty": [
                {
                    "type": item["_id"]["type"],
                    "difficulty": item["_id"]["difficulty"],
                    "count": item["count"]
                }
                for item in type_difficulty_stats
            ],
            "by_chapter_and_difficulty": [
                {
                    "chapter": item["_id"]["chapter"],
                    "difficulty": item["_id"]["difficulty"],
                    "count": item["count"]
                }
                for item in chapter_difficulty_stats
            ],
            "by_chapter_and_type": [
                {
                    "chapter": item["_id"]["chapter"],
                    "type": item["_id"]["type"],
                    "count": item["count"]
                }
                for item in chapter_type_stats
            ]
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/random")
async def get_random_sample(
    user: dict = Depends(verify_client_bound_request),
    type: Optional[str] = Query(None, description="Filter by type (program/realworld)"),
    difficulty: Optional[str] = Query(None, description="Filter by difficulty"),
    chapter: Optional[int] = Query(None, description="Filter by chapter")
):
    """
    Get a random training sample with optional filters.
    Useful for practice or quiz features.
    """
    try:
        # Build filter query
        filter_query = {}
        
        if type:
            if type not in ["program", "realworld"]:
                raise HTTPException(
                    status_code=400,
                    detail="Type must be 'program' or 'realworld'"
                )
            filter_query["type"] = type
        
        if difficulty:
            if difficulty not in ["easy", "medium", "hard"]:
                raise HTTPException(
                    status_code=400,
                    detail="Difficulty must be 'easy', 'medium', or 'hard'"
                )
            filter_query["difficulty"] = difficulty
        
        if chapter is not None:
            filter_query["chapter"] = chapter
        
        # Get random sample using aggregation
        pipeline = [
            {"$match": filter_query},
            {"$sample": {"size": 1}},
            {"$project": {"_id": 0}}
        ]
        
        result = await db.training_samples.aggregate(pipeline).to_list(length=1)
        
        if not result:
            raise HTTPException(
                status_code=404,
                detail="No samples found matching the filters"
            )
        
        return {
            "status": "success",
            "data": result[0],
            "filters_applied": filter_query
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/search")
async def search_samples(
    user: dict = Depends(verify_client_bound_request),
    query: str = Query(..., min_length=3, description="Search query (min 3 characters)"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100)
):
    """
    Search training samples by question or answer content.
    
    Query Parameters:
    - query: Search text (minimum 3 characters)
    - page: Page number
    - limit: Items per page
    """
    try:
        # Create text search query
        search_filter = {
            "$or": [
                {"question": {"$regex": query, "$options": "i"}},
                {"answer": {"$regex": query, "$options": "i"}},
                {"sample_id": {"$regex": query, "$options": "i"}}
            ]
        }
        
        # Calculate skip
        skip = (page - 1) * limit
        
        # Get total count
        total_count = await db.training_samples.count_documents(search_filter)
        
        # Fetch samples
        samples_cursor = db.training_samples.find(
            search_filter,
            {"_id": 0}
        ).skip(skip).limit(limit)
        
        samples = await samples_cursor.to_list(length=limit)
        
        # Calculate pagination metadata
        total_pages = (total_count + limit - 1) // limit
        
        return {
            "status": "success",
            "query": query,
            "data": samples,
            "pagination": {
                "current_page": page,
                "total_pages": total_pages,
                "total_items": total_count,
                "items_per_page": limit,
                "has_next": page < total_pages,
                "has_prev": page > 1
            }
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
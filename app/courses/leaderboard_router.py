from fastapi import APIRouter, HTTPException, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase
from typing import List, Optional
from app.courses.models import LeaderboardEntry, LeaderboardResponse

from app.courses.dependencies import get_db,get_current_user_id
router = APIRouter(tags=["Leaderboards"])

# ==================== LEADERBOARD QUERIES ====================

def serialize_mongo(doc: dict) -> dict:
    if "_id" in doc:
        doc["_id"] = str(doc["_id"])
    return doc

def serialize_many(docs: list[dict]) -> list[dict]:
    return [serialize_mongo(doc) for doc in docs]


async def get_course_leaderboard(
    db: AsyncIOMotorDatabase,
    course_id: str,
    skip: int = 0,
    limit: int = 50
) -> List[dict]:
    """
    Get leaderboard for specific course
    Output strictly matches LeaderboardEntry model
    """

    pipeline = [
        # 1️⃣ only active enrollments of this course
        {
            "$match": {
                "course_id": course_id,
                "is_active": True
            }
        },

        # 2️⃣ join user profile
        {
            "$lookup": {
                "from": "users_profile",
                "localField": "user_id",
                "foreignField": "user_id",
                "as": "user"
            }
        },
        {"$unwind": "$user"},

        # 3️⃣ shape EXACTLY like LeaderboardEntry
        {
            "$project": {
                "_id": 0,

                "user_id": 1,

                # required string → never null
                "sidhi_id": {"$ifNull": ["$sidhi_id", ""]},

                "username": {"$ifNull": ["$user.username", "Anonymous"]},
                "college": "$user.college",

                "league": "$current_league",

                # model names must match
                "total_points": {"$ifNull": ["$league_points", 0]},

                "problems_solved": {
                    "$size": {"$ifNull": ["$solved_questions", []]}
                },

                "avg_efficiency": {"$ifNull": ["$avg_efficiency", 0.0]}
            }
        },

        # 4️⃣ CORRECT sorting (IMPORTANT)
        {
            "$sort": {
                "total_points": -1,
                "problems_solved": -1
            }
        },

        # 5️⃣ pagination
        {"$skip": skip},
        {"$limit": limit}
    ]

    results = await db.course_enrollments.aggregate(pipeline).to_list(length=limit)

    # 6️⃣ rank injection (after pagination)
    for idx, row in enumerate(results):
        row["rank"] = skip + idx + 1

    return results


async def get_global_leaderboard(
    db: AsyncIOMotorDatabase,
    skip: int = 0,
    limit: int = 50,
    filters: dict = {}
) -> List[dict]:
    """Get global leaderboard (OFFICIAL courses only)"""
    match_stage = {"is_active": True}
    
    # Join with courses to filter OFFICIAL only
    pipeline = [
        {"$match": match_stage},
        {"$lookup": {
            "from": "courses",
            "localField": "course_id",
            "foreignField": "course_id",
            "as": "course"
        }},
        {"$unwind": "$course"},
        {"$match": {"course.course_type": "OFFICIAL"}},
        {"$group": {
            "_id": "$user_id",
            "sidhi_id": {"$first": "$sidhi_id"},
            "total_points": {"$sum": "$league_points"},
            "total_solved": {"$sum": {"$size": {"$ifNull": ["$solved_questions", []]}}},
            "avg_efficiency": {"$avg": {"$ifNull": ["$avg_efficiency", 0]}},
            "current_league": {"$max": "$current_league"}
        }},
        {"$lookup": {
            "from": "users_profile",
            "localField": "_id",
            "foreignField": "user_id",
            "as": "user"
        }},
        {"$unwind": "$user"},
        {"$project": {
            "user_id": "$_id",
            "sidhi_id": 1,
            "username": "$user.username",
            "college": "$user.college",
            "department": "$user.department",
            "state": "$user.state",
            "league": "$current_league",
            "points": "$total_points",
            "solved": "$total_solved",
            "avg_efficiency": 1
        }},
        {"$sort": {"points": -1, "solved": -1}},
        {"$skip": skip},
        {"$limit": limit}
    ]
    
    results = await db.course_enrollments.aggregate(pipeline).to_list(length=limit)
    
    for idx, entry in enumerate(results):
        entry["rank"] = skip + idx + 1
    
    return results

async def get_filtered_leaderboard(
    db: AsyncIOMotorDatabase,
    scope: str,
    value: str,
    skip: int = 0,
    limit: int = 50
) -> List[dict]:
    """Get leaderboard filtered by college/state/department"""
    pipeline = [
        {"$match": {"is_active": True}},
        {"$lookup": {
            "from": "courses",
            "localField": "course_id",
            "foreignField": "course_id",
            "as": "course"
        }},
        {"$unwind": "$course"},
        {"$match": {"course.course_type": "OFFICIAL"}},
        {"$group": {
            "_id": "$user_id",
            "sidhi_id": {"$first": "$sidhi_id"},
            "total_points": {"$sum": "$league_points"},
            "total_solved": {"$sum": {"$size": {"$ifNull": ["$solved_questions", []]}}},
            "avg_efficiency": {"$avg": {"$ifNull": ["$avg_efficiency", 0]}},
            "current_league": {"$max": "$current_league"}
        }},
        {"$lookup": {
            "from": "users_profile",
            "localField": "_id",
            "foreignField": "user_id",
            "as": "user"
        }},
        {"$unwind": "$user"},
        {"$match": {f"user.{scope}": value}},
        {"$project": {
            "user_id": "$_id",
            "sidhi_id": 1,
            "username": "$user.username",
            "college": "$user.college",
            "department": "$user.department",
            "league": "$current_league",
            "points": "$total_points",
            "solved": "$total_solved",
            "avg_efficiency": 1
        }},
        {"$sort": {"points": -1, "solved": -1}},
        {"$skip": skip},
        {"$limit": limit}
    ]
    
    results = await db.course_enrollments.aggregate(pipeline).to_list(length=limit)
    
    for idx, entry in enumerate(results):
        entry["rank"] = skip + idx + 1
    
    return results

async def get_alumni_leaderboard(
    db: AsyncIOMotorDatabase,
    skip: int = 0,
    limit: int = 50
) -> List[dict]:
    """Get alumni hall of fame (frozen rankings)"""
    pipeline = [
        {"$match": {"is_alumni": True}},
        {"$sort": {"final_points": -1, "graduation_date": 1}},
        {"$skip": skip},
        {"$limit": limit},
        {"$lookup": {
            "from": "users_profile",
            "localField": "user_id",
            "foreignField": "user_id",
            "as": "user"
        }},
        {"$unwind": "$user"},
        {"$project": {
            "user_id": 1,
            "sidhi_id": 1,
            "username": "$user.username",
            "college": "$user.college",
            "league": "$final_league",
            "points": "$final_points",
            "solved": "$total_problems_solved",
            "graduation_year": {"$year": "$graduation_date"}
        }}
    ]
    
    results = await db.alumni_board.aggregate(pipeline).to_list(length=limit)
    
    for idx, entry in enumerate(results):
        entry["rank"] = skip + idx + 1
    
    return results

# ==================== ENDPOINTS ====================

@router.get("/course/{course_id}")
async def course_leaderboard(
    course_id: str,
    skip: int = 0,
    limit: int = 50,
    db: AsyncIOMotorDatabase = Depends(get_db)
):
    """Get leaderboard for specific course"""
    entries = await get_course_leaderboard(db, course_id, skip, limit)
    total = await db.course_enrollments.count_documents({"course_id": course_id, "is_active": True})
    
    return LeaderboardResponse(
        scope="course",
        entries=[LeaderboardEntry(**e) for e in entries],
        total_users=total,
        page=skip // limit + 1,
        page_size=limit
    )

@router.get("/global")
async def global_leaderboard(
    skip: int = 0,
    limit: int = 50,
    db: AsyncIOMotorDatabase = Depends(get_db)
):
    """Get global leaderboard (OFFICIAL courses only)"""
    entries = await get_global_leaderboard(db, skip, limit)
    
    return {
        "scope": "global",
        "entries": entries,
        "page": skip // limit + 1,
        "page_size": limit
    }

@router.get("/college/{college_name}")
async def college_leaderboard(
    college_name: str,
    skip: int = 0,
    limit: int = 50,
    db: AsyncIOMotorDatabase = Depends(get_db)
):
    """Get college-level leaderboard"""
    entries = await get_filtered_leaderboard(db, "college", college_name, skip, limit)
    
    return {
        "scope": "college",
        "college": college_name,
        "entries": entries,
        "page": skip // limit + 1,
        "page_size": limit
    }

@router.get("/state/{state_name}")
async def state_leaderboard(
    state_name: str,
    skip: int = 0,
    limit: int = 50,
    db: AsyncIOMotorDatabase = Depends(get_db)
):
    """Get state-level leaderboard"""
    entries = await get_filtered_leaderboard(db, "state", state_name, skip, limit)
    
    return {
        "scope": "state",
        "state": state_name,
        "entries": entries,
        "page": skip // limit + 1,
        "page_size": limit
    }

@router.get("/department/{college_name}/{department}")
async def department_leaderboard(
    college_name: str,
    department: str,
    skip: int = 0,
    limit: int = 50,
    db: AsyncIOMotorDatabase = Depends(get_db)
):
    """Get department-level leaderboard"""
    # Custom pipeline for college + department filter
    pipeline = [
        {"$match": {"is_active": True}},
        {"$lookup": {
            "from": "courses",
            "localField": "course_id",
            "foreignField": "course_id",
            "as": "course"
        }},
        {"$unwind": "$course"},
        {"$match": {"course.course_type": "OFFICIAL"}},
        {"$group": {
            "_id": "$user_id",
            "sidhi_id": {"$first": "$sidhi_id"},
            "total_points": {"$sum": "$league_points"},
            "total_solved": {"$sum": {"$size": {"$ifNull": ["$solved_questions", []]}}},
            "current_league": {"$max": "$current_league"}
        }},
        {"$lookup": {
            "from": "users_profile",
            "localField": "_id",
            "foreignField": "user_id",
            "as": "user"
        }},
        {"$unwind": "$user"},
        {"$match": {
            "user.college": college_name,
            "user.department": department
        }},
        {"$project": {
            "user_id": "$_id",
            "sidhi_id": 1,
            "username": "$user.username",
            "college": "$user.college",
            "department": "$user.department",
            "league": "$current_league",
            "points": "$total_points",
            "solved": "$total_solved"
        }},
        {"$sort": {"points": -1, "solved": -1}},
        {"$skip": skip},
        {"$limit": limit}
    ]
    
    entries = await db.course_enrollments.aggregate(pipeline).to_list(length=limit)
    
    for idx, entry in enumerate(entries):
        entry["rank"] = skip + idx + 1
    
    return {
        "scope": "department",
        "college": college_name,
        "department": department,
        "entries": entries,
        "page": skip // limit + 1,
        "page_size": limit
    }

@router.get("/alumni")
async def alumni_hall_of_fame(
    skip: int = 0,
    limit: int = 50,
    db: AsyncIOMotorDatabase = Depends(get_db)
):
    """Get alumni hall of fame"""
    entries = await get_alumni_leaderboard(db, skip, limit)
    total = await db.alumni_board.count_documents({"is_alumni": True})
    
    return {
        "scope": "alumni",
        "entries": entries,
        "total_alumni": total,
        "page": skip // limit + 1,
        "page_size": limit
    }

@router.get("/my-rank/{course_id}")
async def get_my_rank(
    course_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    """Get user's rank in course leaderboard"""
    # Get user's enrollment
    enrollment = await db.course_enrollments.find_one({
        "course_id": course_id,
        "user_id": user_id
    })
    
    if not enrollment:
        raise HTTPException(status_code=404, detail="Not enrolled")
    
    # Count users with higher points
    rank = await db.course_enrollments.count_documents({
        "course_id": course_id,
        "is_active": True,
        "league_points": {"$gt": enrollment.get("league_points", 0)}
    }) + 1
    
    return {
        "rank": rank,
        "league": enrollment.get("current_league", "BRONZE"),
        "points": enrollment.get("league_points", 0),
        "solved": len(enrollment.get("solved_questions", []))
    }

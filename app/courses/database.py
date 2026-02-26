from motor.motor_asyncio import AsyncIOMotorDatabase
from datetime import datetime
from typing import List, Optional, Dict, Any
import uuid
from app.courses.models import CourseType, CourseStatus, LeagueTier

# ==================== COURSE CRUD ====================
from bson import ObjectId

def serialize_mongo(doc: dict) -> dict:
    if "_id" in doc:
        doc["_id"] = str(doc["_id"])
    return doc

def serialize_many(docs: list[dict]) -> list[dict]:
    return [serialize_mongo(doc) for doc in docs]


async def create_course(db: AsyncIOMotorDatabase, course_data: dict, creator_id: str) -> str:
    """
    Create new course
    🆕 SECURITY: Initializes with default FREE pricing
    """
    course_id = f"COURSE_{uuid.uuid4().hex[:12].upper()}"
    
    course = {
        "course_id": course_id,
        "title": course_data["title"],
        "description": course_data["description"],
        "course_type": course_data["course_type"],
        "domain": course_data["domain"],
        "creator_id": creator_id,
        "instructor_id": course_data.get("instructor_id"),
        "status": CourseStatus.DRAFT,
        "thumbnail_url": course_data.get("thumbnail_url"),
        "tags": course_data.get("tags", []),
        "external_resources": course_data.get("external_resources", []),
        
        # 🆕 DEFAULT PRICING (FREE until instructor sets price)
        "pricing": {
            "is_free": True,
            "price": 0,
            "original_price": 0,
            "currency": "INR",
            "tier_access": [],
            "discount_percentage": 0,
            "pricing_set": False  # Track if instructor explicitly set pricing
        },
        
        # 🆕 PURCHASE STATS
        "purchase_stats": {
            "total_purchases": 0,
            "revenue_generated": 0
        },
        
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
        "published_at": None,
        "stats": {
            "enrollments": 0,
            "completions": 0,
            "avg_rating": 0.0
        }
    }
    
    await db.courses.insert_one(course)
    return course_id

async def get_course(db: AsyncIOMotorDatabase, course_id: str) -> Optional[dict]:
    """Get course by ID"""
    return await db.courses.find_one({"course_id": course_id})

async def update_course(db: AsyncIOMotorDatabase, course_id: str, updates: dict) -> bool:
    """
    Update course (only if DRAFT)
    🔒 SECURITY: Cannot update after published
    """
    course = await get_course(db, course_id)
    if not course or course["status"] != CourseStatus.DRAFT:
        return False
    
    updates["updated_at"] = datetime.utcnow()
    result = await db.courses.update_one(
        {"course_id": course_id, "status": CourseStatus.DRAFT},
        {"$set": updates}
    )
    return result.modified_count > 0

async def publish_course(db: AsyncIOMotorDatabase, course_id: str) -> dict:
    """
    Publish course (lock rules)
    🆕 SECURITY: Validates pricing is set before publishing
    
    Returns:
    {
        "success": bool,
        "message": str,
        "errors": list (if validation fails)
    }
    """
    course = await db.courses.find_one({"course_id": course_id})
    
    if not course:
        return {"success": False, "message": "Course not found", "errors": ["Course not found"]}
    
    if course["status"] != CourseStatus.DRAFT:
        return {"success": False, "message": "Course already published", "errors": ["Already published"]}
    
    # 🔒 VALIDATION: Check pricing is explicitly set
    pricing = course.get("pricing", {})
    
    validation_errors = []
    
    # Check 1: Pricing must be explicitly set by instructor
    if not pricing.get("pricing_set", False):
        validation_errors.append("Pricing not configured. Please set course pricing before publishing.")
    
    # Check 2: If paid course, price must be > 0
    if not pricing.get("is_free", True) and pricing.get("price", 0) <= 0:
        validation_errors.append("Paid course must have price greater than 0.")
    
    # If validation fails, return errors
    if validation_errors:
        return {
            "success": False,
            "message": "Cannot publish course. Please fix the following issues:",
            "errors": validation_errors
        }
    
    # All validations passed - publish course
    result = await db.courses.update_one(
        {"course_id": course_id, "status": CourseStatus.DRAFT},
        {"$set": {
            "status": CourseStatus.PUBLISHED,
            "published_at": datetime.utcnow()
        }}
    )
    
    if result.modified_count > 0:
        return {
            "success": True,
            "message": "Course published successfully",
            "errors": []
        }
    else:
        return {
            "success": False,
            "message": "Failed to publish course",
            "errors": ["Database update failed"]
        }

async def list_courses(db: AsyncIOMotorDatabase, filters: dict, skip: int = 0, limit: int = 20) -> List[dict]:
    """List courses with filters"""
    query = {}
    if filters.get("course_type"):
        query["course_type"] = filters["course_type"]
    if filters.get("domain"):
        query["domain"] = filters["domain"]
    if filters.get("status"):
        query["status"] = filters["status"]
    else:
        query["status"] = {"$in": [CourseStatus.PUBLISHED, CourseStatus.ACTIVE]}
    
    cursor = db.courses.find(query).skip(skip).limit(limit).sort("created_at", -1)
    return await cursor.to_list(length=limit)

# ==================== ENROLLMENT CRUD ====================

async def enroll_user(db: AsyncIOMotorDatabase, course_id: str, user_id: str, sidhi_id: str) -> str:
    """Enroll user in course"""
    # Check if already enrolled
    existing = await db.course_enrollments.find_one({
        "course_id": course_id,
        "user_id": user_id
    })
    if existing:
        return existing["enrollment_id"]
    
    enrollment_id = f"ENR_{uuid.uuid4().hex[:12].upper()}"
    certificate_id = f"CERT_{uuid.uuid4().hex[:12].upper()}"
    
    enrollment = {
        "enrollment_id": enrollment_id,
        "course_id": course_id,
        "user_id": user_id,
        "sidhi_id": sidhi_id,
        "certificate_id": certificate_id,
        "enrolled_at": datetime.utcnow(),
        "progress": 0.0,
        "current_league": LeagueTier.BRONZE,
        "league_points": 0,
        "solved_questions": [],
        "is_active": True
    }
    
    await db.course_enrollments.insert_one(enrollment)
    
    # Increment course enrollment count
    await db.courses.update_one(
        {"course_id": course_id},
        {"$inc": {"stats.enrollments": 1}}
    )
    
    return enrollment_id

async def get_enrollment(db: AsyncIOMotorDatabase, course_id: str, user_id: str) -> Optional[dict]:
    """Get user enrollment"""
    return await db.course_enrollments.find_one({
        "course_id": course_id,
        "user_id": user_id
    })

async def get_user_enrollments(db: AsyncIOMotorDatabase, user_id: str) -> List[dict]:
    """Get all enrollments for user"""
    cursor = db.course_enrollments.find({"user_id": user_id, "is_active": True})
    return await cursor.to_list(length=100)

# ==================== QUESTION CRUD ====================

async def create_question(db: AsyncIOMotorDatabase, question_data: dict) -> str:
    """Create course question (supports both software & hardware)"""

    question_id = f"Q_{uuid.uuid4().hex[:8].upper()}"

    language = question_data["language"].lower()

    software_langs = {"c", "cpp", "python", "java", "javascript"}
    hardware_langs = {"verilog", "vhdl", "systemverilog"}

    # -------------------------
    # COMMON FIELDS
    # -------------------------
    question = {
        "question_id": question_id,
        "course_id": question_data["course_id"],
        "module_id": question_data.get("module_id"),
        "title": question_data["title"],
        "description": question_data["description"],
        "difficulty": question_data["difficulty"],
        "language": language,
        "problem_type": question_data.get("problem_type", "coding"),
        "points": question_data.get("points", 100),
        "created_at": datetime.utcnow(),
        "is_active": True
    }

    # -------------------------
    # SOFTWARE QUESTION
    # -------------------------
    if language in software_langs:
        question.update({
            "judge_type": "software",
            "test_cases": question_data.get("test_cases", []),
            "time_limit": question_data.get("time_limit", 2.0),
            "memory_limit": question_data.get("memory_limit", 256)
        })

    # -------------------------
    # HARDWARE QUESTION
    # -------------------------
    elif language in hardware_langs:
        # Extract from the nested config provided by QuestionCreate model
        hdl = question_data.get("hdl_config") or {}
        
        question.update({
            "judge_type": "hardware",
            "problem_id": hdl.get("problem_id") or question_data.get("problem_id"),
            "module_name": hdl.get("module_name") or question_data.get("module_name"),
            "testbench_template": hdl.get("testbench_template") or question_data.get("testbench_template"),
            "time_limit": hdl.get("time_limit", 30)
        })
    else:
        raise ValueError(f"Unsupported language: {language}")

    await db.course_questions.insert_one(question)
    return question_id


async def get_question(db: AsyncIOMotorDatabase, question_id: str) -> Optional[dict]:
    """Get question details"""
    return await db.course_questions.find_one({"question_id": question_id})

async def get_course_questions(db: AsyncIOMotorDatabase, course_id: str, user_id: str) -> List[dict]:
    """Get questions for course (excluding solved ones)"""
    # Get solved questions
    enrollment = await get_enrollment(db, course_id, user_id)
    solved_ids = enrollment.get("solved_questions", []) if enrollment else []
    
    # Get unsolved questions
    cursor = db.course_questions.find({
        "course_id": course_id,
        "question_id": {"$nin": solved_ids},
        "is_active": True
    }).sort("difficulty", 1)
    
    questions = await cursor.to_list(length=None)

    # Remove test case outputs (only show inputs)
    for q in questions:
        if "test_cases" in q:
            for tc in q["test_cases"]:
                if not tc.get("is_sample", False):
                    tc.pop("output", None)

    return serialize_many(questions)

import uuid
from datetime import datetime

async def create_sample(db, data: dict) -> str:
    sample_id = f"SAMP_{uuid.uuid4().hex[:10].upper()}"

    doc = {
        "sample_id": sample_id,
        "course_id": data["course_id"],
        "chapter": data["chapter"],
        "type": data["type"],
        "difficulty": data["difficulty"],
        "question": data["question"],
        "answer": data["answer"],
        "created_at": datetime.utcnow()
    }

    await db.training_samples.insert_one(doc)
    return sample_id

import uuid
from datetime import datetime

async def bulk_create_samples(db, course_id: str, samples: list):
    docs = []

    for s in samples:
        docs.append({
            "sample_id": f"SAMP_{uuid.uuid4().hex[:10].upper()}",
            "course_id": course_id,
            "chapter": s.chapter,
            "type": s.type,
            "difficulty": s.difficulty,
            "question": s.question,
            "answer": s.answer,
            "created_at": datetime.utcnow()
        })

    if docs:
        await db.training_samples.insert_many(docs)

    return len(docs)


# ==================== SUBMISSION CRUD ====================

async def create_submission(db: AsyncIOMotorDatabase, submission_data: dict) -> str:
    """Create submission record"""
    submission_id = f"SUB_{uuid.uuid4().hex[:12].upper()}"
    
    submission = {
        "submission_id": submission_id,
        "course_id": submission_data["course_id"],
        "question_id": submission_data["question_id"],
        "user_id": submission_data["user_id"],
        "code": submission_data["code"],
        "language": submission_data["language"],
        "status": "queued",
        "verdict": None,
        "result": None,
        "score": None,
        "submitted_at": datetime.utcnow(),
        "graded_at": None
    }
    
    await db.course_submissions.insert_one(submission)
    return submission_id

async def update_submission_result(db: AsyncIOMotorDatabase, submission_id: str, result: dict) -> bool:
    """Update submission with judge result"""
    updates = {
        "status": "completed",
        "verdict": result.get("verdict"),
        "result": result,
        "graded_at": datetime.utcnow()
    }
    
    # Calculate score
    if result.get("verdict") == "Accepted":
        updates["score"] = 100.0
    elif result.get("passed"):
        updates["score"] = (result["passed"] / result["total"]) * 100
    else:
        updates["score"] = 0.0
    
    res = await db.course_submissions.update_one(
        {"submission_id": submission_id},
        {"$set": updates}
    )
    return res.modified_count > 0

async def get_submission(db: AsyncIOMotorDatabase, submission_id: str) -> Optional[dict]:
    """Get submission by ID"""
    return await db.course_submissions.find_one({"submission_id": submission_id})

async def mark_question_solved(db: AsyncIOMotorDatabase, course_id: str, user_id: str, question_id: str) -> bool:
    """Mark question as solved (permanent)"""
    result = await db.course_enrollments.update_one(
        {"course_id": course_id, "user_id": user_id},
        {"$addToSet": {"solved_questions": question_id}}
    )
    return result.modified_count > 0

# ==================== LEAGUE OPERATIONS ====================

# ── Points per difficulty ─────────────────────────────────────────────────────
# ~300 questions assumed: 150 easy / 100 medium / 50 hard
# Max possible = 150×100 + 100×250 + 50×500 = 65,000 pts
#
# League thresholds are set so:
#   SILVER  ≈ solving ~4-5 easy problems   → very achievable, feels great
#   GOLD    ≈ ~15 easy OR ~8 medium
#   PLATINUM≈ solid easy+medium mix
#   DIAMOND ≈ needs hard problems
#   MYTHIC  ≈ serious grinder
#   LEGEND  ≈ ~92% of max — true mastery

DIFFICULTY_BASE_POINTS = {
    "easy":   100,
    "medium": 250,
    "hard":   500,
}
DEFAULT_BASE_POINTS = 100  # fallback if difficulty unknown

LEAGUE_THRESHOLDS = {
    LeagueTier.BRONZE:   0,
    LeagueTier.SILVER:   2_000,
    LeagueTier.GOLD:     6_000,
    LeagueTier.PLATINUM: 14_000,
    LeagueTier.DIAMOND:  26_000,
    LeagueTier.MYTHIC:   42_000,
    LeagueTier.LEGEND:   60_000,
}

# Expose as plain dict for routers that need it (certificate page, frontend)
LEAGUE_THRESHOLDS_PLAIN = {k.value: v for k, v in LEAGUE_THRESHOLDS.items()}
LEAGUE_ORDER = ["BRONZE", "SILVER", "GOLD", "PLATINUM", "DIAMOND", "MYTHIC", "LEGEND"]


def get_base_points_for_question(question: dict) -> int:
    """
    Return base points for a question.
    Uses the question's stored 'points' field if set by instructor,
    otherwise derives from difficulty.
    """
    stored = question.get("points")
    if stored and stored != 100:
        # Instructor explicitly set a custom value — respect it
        return stored
    # Derive from difficulty
    difficulty = (question.get("difficulty") or "easy").lower()
    return DIFFICULTY_BASE_POINTS.get(difficulty, DEFAULT_BASE_POINTS)


def calculate_league(points: int) -> LeagueTier:
    """Calculate league tier from total points."""
    for tier in reversed(LEAGUE_ORDER):
        if points >= LEAGUE_THRESHOLDS[LeagueTier(tier)]:
            return LeagueTier(tier)
    return LeagueTier.BRONZE


async def promote_to_alumni(db: AsyncIOMotorDatabase, user_id: str, course_id: str, enrollment: dict) -> None:
    """
    When a student hits LEGEND, snapshot their stats into alumni_board.
    Their enrollment remains active (they keep course access).
    Their rank is retired from live leaderboard into hall of fame.
    Called automatically inside update_league_points when LEGEND is reached.
    """
    existing = await db.alumni_board.find_one({"user_id": user_id, "course_id": course_id})
    if existing:
        # Already alumni for this course — just update final stats
        await db.alumni_board.update_one(
            {"user_id": user_id, "course_id": course_id},
            {"$set": {
                "final_points":          enrollment.get("league_points", 0),
                "final_league":          LeagueTier.LEGEND,
                "total_problems_solved": len(enrollment.get("solved_questions", [])),
                "avg_efficiency":        enrollment.get("avg_efficiency", 0.0),
                "updated_at":            datetime.utcnow(),
            }}
        )
        return

    await db.alumni_board.insert_one({
        "user_id":               user_id,
        "course_id":             course_id,
        "enrollment_id":         enrollment.get("enrollment_id"),
        "sidhi_id":              enrollment.get("sidhi_id"),
        "final_points":          enrollment.get("league_points", 0),
        "final_league":          LeagueTier.LEGEND,
        "total_problems_solved": len(enrollment.get("solved_questions", [])),
        "avg_efficiency":        enrollment.get("avg_efficiency", 0.0),
        "is_alumni":             True,
        "graduation_date":       datetime.utcnow(),
    })


async def update_league_points(
    db:                   AsyncIOMotorDatabase,
    user_id:              str,
    points_delta:         int,
    course_id:            str  = None,
    efficiency_multiplier: float = 1.0,
) -> dict:
    """
    Award points to a student for a specific course enrollment.

    Returns:
        {
            "new_points":   int,
            "new_league":   LeagueTier,
            "league_up":    bool,   ← True if they crossed a new tier
            "is_legend":    bool,   ← True if they just hit LEGEND
        }
    """
    query = {"user_id": user_id}
    if course_id:
        query["course_id"] = course_id

    enrollment = await db.course_enrollments.find_one(query)
    if not enrollment:
        return {"new_points": 0, "new_league": LeagueTier.BRONZE, "league_up": False, "is_legend": False}

    old_league = LeagueTier(enrollment.get("current_league", LeagueTier.BRONZE))
    new_points = enrollment.get("league_points", 0) + points_delta
    new_league = calculate_league(new_points)
    league_up  = new_league != old_league

    # Rolling average efficiency — weighted by solved count
    prev_eff   = enrollment.get("avg_efficiency", 0.0)
    solved_cnt = max(len(enrollment.get("solved_questions", [])), 1)
    new_eff    = round(((prev_eff * (solved_cnt - 1)) + efficiency_multiplier) / solved_cnt, 4)

    await db.course_enrollments.update_one(
        query,
        {"$set": {
            "league_points":  new_points,
            "current_league": new_league,
            "avg_efficiency": new_eff,
        }}
    )

    # Alumni promotion when LEGEND is reached
    is_legend = new_league == LeagueTier.LEGEND
    if is_legend and old_league != LeagueTier.LEGEND:
        updated_enrollment = {**enrollment, "league_points": new_points, "avg_efficiency": new_eff}
        await promote_to_alumni(db, user_id, course_id or enrollment.get("course_id"), updated_enrollment)

    return {
        "new_points": new_points,
        "new_league": new_league,
        "league_up":  league_up,
        "is_legend":  is_legend,
    }
from fastapi import APIRouter, HTTPException, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase
from typing import List
from app.courses.models import CourseCreate, CourseUpdate, CoursePublish, ModuleCreate, QuestionCreate
from app.courses.database import (
    create_course, get_course, update_course, publish_course, list_courses,
    create_question, get_question
)
import uuid
from pydantic import BaseModel
from datetime import datetime,timedelta
from typing import Optional
from app.courses.dependencies import get_db,get_current_user_id
router = APIRouter( tags=["Course Management"])

# ==================== COURSE CRUD ====================
from bson import ObjectId
import re

def normalize_youtube_url(url: str) -> str:
    """
    Convert any youtube link to embed format
    """

    # already embed
    if "embed/" in url:
        return url

    # watch?v=
    match = re.search(r"v=([^&]+)", url)
    if match:
        return f"https://www.youtube.com/embed/{match.group(1)}"

    # youtu.be/
    match = re.search(r"youtu\.be/([^?]+)", url)
    if match:
        return f"https://www.youtube.com/embed/{match.group(1)}"

    return url

def serialize_mongo(doc: dict) -> dict:
    doc["_id"] = str(doc["_id"])
    return doc

def serialize_many(docs: list[dict]) -> list[dict]:
    return [{**doc, "_id": str(doc["_id"])} for doc in docs]

def serialize(doc: dict):
    doc["_id"] = str(doc["_id"])
    return doc


async def verify_course_owner(db, course_id, user_id):
    course = await db.courses.find_one({"course_id": course_id})
    if not course:
        raise HTTPException(404, "Course not found")

    if course["creator_id"] != user_id:
        raise HTTPException(403, "Not authorized")

    return course


async def verify_module_owner(db, module_id, user_id):
    module = await db.modules.find_one({"module_id": module_id})
    if not module:
        raise HTTPException(404, "Module not found")

    await verify_course_owner(db, module["course_id"], user_id)
    return module
class LessonProgressUpdate(BaseModel):
    watched_seconds: int = 0
    completed: bool = False

class ModuleCreate(BaseModel):
    course_id: str
    title: str
    order: int = 1


class ModuleUpdate(BaseModel):
    title: Optional[str] = None
    order: Optional[int] = None


class LessonCreate(BaseModel):
    module_id: str
    title: str
    video_url: str
    order: int = 1
    start_time: int = 0
    end_time: Optional[int] = None
    duration: Optional[int] = None


class LessonUpdate(BaseModel):
    title: Optional[str] = None
    video_url: Optional[str] = None
    order: Optional[int] = None
    start_time: Optional[int] = None
    end_time: Optional[int] = None
    duration: Optional[int] = None


class ReorderPayload(BaseModel):
    order: List[str]


class TimestampItem(BaseModel):
    title: str
    start: int
    end: Optional[int] = None

from app.ai.client_bound_guard import verify_client_bound_request
class TimestampBulkCreate(BaseModel):
    module_id: str
    video_url: str
    lessons: List[TimestampItem]

@router.get("/my")
async def list_my_courses(
    skip: int = 0,
    limit: int = 20,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user: str = Depends(verify_client_bound_request)
):
    user_id = user.get("sub")
    cursor = db.courses.find(
        {"creator_id": user_id}
    ).sort("created_at", -1).skip(skip).limit(limit)

    courses = await cursor.to_list(length=limit)
    print(f"{serialize_many(courses)} , { len(courses)}")
    return {
        "courses": serialize_many(courses),
        "count": len(courses),
        "skip": skip,
        "limit": limit
    }


@router.get("/list")
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
        "courses": serialize_many(courses),
        "count": len(courses),
        "skip": skip,
        "limit": limit
    }

@router.post("/modules")
async def create_module(
    payload: ModuleCreate,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    await verify_course_owner(db, payload.course_id, user_id)

    module_id = f"MOD_{uuid.uuid4().hex[:10].upper()}"

    doc = {
        "module_id": module_id,
        "course_id": payload.course_id,
        "title": payload.title,
        "order": payload.order,
        "created_at": datetime.utcnow()
    }

    await db.modules.insert_one(doc)
    return {"module_id": module_id}

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
    return serialize_mongo(course)

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
@router.get("/modules/{course_id}")
async def list_modules(course_id: str, db: AsyncIOMotorDatabase = Depends(get_db)):
    cursor = db.modules.find({"course_id": course_id}).sort("order", 1)
    modules = await cursor.to_list(None)
    return [serialize(m) for m in modules]
@router.get("/samples/all/{course_id}")
async def list_all_course_samples(
    course_id: str, 
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    # Verify ownership so only the teacher sees the full sample bank
    await verify_course_owner(db, course_id, user_id)
    
    cursor = db.training_samples.find({"course_id": course_id})
    samples = await cursor.to_list(length=500)
    return serialize_many(samples)

@router.get("/questions/all/{course_id}")
async def list_all_course_questions(
    course_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    # Verify ownership to show all questions (including solved/unsolved and hidden outputs)
    await verify_course_owner(db, course_id, user_id)
    
    cursor = db.course_questions.find({"course_id": course_id})
    questions = await cursor.to_list(length=500)
    return serialize_many(questions)
@router.put("/modules/{module_id}")
async def update_module(
    module_id: str,
    payload: ModuleUpdate,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    module = await verify_module_owner(db, module_id, user_id)

    update_data = {k: v for k, v in payload.dict().items() if v is not None}

    await db.modules.update_one(
        {"module_id": module_id},
        {"$set": update_data}
    )

    return {"success": True}
@router.delete("/modules/{module_id}")
async def delete_module(
    module_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    await verify_module_owner(db, module_id, user_id)
    
    await db.lessons.delete_many({"module_id": module_id})
    await db.modules.delete_one({"module_id": module_id})

    return {"success": True}

@router.post("/lessons/{lesson_id}/progress")
async def update_lesson_progress(
    lesson_id: str,
    payload: LessonProgressUpdate,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    await db.lesson_progress.update_one(
        {
            "user_id": user_id,
            "lesson_id": lesson_id
        },
        {
            "$set": {
                "watched_seconds": payload.watched_seconds,
                "completed": payload.completed,
                "updated_at": datetime.utcnow()
            }
        },
        upsert=True
    )

    return {"success": True}
@router.get("/lessons/{lesson_id}/progress")
async def get_lesson_progress(
    lesson_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    progress = await db.lesson_progress.find_one({
        "user_id": user_id,
        "lesson_id": lesson_id
    })

    if not progress:
        return {"watched_seconds": 0, "completed": False}

    return {
        "watched_seconds": progress.get("watched_seconds", 0),
        "completed": progress.get("completed", False)
    }
@router.get("/courses/{course_id}/lesson-progress")
async def get_course_progress_map(
    course_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    # 1️⃣ get modules of course
    modules = await db.modules.find({"course_id": course_id}).to_list(None)

    module_ids = [m["module_id"] for m in modules]

    if not module_ids:
        return {}

    # 2️⃣ get lessons inside those modules
    lessons = await db.lessons.find({
        "module_id": {"$in": module_ids}
    }).to_list(None)

    lesson_ids = [l["lesson_id"] for l in lessons]

    # 3️⃣ fetch progress
    progress_docs = await db.lesson_progress.find({
        "user_id": user_id,
        "lesson_id": {"$in": lesson_ids}
    }).to_list(None)

    # 4️⃣ map result
    result = {lid: False for lid in lesson_ids}

    for p in progress_docs:
        result[p["lesson_id"]] = p.get("completed", False)

    return result


@router.put("/modules/reorder")
async def reorder_modules(
    payload: ReorderPayload,
    db: AsyncIOMotorDatabase = Depends(get_db)
):
    for idx, module_id in enumerate(payload.order):
        await db.modules.update_one(
            {"module_id": module_id},
            {"$set": {"order": idx + 1}}
        )

    return {"success": True}
@router.post("/lessons")
async def create_lesson(
    payload: LessonCreate,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    module = await verify_module_owner(db, payload.module_id, user_id)

    lesson_id = f"LESS_{uuid.uuid4().hex[:10].upper()}"

    doc = payload.dict()
    doc["video_url"] = normalize_youtube_url(doc["video_url"])
    doc.update({
        "lesson_id": lesson_id,
        "created_at": datetime.utcnow()
    })

    await db.lessons.insert_one(doc)

    return {"lesson_id": lesson_id}
@router.get("/lessons/{module_id}")
async def list_lessons(module_id: str, db: AsyncIOMotorDatabase = Depends(get_db)):
    cursor = db.lessons.find({"module_id": module_id}).sort("order", 1)
    lessons = await cursor.to_list(None)
    return [serialize(l) for l in lessons]
@router.put("/lessons/{lesson_id}")
async def update_lesson(
    lesson_id: str,
    payload: LessonUpdate,
    db: AsyncIOMotorDatabase = Depends(get_db)
):
    update_data = {k: v for k, v in payload.dict().items() if v is not None}

    await db.lessons.update_one(
        {"lesson_id": lesson_id},
        {"$set": update_data}
    )

    return {"success": True}
@router.delete("/lessons/{lesson_id}")
async def delete_lesson(lesson_id: str, db: AsyncIOMotorDatabase = Depends(get_db)):
    await db.lessons.delete_one({"lesson_id": lesson_id})
    return {"success": True}
@router.put("/lessons/reorder")
async def reorder_lessons(
    payload: ReorderPayload,
    db: AsyncIOMotorDatabase = Depends(get_db)
):
    for idx, lesson_id in enumerate(payload.order):
        await db.lessons.update_one(
            {"lesson_id": lesson_id},
            {"$set": {"order": idx + 1}}
        )

    return {"success": True}
@router.post("/lessons/from-timestamps")
async def create_from_timestamps(
    payload: TimestampBulkCreate,
    db: AsyncIOMotorDatabase = Depends(get_db)
):
    docs = []

    for idx, item in enumerate(payload.lessons):
        docs.append({
            "lesson_id": f"LESS_{uuid.uuid4().hex[:10].upper()}",
            "module_id": payload.module_id,
            "title": item.title,
            "video_url": normalize_youtube_url(payload.video_url),
            "start_time": item.start,
            "end_time": item.end,
            "order": idx + 1,
            "created_at": datetime.utcnow()
        })

    if docs:
        await db.lessons.insert_many(docs)

    return {"created": len(docs)}

@router.post("/{course_id}/publish")
async def publish_course_endpoint(
    course_id: str,
    confirm: CoursePublish,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    """
    Publish course (locks editing)
    🔒 SECURITY: Validates pricing before publishing
    """
    course = await get_course(db, course_id)
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    
    if course["creator_id"] != user_id:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    # ✅ Call secured publish function (returns validation result)
    result = await publish_course(db, course_id)
    
    if not result["success"]:
        raise HTTPException(
            status_code=400,
            detail={
                "message": result["message"],
                "errors": result["errors"]
            }
        )
    
    return {
        "success": True,
        "message": result["message"],
        "course_id": course_id
    }


# ==================== QUESTION MANAGEMENT ====================
from app.courses.models import SampleCreate,SampleBulkCreate,SampleUpdate
from app.courses.database import create_sample,bulk_create_samples
@router.put("/samples/{sample_id}")
async def update_sample(
    sample_id: str,
    updates: SampleUpdate,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    sample = await db.training_samples.find_one({"sample_id": sample_id})
    if not sample:
        raise HTTPException(404, "Sample not found")

    course = await db.courses.find_one({"course_id": sample["course_id"]})
    if course["creator_id"] != user_id:
        raise HTTPException(403, "Not authorized")

    update_data = {k: v for k, v in updates.dict().items() if v is not None}

    await db.training_samples.update_one(
        {"sample_id": sample_id},
        {"$set": update_data}
    )

    return {"success": True}
@router.delete("/samples/{sample_id}")
async def delete_sample(
    sample_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    sample = await db.training_samples.find_one({"sample_id": sample_id})
    if not sample:
        raise HTTPException(404, "Sample not found")

    course = await db.courses.find_one({"course_id": sample["course_id"]})
    if course["creator_id"] != user_id:
        raise HTTPException(403, "Not authorized")

    await db.training_samples.delete_one({"sample_id": sample_id})

    return {"success": True}

@router.post("/samples/bulk-upload")
async def bulk_upload_samples(
    payload: SampleBulkCreate,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    # verify ownership
    course = await db.courses.find_one({"course_id": payload.course_id})
    if not course:
        raise HTTPException(404, "Course not found")

    if course["creator_id"] != user_id:
        raise HTTPException(403, "Not authorized")

    count = await bulk_create_samples(db, payload.course_id, payload.samples)

    return {
        "success": True,
        "inserted": count
    }

@router.post("/samples/create")
async def create_sample_endpoint(
    sample: SampleCreate,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    # verify course exists
    course = await db.courses.find_one({"course_id": sample.course_id})
    if not course:
        raise HTTPException(404, "Course not found")

    # only creator can add samples
    if course["creator_id"] != user_id:
        raise HTTPException(403, "Not authorized")

    sample_id = await create_sample(db, sample.dict())

    return {
        "success": True,
        "sample_id": sample_id,
        "message": "Sample created successfully"
    }
class ExternalResource(BaseModel):
    title: str
    url: str
    type: Optional[str] = "link"  # link, video, document, etc.
    description: Optional[str] = None

class UpdateExternalResources(BaseModel):
    external_resources: List[ExternalResource]

class AddSingleResource(BaseModel):
    title: str
    url: str
    type: Optional[str] = "link"
    description: Optional[str] = None

# ==================== HELPER FUNCTIONS ====================

async def verify_course_ownership(db: AsyncIOMotorDatabase, course_id: str, user_id: str) -> bool:
    """Check if user is creator or instructor of the course"""
    course = await db.courses.find_one({"course_id": course_id})
    if not course:
        return False
    
    return course.get("creator_id") == user_id or course.get("instructor_id") == user_id

# ==================== EXTERNAL RESOURCES MANAGEMENT ====================

@router.put("/course/{course_id}/external-resources")
async def update_external_resources(
    course_id: str,
    resources: UpdateExternalResources,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    """
    Replace all external resources for a course
    Only creator/instructor can update
    """
    
    # Verify ownership
    if not await verify_course_ownership(db, course_id, user_id):
        raise HTTPException(status_code=403, detail="Not authorized to modify this course")
    
    # Convert Pydantic models to dicts
    resources_list = [r.dict() for r in resources.external_resources]
    
    # Update course
    result = await db.courses.update_one(
        {"course_id": course_id},
        {
            "$set": {
                "external_resources": resources_list,
                "updated_at": datetime.utcnow()
            }
        }
    )
    
    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="Course not found or no changes made")
    
    return {
        "success": True,
        "course_id": course_id,
        "resources_count": len(resources_list),
        "message": "External resources updated successfully"
    }


@router.post("/course/{course_id}/external-resources/add")
async def add_external_resource(
    course_id: str,
    resource: AddSingleResource,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    """
    Add a single external resource to existing list
    """
    
    # Verify ownership
    if not await verify_course_ownership(db, course_id, user_id):
        raise HTTPException(status_code=403, detail="Not authorized to modify this course")
    
    # Add resource to array
    result = await db.courses.update_one(
        {"course_id": course_id},
        {
            "$push": {
                "external_resources": resource.dict()
            },
            "$set": {
                "updated_at": datetime.utcnow()
            }
        }
    )
    
    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="Course not found")
    
    return {
        "success": True,
        "course_id": course_id,
        "resource_added": resource.dict(),
        "message": "Resource added successfully"
    }


@router.delete("/course/{course_id}/external-resources")
async def delete_external_resource(
    course_id: str,
    resource_url: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    """
    Remove a specific external resource by URL
    """
    
    # Verify ownership
    if not await verify_course_ownership(db, course_id, user_id):
        raise HTTPException(status_code=403, detail="Not authorized to modify this course")
    
    # Remove resource from array
    result = await db.courses.update_one(
        {"course_id": course_id},
        {
            "$pull": {
                "external_resources": {"url": resource_url}
            },
            "$set": {
                "updated_at": datetime.utcnow()
            }
        }
    )
    
    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="Course or resource not found")
    
    return {
        "success": True,
        "course_id": course_id,
        "message": "Resource removed successfully"
    }


@router.get("/course/{course_id}/external-resources")
async def get_external_resources(
    course_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db)
):
    """
    Get all external resources for a course
    Public endpoint - anyone can view
    """
    
    course = await db.courses.find_one({"course_id": course_id})
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    
    return {
        "course_id": course_id,
        "external_resources": course.get("external_resources", []),
        "count": len(course.get("external_resources", []))
    }


# ==================== INSTRUCTOR DASHBOARD ====================

@router.get("/my-courses/instructor")
async def get_instructor_courses(
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    """
    Get all courses where user is creator or instructor
    Shows stats for teacher dashboard
    """
    
    courses = await db.courses.find({
        "$or": [
            {"creator_id": user_id},
            {"instructor_id": user_id}
        ]
    }).to_list(length=None)
    
    course_stats = []
    
    for course in courses:
        # Get enrollment count
        enrollment_count = await db.course_enrollments.count_documents({
            "course_id": course["course_id"],
            "is_active": True
        })
        
        # Get question count
        question_count = await db.course_questions.count_documents({
            "course_id": course["course_id"],
            "is_active": True
        })
        
        # Get total submissions
        submission_count = await db.course_submissions.count_documents({
            "course_id": course["course_id"]
        })
        
        course_stats.append({
            "course_id": course["course_id"],
            "title": course["title"],
            "status": course["status"],
            "domain": course["domain"],
            "course_type": course["course_type"],
            "created_at": course["created_at"],
            "stats": {
                "enrollments": enrollment_count,
                "questions": question_count,
                "submissions": submission_count,
                "external_resources": len(course.get("external_resources", []))
            }
        })
    
    return {
        "courses": course_stats,
        "total_courses": len(course_stats)
    }


@router.get("/course/{course_id}/instructor-analytics")
async def get_course_analytics(
    course_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    """
    Get detailed analytics for a course (instructor only)
    """
    
    # Verify ownership
    if not await verify_course_ownership(db, course_id, user_id):
        raise HTTPException(status_code=403, detail="Not authorized to view analytics")
    
    # Get course
    course = await db.courses.find_one({"course_id": course_id})
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    
    # Get enrollments with league distribution
    league_distribution = await db.course_enrollments.aggregate([
        {"$match": {"course_id": course_id, "is_active": True}},
        {"$group": {
            "_id": "$current_league",
            "count": {"$sum": 1}
        }}
    ]).to_list(length=None)
    
    # Get most attempted questions
    most_attempted = await db.course_submissions.aggregate([
        {"$match": {"course_id": course_id}},
        {"$group": {
            "_id": "$question_id",
            "attempt_count": {"$sum": 1},
            "accepted_count": {
                "$sum": {"$cond": [{"$eq": ["$verdict", "Accepted"]}, 1, 0]}
            }
        }},
        {"$sort": {"attempt_count": -1}},
        {"$limit": 10}
    ]).to_list(length=10)
    
    # Enrich with question titles
    for item in most_attempted:
        question = await db.course_questions.find_one({"question_id": item["_id"]})
        item["question_title"] = question.get("title") if question else "Unknown"
        item["difficulty"] = question.get("difficulty") if question else "unknown"
    
    # Get recent activity (last 7 days)
    from datetime import timedelta
    seven_days_ago = datetime.utcnow() - timedelta(days=7)
    
    recent_enrollments = await db.course_enrollments.count_documents({
        "course_id": course_id,
        "enrolled_at": {"$gte": seven_days_ago}
    })
    
    recent_submissions = await db.course_submissions.count_documents({
        "course_id": course_id,
        "submitted_at": {"$gte": seven_days_ago}
    })
    
    return {
        "course_id": course_id,
        "course_title": course["title"],
        "league_distribution": {item["_id"]: item["count"] for item in league_distribution},
        "most_attempted_questions": most_attempted,
        "recent_activity": {
            "new_enrollments_7d": recent_enrollments,
            "submissions_7d": recent_submissions
        },
        "total_stats": {
            "enrollments": await db.course_enrollments.count_documents({
                "course_id": course_id,
                "is_active": True
            }),
            "questions": await db.course_questions.count_documents({
                "course_id": course_id,
                "is_active": True
            }),
            "total_submissions": await db.course_submissions.count_documents({
                "course_id": course_id
            })
        }
    }
# ==================== STUDENT COURSE DASHBOARD ====================

@router.get("/course/{course_id}/dashboard")
async def get_student_course_dashboard(
    course_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    """
    Get complete student data for a specific course
    Returns everything a student needs to see about their progress
    """
    
    # 1️⃣ Get enrollment
    enrollment = await db.course_enrollments.find_one({
        "course_id": course_id,
        "user_id": user_id
    })
    
    if not enrollment:
        raise HTTPException(status_code=404, detail="Not enrolled in this course")
    
    # 2️⃣ Get course details
    course = await db.courses.find_one({"course_id": course_id})
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    
    # 3️⃣ Get total and solved questions
    total_questions = await db.course_questions.count_documents({
        "course_id": course_id,
        "is_active": True
    })
    
    solved_questions = enrollment.get("solved_questions", [])
    solved_count = len(solved_questions)
    
    # 4️⃣ Get submission history (last 10)
    submissions_cursor = db.course_submissions.find({
        "course_id": course_id,
        "user_id": user_id
    }).sort("submitted_at", -1).limit(10)
    
    recent_submissions = await submissions_cursor.to_list(length=10)
    
    # 5️⃣ Get accepted submissions count
    accepted_count = await db.course_submissions.count_documents({
        "course_id": course_id,
        "user_id": user_id,
        "verdict": "Accepted"
    })
    
    # 6️⃣ Get difficulty breakdown
    solved_by_difficulty = await db.course_questions.aggregate([
        {
            "$match": {
                "course_id": course_id,
                "question_id": {"$in": solved_questions},
                "is_active": True
            }
        },
        {
            "$group": {
                "_id": "$difficulty",
                "count": {"$sum": 1}
            }
        }
    ]).to_list(length=None)
    
    difficulty_stats = {
        "easy": 0,
        "medium": 0,
        "hard": 0
    }
    for item in solved_by_difficulty:
        difficulty_stats[item["_id"]] = item["count"]
    
    # 7️⃣ Calculate progress percentage
    progress = (solved_count / total_questions * 100) if total_questions > 0 else 0
    
    # 8️⃣ Get rank in course leaderboard
    rank = await db.course_enrollments.count_documents({
        "course_id": course_id,
        "is_active": True,
        "league_points": {"$gt": enrollment.get("league_points", 0)}
    }) + 1
    
    # 9️⃣ Get practice sample stats
    sample_progress = await db.user_sample_progress.find_one({
        "user_id": user_id,
        "course_id": course_id
    })
    
    read_samples_count = len(sample_progress.get("read_samples", [])) if sample_progress else 0
    total_samples = await db.training_samples.count_documents({"course_id": course_id})
    
    # 🔟 Check certificate eligibility
    current_league = enrollment.get("current_league", "BRONZE")
    certificate_eligible = current_league in ["SILVER", "GOLD", "PLATINUM", "DIAMOND", "MYTHIC", "LEGEND"]
    
    # 📊 Build response
    return {
        "course": {
            "course_id": course["course_id"],
            "title": course["title"],
            "description": course["description"],
            "domain": course["domain"],
            "course_type": course["course_type"],
            "thumbnail_url": course.get("thumbnail_url"),
            "tags": course.get("tags", []),
            "external_resources": course.get("external_resources", [])
        },
        "enrollment": {
            "enrollment_id": enrollment["enrollment_id"],
            "enrolled_at": enrollment["enrolled_at"],
            "certificate_id": enrollment.get("certificate_id"),
            "is_active": enrollment.get("is_active", True)
        },
        "progress": {
            "total_questions": total_questions,
            "solved_count": solved_count,
            "progress_percentage": round(progress, 2),
            "by_difficulty": difficulty_stats
        },
        "league": {
            "current_league": current_league,
            "league_points": enrollment.get("league_points", 0),
            "rank": rank,
            "avg_efficiency": enrollment.get("avg_efficiency", 0.0)
        },
        "submissions": {
            "total_submissions": await db.course_submissions.count_documents({
                "course_id": course_id,
                "user_id": user_id
            }),
            "accepted_submissions": accepted_count,
            "recent": [
                {
                    "submission_id": s["submission_id"],
                    "question_id": s["question_id"],
                    "verdict": s.get("verdict"),
                    "score": s.get("score"),
                    "submitted_at": s["submitted_at"]
                }
                for s in recent_submissions
            ]
        },
        "practice": {
            "total_samples": total_samples,
            "read_samples": read_samples_count,
            "progress_percentage": round((read_samples_count / total_samples * 100) if total_samples > 0 else 0, 2)
        },
        "certificate": {
            "eligible": certificate_eligible,
            "certificate_id": enrollment.get("certificate_id") if certificate_eligible else None,
            "message": "Certificate available for download!" if certificate_eligible else "Reach Silver league to unlock certificate"
        }
    }


@router.get("/my-all-courses-summary")
async def get_all_courses_summary(
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    """
    Get summary of all enrolled courses
    Quick overview for student homepage
    """
    
    enrollments = await db.course_enrollments.find({
        "user_id": user_id,
        "is_active": True
    }).to_list(length=None)
    
    courses_summary = []
    
    for enr in enrollments:
        course = await db.courses.find_one({"course_id": enr["course_id"]})
        if not course:
            continue
        
        total_questions = await db.course_questions.count_documents({
            "course_id": enr["course_id"],
            "is_active": True
        })
        
        solved_count = len(enr.get("solved_questions", []))
        progress = (solved_count / total_questions * 100) if total_questions > 0 else 0
        
        courses_summary.append({
            "course_id": course["course_id"],
            "title": course["title"],
            "domain": course["domain"],
            "thumbnail_url": course.get("thumbnail_url"),
            "enrollment_id": enr["enrollment_id"],
            "progress": round(progress, 2),
            "league": enr.get("current_league", "BRONZE"),
            "league_points": enr.get("league_points", 0),
            "solved_count": solved_count,
            "total_questions": total_questions,
            "enrolled_at": enr["enrolled_at"]
        })
    
    return {
        "courses": courses_summary,
        "total_enrolled": len(courses_summary),
        "total_league_points": sum(c["league_points"] for c in courses_summary)
    }

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
@router.get("/system/health-report")
async def get_health_report(db: AsyncIOMotorDatabase = Depends(get_db)):
    last_24h = datetime.utcnow() - timedelta(hours=24)
    
    # Fetch all records from the last 24 hours
    cursor = db.system_health_records.find({"timestamp": {"$gte": last_24h}}).sort("timestamp", 1)
    records = await cursor.to_list(length=None)
    
    if not records:
        return {"message": "No data collected yet"}

    # Calculate Uptime Percentages
    total_pings = len(records)
    uptime_summary = {}
    
    # Check key servers for uptime stats
    for key in ["auth_server", "softjudge_server", "hardjudge_server"]:
        up_count = sum(1 for r in records if r["status"].get(key) == "UP")
        uptime_summary[key] = f"{(up_count / total_pings) * 100:.2f}%"

    return {
        "summary": uptime_summary,
        "total_records": total_pings,
        "history": serialize_many(records) # Raw data for the frontend heatmap/linechart
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
    
    return serialize_mongo(question)
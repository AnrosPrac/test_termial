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
from datetime import datetime
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


class TimestampBulkCreate(BaseModel):
    module_id: str
    video_url: str
    lessons: List[TimestampItem]

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
    
    return serialize_mongo(question)

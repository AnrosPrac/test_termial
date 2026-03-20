"""
SUPERADMIN ROUTER
File: app/admin/superadmin_router.py

Complete admin control over every part of the system:

  PLATFORM OVERVIEW
    GET  /superadmin/overview              — master dashboard, all systems in one shot

  ENQUIRIES  (college partnerships, faculty, brand, student support, general)
    GET  /superadmin/enquiries             — list all, filter by type/status
    GET  /superadmin/enquiries/{id}        — full detail
    POST /superadmin/enquiries/{id}/respond
    POST /superadmin/enquiries/{id}/close

  ACCESS REQUESTS  (manual college-claim requests)
    GET  /superadmin/access-requests       — pending first
    GET  /superadmin/access-requests/{id}
    POST /superadmin/access-requests/{id}/approve
    POST /superadmin/access-requests/{id}/reject
    POST /superadmin/access-requests/{id}/revoke

  AUTO CLAIMS  (college-name-match instant grants)
    GET  /superadmin/claims                — with suspicious flag
    POST /superadmin/claims/{id}/revoke

  COURSES
    GET  /superadmin/courses               — all courses (any type/status)
    GET  /superadmin/courses/{id}          — detail + stats
    POST /superadmin/courses/{id}/publish
    POST /superadmin/courses/{id}/archive
    DELETE /superadmin/courses/{id}
    POST /superadmin/courses/{id}/claim-config
    DELETE /superadmin/courses/{id}/claim-config

  LAB COURSES
    GET  /superadmin/labs                  — all labs with classroom context

  CLASSROOMS
    GET  /superadmin/classrooms            — all classrooms across all teachers
    GET  /superadmin/classrooms/{id}       — detail + members + assignments
    DELETE /superadmin/classrooms/{id}     — force-delete (use with care)
    POST /superadmin/classrooms/{id}/lock  — lock/unlock joining

  TEACHERS
    GET  /superadmin/teachers              — all teacher profiles
    GET  /superadmin/teachers/{user_id}    — detail with their classrooms
    POST /superadmin/teachers/{user_id}/ban
    POST /superadmin/teachers/{user_id}/unban

  STUDENTS
    GET  /superadmin/students              — all students with filters
    GET  /superadmin/students/{user_id}    — profile + enrollments + submissions
    POST /superadmin/students/{user_id}/ban
    POST /superadmin/students/{user_id}/unban

  ENROLLMENTS
    GET  /superadmin/enrollments           — all course enrollments, filter by course
    DELETE /superadmin/enrollments/{id}    — force-unenroll

  SUBMISSIONS  (course/lab submissions)
    GET  /superadmin/submissions           — all, filter by course/user
    GET  /superadmin/submissions/{id}

  PLAGIARISM
    GET  /superadmin/plagiarism            — all flagged pairs, filter by flag level
    GET  /superadmin/plagiarism/{pair_id}

  LEADERBOARD
    GET  /superadmin/leaderboard/{course_id}  — full leaderboard any course

  SUPPORT TICKETS  (existing help system)
    GET  /superadmin/tickets
    GET  /superadmin/tickets/{id}
    POST /superadmin/tickets/{id}/reply
    POST /superadmin/tickets/{id}/close

  USERS (platform accounts)
    GET  /superadmin/users                 — all with search/filter
    GET  /superadmin/users/{sidhi_id}      — full profile + quota + payments
    PUT  /superadmin/users/{sidhi_id}
    POST /superadmin/users/{sidhi_id}/ban
    POST /superadmin/users/{sidhi_id}/unban
    DELETE /superadmin/users/{sidhi_id}

  PAYMENTS
    GET  /superadmin/payments
    GET  /superadmin/payments/{order_id}
    PUT  /superadmin/payments/{order_id}/refund

  NOTIFICATIONS
    POST /superadmin/notifications/send
    GET  /superadmin/notifications/history
    DELETE /superadmin/notifications/{id}

  EXPORTS
    GET  /superadmin/export/enquiries
    GET  /superadmin/export/courses
    GET  /superadmin/export/students
    GET  /superadmin/export/submissions

Mount in main.py:
    from app.admin.superadmin_router import router as superadmin_router
    app.include_router(superadmin_router, prefix="/superadmin")
"""

import csv
import io
import uuid
from datetime import datetime, timedelta
from typing import List, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, validator
import os

from app.admin.hardened_firebase_auth import get_current_admin

router = APIRouter(tags=["Superadmin"])

MONGO_URL = os.getenv("MONGO_URL")
_client = AsyncIOMotorClient(MONGO_URL)
db = _client.lumetrics_db


# ============================================================================
# HELPERS
# ============================================================================

def _iso(dt):
    return dt.isoformat() if isinstance(dt, datetime) else dt

def _clean(doc: dict) -> dict:
    """Strip _id and convert datetimes to ISO strings."""
    doc = dict(doc)
    doc.pop("_id", None)
    for k, v in doc.items():
        if isinstance(v, datetime):
            doc[k] = v.isoformat()
        elif isinstance(v, ObjectId):
            doc[k] = str(v)
    return doc

def _clean_many(docs: list) -> list:
    return [_clean(dict(d)) for d in docs]

def _oid(raw: str) -> ObjectId:
    try:
        return ObjectId(raw)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid ID format")


# ============================================================================
# PYDANTIC MODELS
# ============================================================================

class EnquiryResponse(BaseModel):
    response: str
    status: Optional[str] = "responded"

    @validator("status")
    def valid_status(cls, v):
        if v not in {"pending", "in_progress", "responded", "closed"}:
            raise ValueError("Invalid status")
        return v


class ReviewNote(BaseModel):
    note: Optional[str] = None


class BanRequest(BaseModel):
    reason: str


class TicketReply(BaseModel):
    admin_response: str


class CourseArchive(BaseModel):
    reason: Optional[str] = None


class ClaimConfig(BaseModel):
    allowed_colleges: List[str]
    claim_label: Optional[str] = "College Students"

    @validator("allowed_colleges", each_item=True)
    def not_empty(cls, v):
        v = v.strip()
        if not v:
            raise ValueError("College name cannot be empty")
        return v


class NotificationRequest(BaseModel):
    title: str
    message: str
    type: str = "info"
    priority: str = "medium"
    target_type: str = "all"
    target_users: Optional[List[str]] = None
    expires_hours: Optional[int] = None


class RefundRequest(BaseModel):
    refund_reason: str


class UserUpdate(BaseModel):
    username: Optional[str] = None
    email_id: Optional[str] = None
    college: Optional[str] = None
    department: Optional[str] = None
    degree: Optional[str] = None


class ClassroomLock(BaseModel):
    locked: bool


# ============================================================================
# PLATFORM OVERVIEW  — one shot, everything that needs attention
# ============================================================================

@router.get("/overview")
async def superadmin_overview(admin: dict = Depends(get_current_admin)):
    """
    Master dashboard. One call — all pending counts across every system.
    Check each 'needs_action' number — anything > 0 requires attention.
    """
    now = datetime.utcnow()
    week_ago = now - timedelta(days=7)

    # Enquiries
    enq_pending   = await db.enquiries.count_documents({"status": "pending"})
    enq_total     = await db.enquiries.count_documents({})

    # Access requests (manual college claims)
    req_pending   = await db.course_access_requests.count_documents({"status": "pending"})
    req_total     = await db.course_access_requests.count_documents({})

    # Support tickets
    tix_pending   = await db.help_tickets.count_documents({"status": "pending"})
    tix_total     = await db.help_tickets.count_documents({})

    # Courses
    courses_draft     = await db.courses.count_documents({"status": "DRAFT"})
    courses_published = await db.courses.count_documents({"status": "PUBLISHED"})
    courses_total     = await db.courses.count_documents({})
    labs_total        = await db.courses.count_documents({"course_type": "LAB"})

    # Classrooms & teachers
    classrooms_total  = await db.classrooms.count_documents({})
    teachers_total    = await db.users_profile.count_documents({"role": "teacher"})

    # Students & enrollments
    students_total    = await db.users_profile.count_documents({"role": "student"})
    enrollments_total = await db.course_enrollments.count_documents({})

    # Plagiarism
    plag_red    = await db.plagiarism_results.count_documents({"flag": "red", "reviewed_by_teacher": False})
    plag_yellow = await db.plagiarism_results.count_documents({"flag": "yellow", "reviewed_by_teacher": False})

    # Submissions (last 7 days)
    recent_submissions = await db.course_submissions.count_documents(
        {"submitted_at": {"$gte": week_ago}}
    )

    # Users (platform accounts)
    users_total    = await db.users_profile.count_documents({})
    users_banned   = await db.users_profile.count_documents({"is_banned": True})
    new_users_week = await db.users_profile.count_documents(
        {"created_at": {"$gte": week_ago}}
    )

    # Payments
    payments_total   = await db.payments.count_documents({})
    payments_pending = await db.payments.count_documents({"status": "created"})
    payments_captured_week = await db.payments.count_documents({
        "status": "captured",
        "created_at": {"$gte": week_ago}
    })

    # 5 most recent pending enquiries preview
    recent_enq = await db.enquiries.find({"status": "pending"}).sort("submitted_at", -1).to_list(5)
    recent_req = await db.course_access_requests.find({"status": "pending"}).sort("submitted_at", -1).to_list(5)

    return {
        "generated_at": now.isoformat(),
        "admin": admin.get("email"),
        "needs_action": {
            "enquiries_pending":       enq_pending,
            "access_requests_pending": req_pending,
            "support_tickets_pending": tix_pending,
            "plagiarism_red_unreviewed":    plag_red,
            "plagiarism_yellow_unreviewed": plag_yellow,
            "payments_pending":        payments_pending,
        },
        "enquiries":    {"pending": enq_pending, "total": enq_total},
        "access_requests": {"pending": req_pending, "total": req_total},
        "tickets":      {"pending": tix_pending, "total": tix_total},
        "courses":      {"draft": courses_draft, "published": courses_published, "total": courses_total, "labs": labs_total},
        "classrooms":   {"total": classrooms_total},
        "teachers":     {"total": teachers_total},
        "students":     {"total": students_total, "enrollments": enrollments_total},
        "plagiarism":   {"red_unreviewed": plag_red, "yellow_unreviewed": plag_yellow},
        "submissions":  {"last_7_days": recent_submissions},
        "users":        {"total": users_total, "banned": users_banned, "new_last_7_days": new_users_week},
        "payments":     {"total": payments_total, "pending": payments_pending, "captured_last_7_days": payments_captured_week},
        "previews": {
            "pending_enquiries":    _clean_many(recent_enq),
            "pending_access_requests": _clean_many(recent_req),
        }
    }


# ============================================================================
# ENQUIRIES  (college partnership, faculty, brand, student, general)
# ============================================================================

@router.get("/enquiries")
async def list_enquiries(
    type: Optional[str] = None,
    status: Optional[str] = "pending",
    skip: int = 0,
    limit: int = 50,
    admin: dict = Depends(get_current_admin)
):
    """List all contact enquiries. Default: pending ones needing action."""
    query = {}
    if type and type != "all":
        query["type"] = type
    if status and status != "all":
        query["status"] = status

    docs  = await db.enquiries.find(query).sort("submitted_at", -1).skip(skip).limit(limit).to_list(limit)
    total = await db.enquiries.count_documents(query)

    # Per-type pending counts for sidebar badges
    types = ["college_partnership", "faculty", "brand_partnership", "student_support", "general"]
    pending_by_type = {}
    for t in types:
        pending_by_type[t] = await db.enquiries.count_documents({"type": t, "status": "pending"})

    return {
        "enquiries": _clean_many(docs),
        "count": len(docs),
        "total": total,
        "pending_by_type": pending_by_type,
        "filter": {"type": type, "status": status}
    }


@router.get("/enquiries/{enquiry_id}")
async def get_enquiry(enquiry_id: str, admin: dict = Depends(get_current_admin)):
    """Full detail of one enquiry."""
    doc = await db.enquiries.find_one({"enquiry_id": enquiry_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Enquiry not found")
    return _clean(doc)


@router.post("/enquiries/{enquiry_id}/respond")
async def respond_to_enquiry(
    enquiry_id: str,
    body: EnquiryResponse,
    admin: dict = Depends(get_current_admin)
):
    """Respond to an enquiry and set its status."""
    doc = await db.enquiries.find_one({"enquiry_id": enquiry_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Enquiry not found")
    if doc["status"] == "closed":
        raise HTTPException(status_code=400, detail="Enquiry is already closed")

    await db.enquiries.update_one(
        {"enquiry_id": enquiry_id},
        {"$set": {
            "status": body.status,
            "admin_response": body.response,
            "responded_at": datetime.utcnow(),
            "responded_by": admin.get("email", "admin"),
        }}
    )
    return {"success": True, "enquiry_id": enquiry_id, "status": body.status}


@router.post("/enquiries/{enquiry_id}/close")
async def close_enquiry(enquiry_id: str, admin: dict = Depends(get_current_admin)):
    """Force-close an enquiry."""
    doc = await db.enquiries.find_one({"enquiry_id": enquiry_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Enquiry not found")
    await db.enquiries.update_one(
        {"enquiry_id": enquiry_id},
        {"$set": {"status": "closed", "closed_at": datetime.utcnow(), "closed_by": admin.get("email")}}
    )
    return {"success": True, "enquiry_id": enquiry_id}


# ============================================================================
# ACCESS REQUESTS  (manual course claim reviews)
# ============================================================================

@router.get("/access-requests")
async def list_access_requests(
    status: Optional[str] = "pending",
    course_id: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
    admin: dict = Depends(get_current_admin)
):
    """List student manual access requests with suspicious-flag indicators."""
    query = {}
    if status and status != "all":
        query["status"] = status
    if course_id:
        query["course_id"] = course_id

    docs  = await db.course_access_requests.find(query).sort("submitted_at", -1).skip(skip).limit(limit).to_list(limit)
    total = await db.course_access_requests.count_documents(query)

    return {
        "requests": _clean_many(docs),
        "count": len(docs),
        "total": total,
        "filter": {"status": status, "course_id": course_id}
    }


@router.get("/access-requests/{request_id}")
async def get_access_request(request_id: str, admin: dict = Depends(get_current_admin)):
    req = await db.course_access_requests.find_one({"request_id": request_id})
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")
    return _clean(req)


@router.post("/access-requests/{request_id}/approve")
async def approve_access_request(
    request_id: str,
    body: ReviewNote,
    admin: dict = Depends(get_current_admin)
):
    """Approve — gives student instant free access to the course."""
    req = await db.course_access_requests.find_one({"request_id": request_id})
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")
    if req["status"] != "pending":
        raise HTTPException(status_code=400, detail=f"Already '{req['status']}'")

    now = datetime.utcnow()
    await db.course_access_requests.update_one(
        {"request_id": request_id},
        {"$set": {"status": "approved", "reviewed_at": now, "reviewed_by": admin.get("email"), "admin_note": body.note}}
    )
    await db.course_claim_access.update_one(
        {"user_id": req["user_id"], "course_id": req["course_id"]},
        {"$set": {
            "user_id": req["user_id"], "sidhi_id": req.get("sidhi_id"),
            "course_id": req["course_id"], "request_id": request_id,
            "college_name": req["college_name"], "branch_name": req["branch_name"],
            "roll_number": req["roll_number"], "college_email": req["college_email"],
            "access_granted": True, "granted_at": now,
            "granted_via": "manual_admin_approval", "granted_by": admin.get("email")
        }},
        upsert=True
    )
    return {"success": True, "action": "approved", "request_id": request_id, "student": req["full_name"]}


@router.post("/access-requests/{request_id}/reject")
async def reject_access_request(
    request_id: str,
    body: ReviewNote,
    admin: dict = Depends(get_current_admin)
):
    """Reject — student's lifetime slot is freed so they can resubmit."""
    req = await db.course_access_requests.find_one({"request_id": request_id})
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")
    if req["status"] != "pending":
        raise HTTPException(status_code=400, detail=f"Already '{req['status']}'")

    await db.course_access_requests.update_one(
        {"request_id": request_id},
        {"$set": {
            "status": "rejected",
            "reviewed_at": datetime.utcnow(),
            "reviewed_by": admin.get("email"),
            "admin_note": body.note or "Request not approved."
        }}
    )
    return {"success": True, "action": "rejected", "student": req["full_name"]}


@router.post("/access-requests/{request_id}/revoke")
async def revoke_access_request(request_id: str, admin: dict = Depends(get_current_admin)):
    """Revoke an already-approved request — removes access immediately."""
    req = await db.course_access_requests.find_one({"request_id": request_id})
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")
    if req["status"] != "approved":
        raise HTTPException(status_code=400, detail="Not approved — nothing to revoke")

    await db.course_access_requests.update_one(
        {"request_id": request_id},
        {"$set": {"status": "revoked", "revoked_at": datetime.utcnow(), "revoked_by": admin.get("email")}}
    )
    await db.course_claim_access.delete_one({"user_id": req["user_id"], "course_id": req["course_id"]})
    return {"success": True, "action": "revoked", "request_id": request_id}


# ============================================================================
# AUTO CLAIMS  (college-name-match instant grants)
# ============================================================================

@router.get("/claims")
async def list_auto_claims(
    course_id: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
    admin: dict = Depends(get_current_admin)
):
    """List auto-approved claims. Flags suspicious ones (non-academic email domain)."""
    query = {"status": "approved", "approved_via": "college_name_match"}
    if course_id:
        query["course_id"] = course_id

    docs  = await db.course_claims.find(query).sort("approved_at", -1).skip(skip).limit(limit).to_list(limit)
    total = await db.course_claims.count_documents(query)

    result = []
    for c in docs:
        c = _clean(c)
        domain = c.get("email_domain", "")
        c["suspicious"] = not any(x in domain for x in ["ac.in", "edu", ".edu"])
        result.append(c)

    return {"claims": result, "count": len(result), "total": total}


@router.post("/claims/{claim_id}/revoke")
async def revoke_claim(claim_id: str, admin: dict = Depends(get_current_admin)):
    """Revoke an auto-claim — removes access and frees the lifetime slot."""
    claim = await db.course_claims.find_one({"claim_id": claim_id})
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found")
    if claim["status"] != "approved":
        raise HTTPException(status_code=400, detail="Not approved")

    await db.course_claims.update_one(
        {"claim_id": claim_id},
        {"$set": {"status": "revoked", "revoked_at": datetime.utcnow(), "revoked_by": admin.get("email")}}
    )
    await db.course_claim_access.delete_one({"user_id": claim["user_id"], "course_id": claim["course_id"]})
    return {"success": True, "claim_id": claim_id}


# ============================================================================
# COURSES
# ============================================================================

@router.get("/courses")
async def list_all_courses(
    status: Optional[str] = None,
    course_type: Optional[str] = None,
    domain: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
    admin: dict = Depends(get_current_admin)
):
    """List all courses regardless of status. Admins see everything."""
    query = {}
    if status and status != "all":
        query["status"] = status
    if course_type:
        query["course_type"] = course_type
    if domain:
        query["domain"] = domain

    docs  = await db.courses.find(query).sort("created_at", -1).skip(skip).limit(limit).to_list(limit)
    total = await db.courses.count_documents(query)

    result = []
    for c in docs:
        c = _clean(c)
        c["enrollment_count"] = await db.course_enrollments.count_documents({"course_id": c["course_id"], "is_active": True})
        c["question_count"]   = await db.course_questions.count_documents({"course_id": c["course_id"], "is_active": True})
        result.append(c)

    return {"courses": result, "count": len(result), "total": total}


@router.get("/courses/{course_id}")
async def get_course_detail(course_id: str, admin: dict = Depends(get_current_admin)):
    """Full course detail with enrollment, submission, and question stats."""
    course = await db.courses.find_one({"course_id": course_id})
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")

    c = _clean(course)
    c["enrollment_count"]  = await db.course_enrollments.count_documents({"course_id": course_id, "is_active": True})
    c["question_count"]    = await db.course_questions.count_documents({"course_id": course_id, "is_active": True})
    c["submission_count"]  = await db.course_submissions.count_documents({"course_id": course_id})
    c["claim_config"]      = c.get("claim_access", {})

    # If lab, attach classroom info
    if course.get("is_lab") or course.get("course_type") == "LAB":
        classroom = await db.classrooms.find_one({"classroom_id": course.get("classroom_id")})
        c["classroom"] = _clean(classroom) if classroom else None

    return c


@router.post("/courses/{course_id}/publish")
async def admin_publish_course(course_id: str, admin: dict = Depends(get_current_admin)):
    """Force-publish a course regardless of pricing validation."""
    course = await db.courses.find_one({"course_id": course_id})
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")

    await db.courses.update_one(
        {"course_id": course_id},
        {"$set": {"status": "PUBLISHED", "published_at": datetime.utcnow(), "updated_at": datetime.utcnow()}}
    )
    return {"success": True, "course_id": course_id, "status": "PUBLISHED"}


@router.post("/courses/{course_id}/archive")
async def admin_archive_course(
    course_id: str,
    body: CourseArchive,
    admin: dict = Depends(get_current_admin)
):
    """Archive a course — removes it from student view but preserves data."""
    course = await db.courses.find_one({"course_id": course_id})
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")

    await db.courses.update_one(
        {"course_id": course_id},
        {"$set": {
            "status": "ARCHIVED",
            "archived_at": datetime.utcnow(),
            "archived_by": admin.get("email"),
            "archive_reason": body.reason,
            "updated_at": datetime.utcnow()
        }}
    )
    return {"success": True, "course_id": course_id, "status": "ARCHIVED"}


@router.delete("/courses/{course_id}")
async def admin_delete_course(course_id: str, admin: dict = Depends(get_current_admin)):
    """
    DANGER: Hard delete a course and all associated data.
    Removes questions, enrollments, submissions, modules, lessons.
    """
    course = await db.courses.find_one({"course_id": course_id})
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")

    await db.courses.delete_one({"course_id": course_id})
    await db.course_questions.delete_many({"course_id": course_id})
    await db.course_enrollments.delete_many({"course_id": course_id})
    await db.course_submissions.delete_many({"course_id": course_id})
    await db.training_samples.delete_many({"course_id": course_id})
    await db.modules.delete_many({"course_id": course_id})
    await db.course_claim_access.delete_many({"course_id": course_id})
    await db.course_access_requests.delete_many({"course_id": course_id})

    return {"success": True, "course_id": course_id, "message": "Course and all associated data deleted"}


@router.post("/courses/{course_id}/claim-config")
async def set_course_claim_config(
    course_id: str,
    config: ClaimConfig,
    admin: dict = Depends(get_current_admin)
):
    """Set which colleges can auto-claim access to this course."""
    course = await db.courses.find_one({"course_id": course_id})
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")

    await db.courses.update_one(
        {"course_id": course_id},
        {"$set": {
            "claim_access": {
                "enabled": True,
                "allowed_colleges": config.allowed_colleges,
                "claim_label": config.claim_label,
                "configured_by": admin.get("email"),
                "configured_at": datetime.utcnow()
            },
            "updated_at": datetime.utcnow()
        }}
    )
    return {"success": True, "course_id": course_id, "allowed_colleges": config.allowed_colleges}


@router.delete("/courses/{course_id}/claim-config")
async def remove_course_claim_config(course_id: str, admin: dict = Depends(get_current_admin)):
    """Disable auto-claiming for a course. Existing approvals stay valid."""
    await db.courses.update_one(
        {"course_id": course_id},
        {"$set": {"claim_access.enabled": False, "updated_at": datetime.utcnow()}}
    )
    return {"success": True, "course_id": course_id, "message": "Auto-claiming disabled"}


# ============================================================================
# LAB COURSES
# ============================================================================

@router.get("/labs")
async def list_all_labs(
    classroom_id: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
    admin: dict = Depends(get_current_admin)
):
    """List all LAB courses with their classroom context."""
    query = {"course_type": "LAB"}
    if classroom_id:
        query["classroom_id"] = classroom_id

    docs  = await db.courses.find(query).sort("created_at", -1).skip(skip).limit(limit).to_list(limit)
    total = await db.courses.count_documents(query)

    result = []
    for c in docs:
        c = _clean(c)
        classroom = await db.classrooms.find_one({"classroom_id": c.get("classroom_id")})
        c["classroom"] = _clean(classroom) if classroom else None
        c["enrollment_count"] = await db.course_enrollments.count_documents({"course_id": c["course_id"], "is_active": True})
        c["question_count"]   = await db.course_questions.count_documents({"course_id": c["course_id"], "is_active": True})
        result.append(c)

    return {"labs": result, "count": len(result), "total": total}


# ============================================================================
# CLASSROOMS
# ============================================================================

@router.get("/classrooms")
async def list_all_classrooms(
    teacher_user_id: Optional[str] = None,
    university_id: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
    admin: dict = Depends(get_current_admin)
):
    """List all classrooms across all teachers."""
    query = {}
    if teacher_user_id:
        query["teacher_user_id"] = teacher_user_id
    if university_id:
        query["university_id"] = university_id

    docs  = await db.classrooms.find(query).sort("created_at", -1).skip(skip).limit(limit).to_list(limit)
    total = await db.classrooms.count_documents(query)

    result = []
    for c in docs:
        c = _clean(c)
        c["student_count"]    = await db.classroom_memberships.count_documents({"classroom_id": c["classroom_id"], "is_active": True})
        c["assignment_count"] = await db.assignments.count_documents({"classroom_id": c["classroom_id"]})
        c["lab_count"]        = await db.courses.count_documents({"classroom_id": c["classroom_id"], "course_type": "LAB"})
        result.append(c)

    return {"classrooms": result, "count": len(result), "total": total}


@router.get("/classrooms/{classroom_id}")
async def get_classroom_detail(classroom_id: str, admin: dict = Depends(get_current_admin)):
    """Full classroom detail — members, assignments, labs."""
    classroom = await db.classrooms.find_one({"classroom_id": classroom_id})
    if not classroom:
        raise HTTPException(status_code=404, detail="Classroom not found")

    c = _clean(classroom)

    # Teacher info
    teacher = await db.users_profile.find_one({"user_id": classroom["teacher_user_id"]})
    c["teacher"] = {"username": teacher.get("username"), "email": teacher.get("email_id")} if teacher else None

    # Members
    members_cursor = db.classroom_memberships.find({"classroom_id": classroom_id, "is_active": True})
    members = await members_cursor.to_list(length=None)
    c["student_count"] = len(members)

    # Assignments
    assignments = await db.assignments.find({"classroom_id": classroom_id}).to_list(length=None)
    c["assignments"] = _clean_many(assignments)

    # Labs
    labs = await db.courses.find({"classroom_id": classroom_id, "course_type": "LAB"}).to_list(length=None)
    c["labs"] = _clean_many(labs)

    return c


@router.post("/classrooms/{classroom_id}/lock")
async def admin_lock_classroom(
    classroom_id: str,
    body: ClassroomLock,
    admin: dict = Depends(get_current_admin)
):
    """Lock or unlock student joining for a classroom."""
    result = await db.classrooms.update_one(
        {"classroom_id": classroom_id},
        {"$set": {"joining_locked": body.locked, "updated_at": datetime.utcnow()}}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Classroom not found")
    return {"success": True, "classroom_id": classroom_id, "joining_locked": body.locked}


@router.delete("/classrooms/{classroom_id}")
async def admin_delete_classroom(classroom_id: str, admin: dict = Depends(get_current_admin)):
    """
    DANGER: Hard delete a classroom and all memberships and assignments.
    Labs attached to this classroom are archived, not deleted.
    """
    classroom = await db.classrooms.find_one({"classroom_id": classroom_id})
    if not classroom:
        raise HTTPException(status_code=404, detail="Classroom not found")

    await db.classrooms.delete_one({"classroom_id": classroom_id})
    await db.classroom_memberships.delete_many({"classroom_id": classroom_id})
    await db.assignments.delete_many({"classroom_id": classroom_id})
    await db.submissions.delete_many({"classroom_id": classroom_id})

    # Archive (not delete) labs so student data is preserved
    await db.courses.update_many(
        {"classroom_id": classroom_id, "course_type": "LAB"},
        {"$set": {"status": "ARCHIVED", "archived_at": datetime.utcnow(), "archived_by": admin.get("email")}}
    )

    return {"success": True, "classroom_id": classroom_id, "message": "Classroom deleted. Labs archived."}


# ============================================================================
# TEACHERS
# ============================================================================

@router.get("/teachers")
async def list_all_teachers(
    university_id: Optional[str] = None,
    search: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
    admin: dict = Depends(get_current_admin)
):
    """List all teacher profiles with classroom counts."""
    query = {"role": "teacher"}
    if university_id:
        query["college"] = university_id
    if search:
        query["$or"] = [
            {"username": {"$regex": search, "$options": "i"}},
            {"email_id": {"$regex": search, "$options": "i"}},
        ]

    docs  = await db.users_profile.find(query, {"_id": 0}).sort("created_at", -1).skip(skip).limit(limit).to_list(limit)
    total = await db.users_profile.count_documents(query)

    result = []
    for t in docs:
        t["classroom_count"] = await db.classrooms.count_documents({"teacher_user_id": t["user_id"]})
        result.append(t)

    return {"teachers": result, "count": len(result), "total": total}


@router.get("/teachers/{user_id}")
async def get_teacher_detail(user_id: str, admin: dict = Depends(get_current_admin)):
    """Teacher profile + all their classrooms and courses."""
    teacher = await db.users_profile.find_one({"user_id": user_id}, {"_id": 0})
    if not teacher:
        raise HTTPException(status_code=404, detail="Teacher not found")

    classrooms = await db.classrooms.find({"teacher_user_id": user_id}).to_list(length=None)
    courses    = await db.courses.find({"creator_id": user_id}).to_list(length=None)

    return {
        "teacher": teacher,
        "classrooms": _clean_many(classrooms),
        "courses": _clean_many(courses),
    }


@router.post("/teachers/{user_id}/ban")
async def ban_teacher(user_id: str, body: BanRequest, admin: dict = Depends(get_current_admin)):
    """Ban a teacher — they can still login but cannot create classrooms/courses."""
    result = await db.users_profile.update_one(
        {"user_id": user_id},
        {"$set": {"is_banned": True, "ban_reason": body.reason, "banned_by": admin.get("email"), "banned_at": datetime.utcnow()}}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Teacher not found")
    return {"success": True, "user_id": user_id, "action": "banned"}


@router.post("/teachers/{user_id}/unban")
async def unban_teacher(user_id: str, admin: dict = Depends(get_current_admin)):
    result = await db.users_profile.update_one(
        {"user_id": user_id},
        {"$unset": {"is_banned": "", "ban_reason": "", "banned_by": "", "banned_at": ""}}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Teacher not found")
    return {"success": True, "user_id": user_id, "action": "unbanned"}


# ============================================================================
# STUDENTS
# ============================================================================

@router.get("/students")
async def list_all_students(
    university_id: Optional[str] = None,
    department: Optional[str] = None,
    search: Optional[str] = None,
    is_banned: Optional[bool] = None,
    skip: int = 0,
    limit: int = 50,
    admin: dict = Depends(get_current_admin)
):
    """List all students with optional filters."""
    query = {"role": "student"}
    if university_id:
        query["college"] = university_id
    if department:
        query["department"] = department
    if is_banned is not None:
        query["is_banned"] = is_banned
    if search:
        query["$or"] = [
            {"username": {"$regex": search, "$options": "i"}},
            {"email_id": {"$regex": search, "$options": "i"}},
            {"sidhi_id": {"$regex": search, "$options": "i"}},
        ]

    docs  = await db.users_profile.find(query, {"_id": 0}).sort("created_at", -1).skip(skip).limit(limit).to_list(limit)
    total = await db.users_profile.count_documents(query)

    result = []
    for s in docs:
        s["enrollment_count"] = await db.course_enrollments.count_documents({"user_id": s["user_id"], "is_active": True})
        s["classroom_count"]  = await db.classroom_memberships.count_documents({"student_user_id": s["user_id"], "is_active": True})
        result.append(s)

    return {"students": result, "count": len(result), "total": total}


@router.get("/students/{user_id}")
async def get_student_detail(user_id: str, admin: dict = Depends(get_current_admin)):
    """Full student profile — enrollments, classroom memberships, recent submissions."""
    student = await db.users_profile.find_one({"user_id": user_id}, {"_id": 0})
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    enrollments   = await db.course_enrollments.find({"user_id": user_id}).to_list(length=None)
    memberships   = await db.classroom_memberships.find({"student_user_id": user_id, "is_active": True}).to_list(length=None)
    submissions   = await db.course_submissions.find({"user_id": user_id}).sort("submitted_at", -1).to_list(20)

    return {
        "student": student,
        "enrollments": _clean_many(enrollments),
        "classroom_memberships": _clean_many(memberships),
        "recent_submissions": _clean_many(submissions),
    }


@router.post("/students/{user_id}/ban")
async def ban_student(user_id: str, body: BanRequest, admin: dict = Depends(get_current_admin)):
    result = await db.users_profile.update_one(
        {"user_id": user_id},
        {"$set": {"is_banned": True, "ban_reason": body.reason, "banned_by": admin.get("email"), "banned_at": datetime.utcnow()}}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Student not found")
    return {"success": True, "user_id": user_id, "action": "banned"}


@router.post("/students/{user_id}/unban")
async def unban_student(user_id: str, admin: dict = Depends(get_current_admin)):
    result = await db.users_profile.update_one(
        {"user_id": user_id},
        {"$unset": {"is_banned": "", "ban_reason": "", "banned_by": "", "banned_at": ""}}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Student not found")
    return {"success": True, "user_id": user_id, "action": "unbanned"}


# ============================================================================
# ENROLLMENTS
# ============================================================================

@router.get("/enrollments")
async def list_enrollments(
    course_id: Optional[str] = None,
    user_id: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
    admin: dict = Depends(get_current_admin)
):
    """List all course enrollments with optional filters."""
    query = {}
    if course_id:
        query["course_id"] = course_id
    if user_id:
        query["user_id"] = user_id

    docs  = await db.course_enrollments.find(query).sort("enrolled_at", -1).skip(skip).limit(limit).to_list(limit)
    total = await db.course_enrollments.count_documents(query)

    return {"enrollments": _clean_many(docs), "count": len(docs), "total": total}


@router.delete("/enrollments/{enrollment_id}")
async def admin_force_unenroll(enrollment_id: str, admin: dict = Depends(get_current_admin)):
    """Force-unenroll a student from a course. Their progress data is preserved but enrollment is removed."""
    enr = await db.course_enrollments.find_one({"enrollment_id": enrollment_id})
    if not enr:
        raise HTTPException(status_code=404, detail="Enrollment not found")

    await db.course_enrollments.update_one(
        {"enrollment_id": enrollment_id},
        {"$set": {"is_active": False, "unenrolled_at": datetime.utcnow(), "unenrolled_by": admin.get("email")}}
    )
    await db.courses.update_one(
        {"course_id": enr["course_id"]},
        {"$inc": {"stats.enrollments": -1}}
    )
    return {"success": True, "enrollment_id": enrollment_id, "message": "Student unenrolled"}


# ============================================================================
# SUBMISSIONS
# ============================================================================

@router.get("/submissions")
async def list_all_submissions(
    course_id: Optional[str] = None,
    user_id: Optional[str] = None,
    verdict: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
    admin: dict = Depends(get_current_admin)
):
    """List all course submissions."""
    query = {}
    if course_id:
        query["course_id"] = course_id
    if user_id:
        query["user_id"] = user_id
    if verdict:
        query["verdict"] = verdict

    docs  = await db.course_submissions.find(query).sort("submitted_at", -1).skip(skip).limit(limit).to_list(limit)
    total = await db.course_submissions.count_documents(query)

    return {"submissions": _clean_many(docs), "count": len(docs), "total": total}


@router.get("/submissions/{submission_id}")
async def get_submission_detail(submission_id: str, admin: dict = Depends(get_current_admin)):
    sub = await db.course_submissions.find_one({"submission_id": submission_id})
    if not sub:
        raise HTTPException(status_code=404, detail="Submission not found")
    return _clean(sub)


# ============================================================================
# PLAGIARISM
# ============================================================================

@router.get("/plagiarism")
async def list_plagiarism_flags(
    flag: Optional[str] = None,
    assignment_id: Optional[str] = None,
    reviewed: Optional[bool] = None,
    skip: int = 0,
    limit: int = 50,
    admin: dict = Depends(get_current_admin)
):
    """
    List plagiarism results. Filter by flag level: red, yellow, green.
    reviewed=False shows unreviewed ones needing attention.
    """
    query = {}
    if flag:
        query["flag"] = flag
    if assignment_id:
        query["assignment_id"] = assignment_id
    if reviewed is not None:
        query["reviewed_by_teacher"] = reviewed

    docs  = await db.plagiarism_results.find(query).sort("detected_at", -1).skip(skip).limit(limit).to_list(limit)
    total = await db.plagiarism_results.count_documents(query)

    # Summary counts
    summary = {
        "red":    await db.plagiarism_results.count_documents({"flag": "red"}),
        "yellow": await db.plagiarism_results.count_documents({"flag": "yellow"}),
        "green":  await db.plagiarism_results.count_documents({"flag": "green"}),
        "unreviewed_red": await db.plagiarism_results.count_documents({"flag": "red", "reviewed_by_teacher": False}),
    }

    return {"results": _clean_many(docs), "count": len(docs), "total": total, "summary": summary}


@router.get("/plagiarism/{pair_id}")
async def get_plagiarism_detail(pair_id: str, admin: dict = Depends(get_current_admin)):
    result = await db.plagiarism_results.find_one({"pair_id": pair_id})
    if not result:
        raise HTTPException(status_code=404, detail="Plagiarism result not found")
    return _clean(result)


# ============================================================================
# LEADERBOARD  (admin can see any course's full leaderboard)
# ============================================================================

@router.get("/leaderboard/{course_id}")
async def admin_course_leaderboard(
    course_id: str,
    skip: int = 0,
    limit: int = 100,
    admin: dict = Depends(get_current_admin)
):
    """Full leaderboard for any course (including labs). No restrictions."""
    course = await db.courses.find_one({"course_id": course_id})
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")

    pipeline = [
        {"$match": {"course_id": course_id, "is_active": True}},
        {"$lookup": {"from": "users_profile", "localField": "user_id", "foreignField": "user_id", "as": "user"}},
        {"$unwind": "$user"},
        {"$project": {
            "_id": 0,
            "user_id": 1,
            "sidhi_id": {"$ifNull": ["$sidhi_id", ""]},
            "username": {"$ifNull": ["$user.username", "Anonymous"]},
            "college": "$user.college",
            "department": "$user.department",
            "league": "$current_league",
            "league_points": 1,
            "problems_solved": {"$size": {"$ifNull": ["$solved_questions", []]}},
            "avg_efficiency": {"$ifNull": ["$avg_efficiency", 0.0]},
            "enrolled_at": 1,
        }},
        {"$sort": {"league_points": -1, "problems_solved": -1}},
        {"$skip": skip},
        {"$limit": limit}
    ]

    entries = await db.course_enrollments.aggregate(pipeline).to_list(length=limit)
    for i, e in enumerate(entries):
        e["rank"] = skip + i + 1

    total = await db.course_enrollments.count_documents({"course_id": course_id, "is_active": True})

    return {
        "course_id": course_id,
        "course_title": course.get("title"),
        "course_type": course.get("course_type"),
        "entries": entries,
        "total_enrolled": total,
        "page": skip // limit + 1,
    }


# ============================================================================
# SUPPORT TICKETS  (existing help system)
# ============================================================================

@router.get("/tickets")
async def list_support_tickets(
    status: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
    admin: dict = Depends(get_current_admin)
):
    query = {}
    if status:
        query["status"] = status

    tickets = await db.help_tickets.find(query).sort("created_at", -1).skip(skip).limit(limit).to_list(limit)
    total   = await db.help_tickets.count_documents(query)

    for t in tickets:
        t["_id"] = str(t["_id"])

    return {"tickets": tickets, "count": len(tickets), "total": total}


@router.get("/tickets/{ticket_id}")
async def get_ticket(ticket_id: str, admin: dict = Depends(get_current_admin)):
    ticket = await db.help_tickets.find_one({"_id": _oid(ticket_id)})
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    ticket["_id"] = str(ticket["_id"])
    return ticket


@router.post("/tickets/{ticket_id}/reply")
async def reply_to_ticket(
    ticket_id: str,
    body: TicketReply,
    admin: dict = Depends(get_current_admin)
):
    result = await db.help_tickets.update_one(
        {"_id": _oid(ticket_id)},
        {"$set": {
            "admin_response": body.admin_response,
            "status": "resolved",
            "resolved_at": datetime.utcnow(),
            "resolved_by": admin.get("email"),
        }}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return {"success": True, "ticket_id": ticket_id, "status": "resolved"}


@router.post("/tickets/{ticket_id}/close")
async def close_ticket(ticket_id: str, admin: dict = Depends(get_current_admin)):
    result = await db.help_tickets.update_one(
        {"_id": _oid(ticket_id)},
        {"$set": {"status": "closed", "closed_at": datetime.utcnow(), "closed_by": admin.get("email")}}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return {"success": True, "ticket_id": ticket_id, "status": "closed"}


# ============================================================================
# USERS  (platform accounts — terminal companion side)
# ============================================================================

@router.get("/users")
async def list_platform_users(
    search: Optional[str] = None,
    is_banned: Optional[bool] = None,
    skip: int = 0,
    limit: int = 50,
    admin: dict = Depends(get_current_admin)
):
    query = {}
    if search:
        query["$or"] = [
            {"username": {"$regex": search, "$options": "i"}},
            {"email_id": {"$regex": search, "$options": "i"}},
            {"sidhi_id": {"$regex": search, "$options": "i"}},
        ]
    if is_banned is not None:
        query["is_banned"] = is_banned

    users = await db.users_profile.find(query, {"_id": 0}).sort("created_at", -1).skip(skip).limit(limit).to_list(limit)
    total = await db.users_profile.count_documents(query)

    for u in users:
        quota = await db.quotas.find_one({"sidhi_id": u.get("sidhi_id")}, {"tier": 1, "_id": 0})
        u["tier"] = quota.get("tier") if quota else "none"

    return {"users": users, "count": len(users), "total": total}


@router.get("/users/{sidhi_id}")
async def get_platform_user(sidhi_id: str, admin: dict = Depends(get_current_admin)):
    profile = await db.users_profile.find_one({"sidhi_id": sidhi_id}, {"_id": 0})
    if not profile:
        raise HTTPException(status_code=404, detail="User not found")

    quota    = await db.quotas.find_one({"sidhi_id": sidhi_id}, {"_id": 0})
    payments = await db.payments.find({"sidhi_id": sidhi_id}, {"_id": 0}).sort("created_at", -1).to_list(10)

    return {"profile": profile, "quota": quota, "recent_payments": payments}


@router.put("/users/{sidhi_id}")
async def update_platform_user(
    sidhi_id: str,
    data: UserUpdate,
    admin: dict = Depends(get_current_admin)
):
    fields = {k: v for k, v in data.dict().items() if v is not None}
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")

    result = await db.users_profile.update_one({"sidhi_id": sidhi_id}, {"$set": fields})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="User not found")
    return {"success": True, "updated_fields": list(fields.keys())}


@router.post("/users/{sidhi_id}/ban")
async def ban_platform_user(sidhi_id: str, body: BanRequest, admin: dict = Depends(get_current_admin)):
    result = await db.users_profile.update_one(
        {"sidhi_id": sidhi_id},
        {"$set": {"is_banned": True, "ban_reason": body.reason, "banned_by": admin.get("email"), "banned_at": datetime.utcnow()}}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="User not found")
    return {"success": True, "sidhi_id": sidhi_id, "action": "banned"}


@router.post("/users/{sidhi_id}/unban")
async def unban_platform_user(sidhi_id: str, admin: dict = Depends(get_current_admin)):
    result = await db.users_profile.update_one(
        {"sidhi_id": sidhi_id},
        {"$unset": {"is_banned": "", "ban_reason": "", "banned_by": "", "banned_at": ""}}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="User not found")
    return {"success": True, "sidhi_id": sidhi_id, "action": "unbanned"}


@router.delete("/users/{sidhi_id}")
async def delete_platform_user(sidhi_id: str, admin: dict = Depends(get_current_admin)):
    """DANGER: Hard delete user from all platform collections."""
    await db.users_profile.delete_one({"sidhi_id": sidhi_id})
    await db.users.delete_one({"sid_id": sidhi_id})
    await db.quotas.delete_one({"sidhi_id": sidhi_id})
    await db.history.delete_one({"sidhi_id": sidhi_id})
    await db.cloud_history.delete_one({"sidhi_id": sidhi_id})
    await db.personalization.delete_one({"sidhi_id": sidhi_id})
    await db.help_bot_history.delete_many({"sidhi_id": sidhi_id})
    return {"success": True, "sidhi_id": sidhi_id, "message": "User deleted from all collections"}


# ============================================================================
# PAYMENTS
# ============================================================================

@router.get("/payments")
async def list_payments(
    status: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
    admin: dict = Depends(get_current_admin)
):
    query = {}
    if status:
        query["status"] = status

    payments = await db.payments.find(query, {"_id": 0}).sort("created_at", -1).skip(skip).limit(limit).to_list(limit)
    total    = await db.payments.count_documents(query)

    return {"payments": payments, "count": len(payments), "total": total}


@router.get("/payments/{order_id}")
async def get_payment(order_id: str, admin: dict = Depends(get_current_admin)):
    payment = await db.payments.find_one({"razorpay_order_id": order_id}, {"_id": 0})
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    return payment


@router.put("/payments/{order_id}/refund")
async def refund_payment(order_id: str, body: RefundRequest, admin: dict = Depends(get_current_admin)):
    """Mark a payment as refunded. Process the actual refund in Razorpay dashboard separately."""
    result = await db.payments.update_one(
        {"razorpay_order_id": order_id},
        {"$set": {
            "status": "refunded",
            "refund_reason": body.refund_reason,
            "refunded_at": datetime.utcnow(),
            "refunded_by": admin.get("email")
        }}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Payment not found")
    return {"success": True, "order_id": order_id, "note": "Process actual refund in Razorpay dashboard"}


# ============================================================================
# NOTIFICATIONS
# ============================================================================

@router.post("/notifications/send")
async def send_notification(body: NotificationRequest, admin: dict = Depends(get_current_admin)):
    if body.target_type == "specific" and not body.target_users:
        raise HTTPException(status_code=400, detail="target_users required for specific notifications")

    expires_at = datetime.utcnow() + timedelta(hours=body.expires_hours) if body.expires_hours else None

    doc = {
        "title": body.title, "message": body.message,
        "type": body.type, "priority": body.priority,
        "target_type": body.target_type, "target_users": body.target_users or [],
        "expires_at": expires_at, "created_at": datetime.utcnow(),
        "created_by": admin.get("email"), "read_by": []
    }
    result = await db.notifications.insert_one(doc)
    return {
        "success": True,
        "notification_id": str(result.inserted_id),
        "target_count": len(body.target_users) if body.target_type == "specific" else "all"
    }


@router.get("/notifications/history")
async def notification_history(
    skip: int = 0,
    limit: int = 50,
    admin: dict = Depends(get_current_admin)
):
    notifs = await db.notifications.find({}).sort("created_at", -1).skip(skip).limit(limit).to_list(limit)
    total  = await db.notifications.count_documents({})
    for n in notifs:
        n["_id"] = str(n["_id"])
        n["read_count"] = len(n.get("read_by", []))
    return {"notifications": notifs, "count": len(notifs), "total": total}


@router.delete("/notifications/{notification_id}")
async def delete_notification(notification_id: str, admin: dict = Depends(get_current_admin)):
    result = await db.notifications.delete_one({"_id": _oid(notification_id)})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Notification not found")
    return {"success": True, "notification_id": notification_id}


# ============================================================================
# EXPORTS  (CSV downloads)
# ============================================================================

def _csv_response(rows: list[dict], filename: str) -> StreamingResponse:
    if not rows:
        return StreamingResponse(iter([""]), media_type="text/csv",
                                 headers={"Content-Disposition": f"attachment; filename={filename}"})
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=rows[0].keys())
    writer.writeheader()
    writer.writerows(rows)
    output.seek(0)
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f"attachment; filename={filename}"})


@router.get("/export/enquiries")
async def export_enquiries(
    status: Optional[str] = None,
    type: Optional[str] = None,
    admin: dict = Depends(get_current_admin)
):
    """Export all enquiries as CSV."""
    query = {}
    if status and status != "all":
        query["status"] = status
    if type and type != "all":
        query["type"] = type

    docs = await db.enquiries.find(query).sort("submitted_at", -1).to_list(length=None)
    rows = []
    for d in docs:
        rows.append({
            "enquiry_id": d.get("enquiry_id"), "type": d.get("type"),
            "status": d.get("status"), "name": d.get("name"),
            "email": d.get("email"), "phone": d.get("phone", ""),
            "college_name": d.get("college_name", ""), "designation": d.get("designation", ""),
            "message": d.get("message", ""), "submitted_at": _iso(d.get("submitted_at")),
            "admin_response": d.get("admin_response", ""), "responded_by": d.get("responded_by", ""),
            "responded_at": _iso(d.get("responded_at")),
        })
    return _csv_response(rows, "enquiries_export.csv")


@router.get("/export/courses")
async def export_courses(admin: dict = Depends(get_current_admin)):
    """Export all courses as CSV."""
    docs = await db.courses.find({}).sort("created_at", -1).to_list(length=None)
    rows = []
    for d in docs:
        rows.append({
            "course_id": d.get("course_id"), "title": d.get("title"),
            "course_type": d.get("course_type"), "domain": d.get("domain"),
            "status": d.get("status"), "creator_id": d.get("creator_id"),
            "classroom_id": d.get("classroom_id", ""),
            "enrollments": d.get("stats", {}).get("enrollments", 0),
            "is_free": d.get("pricing", {}).get("is_free", True),
            "created_at": _iso(d.get("created_at")), "published_at": _iso(d.get("published_at")),
        })
    return _csv_response(rows, "courses_export.csv")


@router.get("/export/students")
async def export_students(
    university_id: Optional[str] = None,
    admin: dict = Depends(get_current_admin)
):
    """Export all students as CSV."""
    query = {"role": "student"}
    if university_id:
        query["college"] = university_id

    docs = await db.users_profile.find(query, {"_id": 0}).sort("created_at", -1).to_list(length=None)
    rows = []
    for d in docs:
        rows.append({
            "user_id": d.get("user_id"), "sidhi_id": d.get("sidhi_id"),
            "username": d.get("username"), "email_id": d.get("email_id"),
            "college": d.get("college"), "department": d.get("department"),
            "degree": d.get("degree"), "is_banned": d.get("is_banned", False),
            "created_at": _iso(d.get("created_at")),
        })
    return _csv_response(rows, "students_export.csv")


@router.get("/export/submissions")
async def export_submissions(
    course_id: Optional[str] = None,
    admin: dict = Depends(get_current_admin)
):
    """Export course submissions as CSV."""
    query = {}
    if course_id:
        query["course_id"] = course_id

    docs = await db.course_submissions.find(query).sort("submitted_at", -1).to_list(length=None)
    rows = []
    for d in docs:
        rows.append({
            "submission_id": d.get("submission_id"), "course_id": d.get("course_id"),
            "question_id": d.get("question_id"), "user_id": d.get("user_id"),
            "language": d.get("language"), "verdict": d.get("verdict", ""),
            "score": d.get("score", ""), "status": d.get("status"),
            "submitted_at": _iso(d.get("submitted_at")),
        })
    return _csv_response(rows, "submissions_export.csv")
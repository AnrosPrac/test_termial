"""
COURSE CLAIM SYSTEM - COLLEGE NAME BASED
File: app/courses/claim_router.py

LOGIC:
  Student's profile has college (e.g. "Anna University")
  Course has allowed_colleges list (e.g. ["Anna University", "PSG Tech"])
  If profile.college is in allowed_colleges → instant free access ✅

TWO PATHS:
  PATH A - Auto claim (college name matches):
    POST /claim { course_id }
    → read profile.college → in allowed_colleges? → approved instantly

  PATH B - Manual request (college not in list OR no college in profile):
    POST /access-request { course_id, full_name, roll_number,
                           year_of_study, college_name, branch_name,
                           college_email }
    → admin reviews → approve/reject

ADMIN SEES:
  - Student's college name from profile
  - Their registered email domain
  - Whether college name matched but email domain looks suspicious
  - Can approve or revoke at any time

RULES:
  ✅ One lifetime claim per student (both paths share this limit)
  ✅ Auto-approved if college is in allowed_colleges list
  ✅ Manual request if not — admin reviews
  ✅ Admin can revoke any time → access removed instantly
"""

import uuid
import re
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, validator

from app.courses.dependencies import get_db, get_current_user_id, get_sidhi_id

router = APIRouter(tags=["Course Claims"])


# ==================== MODELS ====================

class ClaimRequest(BaseModel):
    course_id: str

class AccessRequestCreate(BaseModel):
    course_id:     str
    full_name:     str
    roll_number:   str
    year_of_study: int
    college_name:  str
    branch_name:   str
    college_email: str

    @validator("year_of_study")
    def valid_year(cls, v):
        if v not in (1, 2, 3, 4):
            raise ValueError("year_of_study must be 1–4")
        return v

    @validator("college_email")
    def valid_email(cls, v):
        v = v.lower().strip()
        if not re.match(r"^[\w\.\+\-]+@[\w\-]+\.[\w\.\-]+$", v):
            raise ValueError("Invalid email format")
        return v

    @validator("full_name", "roll_number", "college_name", "branch_name")
    def not_empty(cls, v):
        v = v.strip()
        if not v:
            raise ValueError("Field cannot be empty")
        return v


# ==================== HELPERS ====================

def _extract_domain(email: str) -> str:
    return email.split("@")[-1].lower() if "@" in email else ""

def _iso(dt):
    return dt.isoformat() if isinstance(dt, datetime) else dt

def _clean(doc: dict) -> dict:
    doc.pop("_id", None)
    for f in ("submitted_at", "reviewed_at", "approved_at",
              "created_at", "revoked_at"):
        if doc.get(f):
            doc[f] = _iso(doc[f])
    return doc

async def _lifetime_check(db, user_id: str):
    """Raise 409 if student already has an approved claim or request."""
    used = await db.course_claim_access.find_one({
        "user_id": user_id,
        "access_granted": True
    })
    if used:
        raise HTTPException(status_code=409, detail={
            "message":   "You have already used your one lifetime course claim.",
            "course_id": used.get("course_id")
        })


# ==================== PATH A: AUTO CLAIM (college name match) ====================

@router.get("/claimable-courses")
async def get_claimable_courses(
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    """
    All courses with claim access enabled.
    Marks which ones this student is instantly eligible for
    (their profile college is in allowed_colleges).
    """
    profile       = await db.users_profile.find_one({"user_id": user_id})
    student_college = (profile.get("college") or "").strip() if profile else ""

    used = await db.course_claim_access.find_one({"user_id": user_id, "access_granted": True})

    cursor  = db.courses.find(
        {"claim_access.enabled": True, "status": {"$in": ["PUBLISHED", "ACTIVE"]}},
        {"course_id": 1, "title": 1, "description": 1,
         "domain": 1, "thumbnail_url": 1, "claim_access": 1, "pricing": 1}
    )
    courses = await cursor.to_list(100)

    result = []
    for c in courses:
        c.pop("_id", None)
        allowed = c.get("claim_access", {}).get("allowed_colleges", [])
        result.append({
            "course_id":        c["course_id"],
            "title":            c["title"],
            "description":      c.get("description", ""),
            "domain":           c.get("domain", ""),
            "thumbnail_url":    c.get("thumbnail_url"),
            "claim_label":      c.get("claim_access", {}).get("claim_label", "College Students"),
            "allowed_colleges": allowed,
            "original_price":   c.get("pricing", {}).get("price", 0) // 100,
            # instantly claimable if their college is in the list
            "auto_eligible":    student_college in allowed,
        })

    return {
        "claimable_courses":  result,
        "count":              len(result),
        "your_college":       student_college,
        "claim_used":         used is not None,
        "claimed_course_id":  used.get("course_id") if used else None
    }


@router.post("/claim")
async def auto_claim(
    payload: ClaimRequest,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
    sidhi_id: str = Depends(get_sidhi_id)
):
    """
    ONE-TAP AUTO CLAIM.

    Reads student's college from profile.
    Checks against course's allowed_colleges.
    If match → approved instantly. No form, no email, no OTP.
    """
    course_id = payload.course_id

    # Lifetime limit
    await _lifetime_check(db, user_id)

    # Get student profile
    profile = await db.users_profile.find_one({"user_id": user_id})
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found.")

    student_college = (profile.get("college") or "").strip()
    student_email   = profile.get("email_id", "")
    email_domain    = _extract_domain(student_email)

    if not student_college:
        raise HTTPException(
            status_code=400,
            detail="No college found in your profile. Use the manual request form instead."
        )

    # Get course
    course = await db.courses.find_one({"course_id": course_id})
    if not course:
        raise HTTPException(status_code=404, detail="Course not found.")

    claim_access     = course.get("claim_access", {})
    allowed_colleges = claim_access.get("allowed_colleges", [])

    if not claim_access.get("enabled", False):
        raise HTTPException(status_code=400, detail="This course does not support claiming.")

    if student_college not in allowed_colleges:
        raise HTTPException(status_code=403, detail={
            "message":          f"'{student_college}' is not in the eligible colleges list for this course.",
            "your_college":     student_college,
            "tip":              "Submit a manual request if you believe this is an error."
        })

    # ── AUTO APPROVED ──────────────────────────────────────────────────────────
    now      = datetime.utcnow()
    claim_id = f"CLM_{uuid.uuid4().hex[:10].upper()}"

    await db.course_claims.insert_one({
        "claim_id":       claim_id,
        "course_id":      course_id,
        "course_title":   course.get("title", ""),
        "user_id":        user_id,
        "sidhi_id":       sidhi_id,
        "student_college": student_college,
        "email_domain":   email_domain,   # stored so admin can spot mismatches
        "college":        student_college,
        "department":     profile.get("department", ""),
        "status":         "approved",
        "approved_at":    now,
        "approved_via":   "college_name_match",
        "created_at":     now,
    })

    # Access grant — CHECK 0 in enrollment_router reads this
    await db.course_claim_access.update_one(
        {"user_id": user_id, "course_id": course_id},
        {"$set": {
            "user_id":         user_id,
            "sidhi_id":        sidhi_id,
            "course_id":       course_id,
            "claim_id":        claim_id,
            "student_college": student_college,
            "email_domain":    email_domain,
            "access_granted":  True,
            "granted_at":      now,
            "granted_via":     "college_name_match"
        }},
        upsert=True
    )

    return {
        "success":        True,
        "claim_id":       claim_id,
        "course_id":      course_id,
        "course_title":   course.get("title", ""),
        "student_college": student_college,
        "message":        f"Course claimed via {student_college}! You can now enroll for free.",
        "access_reason":  "domain_claim"
    }


# ==================== PATH B: MANUAL REQUEST ====================

@router.post("/access-request")
async def submit_access_request(
    payload: AccessRequestCreate,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
    sidhi_id: str = Depends(get_sidhi_id)
):
    """
    Manual access request for students whose college isn't in the auto list.
    Admin reviews and approves/rejects.
    """
    # Lifetime limit
    await _lifetime_check(db, user_id)

    # One pending request at a time
    pending = await db.course_access_requests.find_one({"user_id": user_id, "status": "pending"})
    if pending:
        raise HTTPException(status_code=409, detail={
            "message":    "You already have a pending request.",
            "request_id": pending["request_id"],
        })

    course = await db.courses.find_one({"course_id": payload.course_id})
    if not course:
        raise HTTPException(status_code=404, detail="Course not found.")
    if course.get("status") not in ("PUBLISHED", "ACTIVE"):
        raise HTTPException(status_code=400, detail="Course not available.")

    # Pull their registered email from profile for admin's reference
    profile      = await db.users_profile.find_one({"user_id": user_id})
    profile_email = profile.get("email_id", "") if profile else ""
    profile_college = (profile.get("college") or "").strip() if profile else ""

    request_id = f"REQ_{uuid.uuid4().hex[:10].upper()}"
    now        = datetime.utcnow()

    await db.course_access_requests.insert_one({
        "request_id":     request_id,
        "course_id":      payload.course_id,
        "course_title":   course.get("title", ""),
        "user_id":        user_id,
        "sidhi_id":       sidhi_id,

        # Form fields
        "full_name":      payload.full_name,
        "roll_number":    payload.roll_number,
        "year_of_study":  payload.year_of_study,
        "college_name":   payload.college_name,      # what they claim
        "branch_name":    payload.branch_name,
        "college_email":  payload.college_email,

        # Profile data stored for admin comparison
        "profile_college": profile_college,          # what's in their profile
        "profile_email":   profile_email,            # their login email
        # Admin sees: does college_name match profile_college? does email domain match?
        "college_match":  payload.college_name.strip() == profile_college,
        "domain_match":   _extract_domain(payload.college_email) == _extract_domain(profile_email),

        "status":        "pending",
        "submitted_at":  now,
        "reviewed_at":   None,
        "reviewed_by":   None,
        "admin_note":    None,
    })

    return {
        "success":    True,
        "request_id": request_id,
        "status":     "pending",
        "message":    "Request submitted. Admin will review it shortly.",
        "course_id":  payload.course_id
    }


@router.get("/access-request/my-status")
async def my_request_status(
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    """Student checks status of their access request."""
    request = await db.course_access_requests.find_one(
        {"user_id": user_id},
        sort=[("submitted_at", -1)]
    )
    if not request:
        return {"has_request": False, "message": "No request submitted yet."}
    return {"has_request": True, "request": _clean(request)}


@router.get("/claim/my-status")
async def my_claim_status(
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    """Student checks status of their auto-claim."""
    access = await db.course_claim_access.find_one({"user_id": user_id})
    if not access:
        profile        = await db.users_profile.find_one({"user_id": user_id})
        student_college = (profile.get("college") or "") if profile else ""
        return {
            "has_claim":   False,
            "your_college": student_college,
            "message":     "No claim yet. You have 1 lifetime claim available."
        }
    access.pop("_id", None)
    return {"has_claim": True, "access": access}
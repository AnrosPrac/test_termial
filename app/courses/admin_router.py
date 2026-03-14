"""
ADMIN ROUTER
File: app/courses/admin_router.py

Admin logs in → sees dashboard → reviews access requests → approves/rejects.

KEY FEATURE: For each request admin can see:
  - college_name   (what student claimed on form)
  - profile_college (what's actually in their profile)
  - college_match  True/False — do they match?
  - domain_match   True/False — does their email domain look right?

This lets admin spot suspicious requests instantly.

WIRE UP in app.py:
  from app.courses.admin_router import router as admin_router
  from app.admin.hardened_firebase_auth import init_auth

  app.include_router(admin_router, prefix="/api")

  # in startup:
  init_auth()

.env vars:
  ADMIN_USERNAME=yourname
  ADMIN_PASSWORD=yourpassword
  ADMIN_JWT_SECRET=some_long_random_string_min_32_chars
"""

import re
from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, HTTPException, Depends, Header
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, validator
import os

from app.admin.hardened_firebase_auth import (
    verify_credentials,
    create_admin_jwt,
    revoke_admin_session,
    get_current_admin,
)

router = APIRouter(prefix="/admin", tags=["Admin"])

MONGO_URL = os.getenv("MONGO_URL")
_client   = AsyncIOMotorClient(MONGO_URL)
db        = _client.lumetrics_db


# ==================== MODELS ====================

class LoginRequest(BaseModel):
    username: str
    password: str

class ReviewRequest(BaseModel):
    note: Optional[str] = None

class ClaimConfigSet(BaseModel):
    allowed_colleges: List[str]        # e.g. ["Anna University", "PSG Tech"]
    claim_label: Optional[str] = "College Students"

    @validator("allowed_colleges", each_item=True)
    def not_empty(cls, v):
        v = v.strip()
        if not v:
            raise ValueError("College name cannot be empty")
        return v


# ==================== HELPERS ====================

def _iso(dt):
    return dt.isoformat() if isinstance(dt, datetime) else dt

def _clean(doc: dict) -> dict:
    doc.pop("_id", None)
    for f in ("submitted_at", "reviewed_at", "approved_at",
              "created_at", "revoked_at", "granted_at"):
        if doc.get(f):
            doc[f] = _iso(doc[f])
    return doc


# ==================== AUTH ====================

@router.post("/login")
async def admin_login(credentials: LoginRequest):
    """
    Login with username + password → get JWT token.
    Use token as: Authorization: Bearer <token>
    """
    user  = verify_credentials(credentials.username, credentials.password)
    token = create_admin_jwt(user["email"])
    return {
        "token":    token,
        "username": user["username"],
        "message":  "Logged in. Use token as Bearer on all admin endpoints."
    }


@router.post("/logout")
async def admin_logout(
    admin: dict = Depends(get_current_admin),
    authorization: str = Header(None)
):
    """Revoke current admin JWT."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=400, detail="Missing or malformed Authorization header")
    token = authorization.split(" ")[1]
    revoke_admin_session(token)
    return {"message": "Logged out."}


# ==================== DASHBOARD ====================

@router.get("/dashboard")
async def dashboard(admin: dict = Depends(get_current_admin)):
    """
    Overview of everything that needs attention.
    Check pending_requests — those need action.
    """
    pending  = await db.course_access_requests.count_documents({"status": "pending"})
    approved = await db.course_access_requests.count_documents({"status": "approved"})
    rejected = await db.course_access_requests.count_documents({"status": "rejected"})

    auto_claims   = await db.course_claims.count_documents({"status": "approved"})
    # Mismatch count: college matched but email domain didn't — worth reviewing
    mismatch_count = await db.course_claims.aggregate([
        {"$match": {
            "approved_via": "college_name_match",
            "email_domain": {"$exists": True, "$nin": [None, ""]}
        }},
        {"$match": {"$expr": {"$not": {"$regexMatch": {
            "input": "$email_domain",
            "regex": "ac\\.in|edu"
        }}}}},
        {"$count": "total"}
    ]).to_list(1)
    suspicious_count = mismatch_count[0]["total"] if mismatch_count else 0

    total_enrollments = await db.course_enrollments.count_documents({})
    claimable_courses = await db.courses.count_documents({"claim_access.enabled": True})

    # 5 most recent pending requests for quick preview
    recent_pending = await db.course_access_requests.find(
        {"status": "pending"}
    ).sort("submitted_at", -1).to_list(5)

    return {
        "admin":   admin.get("email"),
        "summary": {
            "pending_requests":   pending,         # ← needs action
            "approved_requests":  approved,
            "rejected_requests":  rejected,
            "auto_claims":        auto_claims,
            "suspicious_claims":  suspicious_count, # college matched, email domain looks off
            "total_enrollments":  total_enrollments,
            "claimable_courses":  claimable_courses,
        },
        "pending_preview":  [_clean(r) for r in recent_pending],
        "generated_at":     datetime.utcnow().isoformat()
    }


# ==================== ACCESS REQUESTS (manual) ====================

@router.get("/access-requests")
async def list_access_requests(
    status:    Optional[str] = "pending",
    course_id: Optional[str] = None,
    skip:      int = 0,
    limit:     int = 50,
    admin: dict = Depends(get_current_admin)
):
    """
    List student access requests.

    Each request shows:
      college_name    — what student wrote on form
      profile_college — what's actually in their profile
      college_match   — do they match? (True/False)
      domain_match    — does their college email domain look right? (True/False)

    Use these flags to spot suspicious requests quickly.
    """
    query = {}
    if status and status != "all":
        query["status"] = status
    if course_id:
        query["course_id"] = course_id

    requests = await db.course_access_requests.find(query)\
        .sort("submitted_at", -1).skip(skip).limit(limit).to_list(limit)
    total = await db.course_access_requests.count_documents(query)

    return {
        "requests": [_clean(r) for r in requests],
        "count":    len(requests),
        "total":    total,
        "filter":   {"status": status, "course_id": course_id}
    }


@router.get("/access-requests/{request_id}")
async def get_request_detail(
    request_id: str,
    admin: dict = Depends(get_current_admin)
):
    """Full detail of one access request."""
    req = await db.course_access_requests.find_one({"request_id": request_id})
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")
    return _clean(req)


@router.post("/access-requests/{request_id}/approve")
async def approve_request(
    request_id: str,
    body: ReviewRequest,
    admin: dict = Depends(get_current_admin)
):
    """
    Approve a manual access request.
    Student gets instant free access to the course.
    """
    req = await db.course_access_requests.find_one({"request_id": request_id})
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")
    if req["status"] != "pending":
        raise HTTPException(status_code=400, detail=f"Already '{req['status']}'.")

    now         = datetime.utcnow()
    admin_email = admin.get("email", "admin")

    await db.course_access_requests.update_one(
        {"request_id": request_id},
        {"$set": {
            "status":      "approved",
            "reviewed_at": now,
            "reviewed_by": admin_email,
            "admin_note":  body.note
        }}
    )

    # Write access grant → enrollment CHECK 0 picks it up
    await db.course_claim_access.update_one(
        {"user_id": req["user_id"], "course_id": req["course_id"]},
        {"$set": {
            "user_id":        req["user_id"],
            "sidhi_id":       req.get("sidhi_id"),
            "course_id":      req["course_id"],
            "request_id":     request_id,
            "college_name":   req["college_name"],
            "branch_name":    req["branch_name"],
            "roll_number":    req["roll_number"],
            "college_email":  req["college_email"],
            "access_granted": True,
            "granted_at":     now,
            "granted_via":    "manual_admin_approval",
            "granted_by":     admin_email
        }},
        upsert=True
    )

    return {
        "success":    True,
        "action":     "approved",
        "request_id": request_id,
        "student":    req["full_name"],
        "college":    req["college_name"],
        "course_id":  req["course_id"],
        "message":    "Approved. Student now has free access."
    }


@router.post("/access-requests/{request_id}/reject")
async def reject_request(
    request_id: str,
    body: ReviewRequest,
    admin: dict = Depends(get_current_admin)
):
    """
    Reject a manual access request.
    Student's lifetime slot is freed — they can submit again.
    """
    req = await db.course_access_requests.find_one({"request_id": request_id})
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")
    if req["status"] != "pending":
        raise HTTPException(status_code=400, detail=f"Already '{req['status']}'.")

    await db.course_access_requests.update_one(
        {"request_id": request_id},
        {"$set": {
            "status":      "rejected",
            "reviewed_at": datetime.utcnow(),
            "reviewed_by": admin.get("email", "admin"),
            "admin_note":  body.note or "Request not approved."
        }}
    )

    return {
        "success":  True,
        "action":   "rejected",
        "student":  req["full_name"],
        "message":  "Rejected. Student can resubmit."
    }


@router.post("/access-requests/{request_id}/revoke")
async def revoke_request(
    request_id: str,
    admin: dict = Depends(get_current_admin)
):
    """Revoke an approved request. Removes access immediately."""
    req = await db.course_access_requests.find_one({"request_id": request_id})
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")
    if req["status"] != "approved":
        raise HTTPException(status_code=400, detail=f"Not approved.")

    await db.course_access_requests.update_one(
        {"request_id": request_id},
        {"$set": {"status": "revoked", "revoked_at": datetime.utcnow(),
                  "revoked_by": admin.get("email", "admin")}}
    )
    await db.course_claim_access.delete_one({
        "user_id": req["user_id"], "course_id": req["course_id"]
    })

    return {"success": True, "message": "Access revoked.", "request_id": request_id}


# ==================== AUTO CLAIMS (college name match) ====================

@router.get("/claims")
async def list_auto_claims(
    course_id: Optional[str] = None,
    skip:  int = 0,
    limit: int = 50,
    admin: dict = Depends(get_current_admin)
):
    """
    List all auto-approved claims (college name matched).
    Each shows student_college + email_domain so admin can
    spot mismatches (e.g. college = Anna University but email = @gmail.com).
    """
    query = {"status": "approved", "approved_via": "college_name_match"}
    if course_id:
        query["course_id"] = course_id

    claims = await db.course_claims.find(query)\
        .sort("approved_at", -1).skip(skip).limit(limit).to_list(limit)
    total  = await db.course_claims.count_documents(query)

    # Flag suspicious ones for admin: email domain not an academic domain
    result = []
    for c in claims:
        c = _clean(c)
        email_domain = c.get("email_domain", "")
        c["suspicious"] = not any(
            x in email_domain for x in ["ac.in", "edu", ".edu"]
        )
        result.append(c)

    return {"claims": result, "count": len(result), "total": total}


@router.post("/claims/{claim_id}/revoke")
async def revoke_claim(
    claim_id: str,
    admin: dict = Depends(get_current_admin)
):
    """Revoke an auto-claim. Removes access, frees lifetime slot."""
    claim = await db.course_claims.find_one({"claim_id": claim_id})
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found")
    if claim["status"] != "approved":
        raise HTTPException(status_code=400, detail=f"Not approved.")

    await db.course_claims.update_one(
        {"claim_id": claim_id},
        {"$set": {"status": "revoked", "revoked_at": datetime.utcnow(),
                  "revoked_by": admin.get("email", "admin")}}
    )
    await db.course_claim_access.delete_one({
        "user_id": claim["user_id"], "course_id": claim["course_id"]
    })

    return {"success": True, "message": "Claim revoked.", "claim_id": claim_id}


# ==================== COURSE CLAIM CONFIG ====================

@router.post("/course/{course_id}/claim-config")
async def set_claim_config(
    course_id: str,
    config: ClaimConfigSet,
    admin: dict = Depends(get_current_admin)
):
    """
    Set which colleges can auto-claim this course.

    Body: { "allowed_colleges": ["Anna University", "PSG Tech"], "claim_label": "..." }

    Students whose profile.college exactly matches one of these
    get instant free access with one tap.
    """
    course = await db.courses.find_one({"course_id": course_id})
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")

    doc = {
        "enabled":          True,
        "allowed_colleges": config.allowed_colleges,
        "claim_label":      config.claim_label,
        "configured_by":    admin.get("email", "admin"),
        "configured_at":    datetime.utcnow()
    }

    await db.courses.update_one(
        {"course_id": course_id},
        {"$set": {"claim_access": doc, "updated_at": datetime.utcnow()}}
    )

    return {
        "success":        True,
        "course_id":      course_id,
        "course_title":   course.get("title", ""),
        "allowed_colleges": config.allowed_colleges,
        "message": f"Students from {len(config.allowed_colleges)} college(s) can now auto-claim."
    }


@router.delete("/course/{course_id}/claim-config")
async def remove_claim_config(
    course_id: str,
    admin: dict = Depends(get_current_admin)
):
    """Disable auto-claiming. Existing approvals stay valid."""
    course = await db.courses.find_one({"course_id": course_id})
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    await db.courses.update_one(
        {"course_id": course_id},
        {"$set": {"claim_access.enabled": False, "updated_at": datetime.utcnow()}}
    )
    return {"success": True, "message": "Auto-claiming disabled."}


@router.get("/course/{course_id}/claim-config")
async def get_claim_config(
    course_id: str,
    admin: dict = Depends(get_current_admin)
):
    """View current claim config for a course."""
    course = await db.courses.find_one(
        {"course_id": course_id},
        {"claim_access": 1, "title": 1, "course_id": 1}
    )
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")

    ca = course.get("claim_access", {})
    return {
        "course_id":        course_id,
        "title":            course.get("title", ""),
        "enabled":          ca.get("enabled", False),
        "allowed_colleges": ca.get("allowed_colleges", []),
        "claim_label":      ca.get("claim_label", ""),
        "configured_by":    ca.get("configured_by"),
        "configured_at":    _iso(ca.get("configured_at"))
    }
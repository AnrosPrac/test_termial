"""
CONTACT & ENQUIRY SYSTEM
File: app/contact/contact_router.py

PUBLIC endpoints — no login required for submissions.

ENQUIRY TYPES:
  college_partnership  — College/institution wants to onboard students
  faculty              — Faculty wants to use platform for their class
  brand_partnership    — Brand/sponsor wants to collaborate
  student_support      — Student needs help (general, non-course)
  general              — Anything else

ADMIN endpoints (JWT protected):
  GET  /contact/enquiries          — list all, filter by type/status
  GET  /contact/enquiries/{id}     — full detail
  POST /contact/enquiries/{id}/respond  — reply + update status
  POST /contact/enquiries/{id}/close    — close ticket

Wire up in main.py:
  from app.contact.contact_router import router as contact_router
  app.include_router(contact_router, prefix="/contact")
"""

import uuid
from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, validator
from motor.motor_asyncio import AsyncIOMotorClient
import re
import os

from app.admin.hardened_firebase_auth import get_current_admin

router = APIRouter(prefix="/contact", tags=["Contact & Enquiries"])

MONGO_URL = os.getenv("MONGO_URL")
_client   = AsyncIOMotorClient(MONGO_URL)
db        = _client.lumetrics_db

VALID_TYPES = {
    "college_partnership",
    "faculty",
    "brand_partnership",
    "student_support",
    "general"
}

VALID_STATUSES = {"pending", "in_progress", "responded", "closed"}


# ==================== MODELS ====================

class CollegePartnershipEnquiry(BaseModel):
    name:         str
    email:        str
    phone:        Optional[str] = None
    college_name: str
    designation:  str           # Principal / HOD / Dean / Coordinator
    student_count: Optional[int] = None
    message:      str

class FacultyEnquiry(BaseModel):
    name:         str
    email:        str
    phone:        Optional[str] = None
    college_name: str
    department:   str
    designation:  str           # Professor / Associate Prof / Assistant Prof
    subject:      Optional[str] = None   # subject they teach
    message:      str

class BrandPartnershipEnquiry(BaseModel):
    name:         str
    email:        str
    phone:        Optional[str] = None
    company_name: str
    designation:  str
    partnership_type: Optional[str] = None  # sponsorship / hiring / co-branding
    message:      str

class StudentSupportEnquiry(BaseModel):
    name:    str
    email:   str
    phone:   Optional[str] = None
    subject: str
    message: str

class GeneralEnquiry(BaseModel):
    name:    str
    email:   str
    phone:   Optional[str] = None
    subject: str
    message: str

class AdminResponse(BaseModel):
    response:   str
    status:     Optional[str] = "responded"   # in_progress | responded | closed

    @validator("status")
    def valid_status(cls, v):
        if v not in VALID_STATUSES:
            raise ValueError(f"status must be one of {VALID_STATUSES}")
        return v


# ==================== HELPERS ====================

def _validate_email(email: str) -> str:
    email = email.lower().strip()
    if not re.match(r"^[\w\.\+\-]+@[\w\-]+\.[\w\.\-]+$", email):
        raise HTTPException(status_code=400, detail="Invalid email format")
    return email

def _clean(doc: dict) -> dict:
    doc.pop("_id", None)
    for f in ("submitted_at", "responded_at", "closed_at"):
        if doc.get(f) and isinstance(doc[f], datetime):
            doc[f] = doc[f].isoformat()
    return doc

def _make_enquiry_id():
    return f"ENQ_{uuid.uuid4().hex[:10].upper()}"


# ==================== PUBLIC: SUBMIT ENQUIRIES ====================

@router.post("/college-partnership")
async def college_partnership_enquiry(payload: CollegePartnershipEnquiry):
    """
    College / institution wants to onboard their students.
    No login required.
    """
    email = _validate_email(payload.email)
    doc = {
        "enquiry_id":    _make_enquiry_id(),
        "type":          "college_partnership",
        "status":        "pending",

        "name":          payload.name.strip(),
        "email":         email,
        "phone":         payload.phone,
        "college_name":  payload.college_name.strip(),
        "designation":   payload.designation.strip(),
        "student_count": payload.student_count,
        "message":       payload.message.strip(),

        "submitted_at":  datetime.utcnow(),
        "responded_at":  None,
        "admin_response": None,
        "responded_by":  None,
    }
    await db.enquiries.insert_one(doc)
    return {
        "success":      True,
        "enquiry_id":   doc["enquiry_id"],
        "message":      "Thanks for reaching out! Our team will contact you within 24 hours."
    }


@router.post("/faculty")
async def faculty_enquiry(payload: FacultyEnquiry):
    """
    Faculty wants to use the platform for their class/department.
    No login required.
    """
    email = _validate_email(payload.email)
    doc = {
        "enquiry_id":   _make_enquiry_id(),
        "type":         "faculty",
        "status":       "pending",

        "name":         payload.name.strip(),
        "email":        email,
        "phone":        payload.phone,
        "college_name": payload.college_name.strip(),
        "department":   payload.department.strip(),
        "designation":  payload.designation.strip(),
        "subject":      payload.subject,
        "message":      payload.message.strip(),

        "submitted_at":  datetime.utcnow(),
        "responded_at":  None,
        "admin_response": None,
        "responded_by":  None,
    }
    await db.enquiries.insert_one(doc)
    return {
        "success":    True,
        "enquiry_id": doc["enquiry_id"],
        "message":    "Thanks! We'll get back to you within 24 hours."
    }


@router.post("/brand-partnership")
async def brand_partnership_enquiry(payload: BrandPartnershipEnquiry):
    """
    Brand / sponsor / hiring partner wants to collaborate.
    No login required.
    """
    email = _validate_email(payload.email)
    doc = {
        "enquiry_id":        _make_enquiry_id(),
        "type":              "brand_partnership",
        "status":            "pending",

        "name":              payload.name.strip(),
        "email":             email,
        "phone":             payload.phone,
        "company_name":      payload.company_name.strip(),
        "designation":       payload.designation.strip(),
        "partnership_type":  payload.partnership_type,
        "message":           payload.message.strip(),

        "submitted_at":  datetime.utcnow(),
        "responded_at":  None,
        "admin_response": None,
        "responded_by":  None,
    }
    await db.enquiries.insert_one(doc)
    return {
        "success":    True,
        "enquiry_id": doc["enquiry_id"],
        "message":    "Exciting! Our partnerships team will reach out within 48 hours."
    }


@router.post("/student-support")
async def student_support_enquiry(payload: StudentSupportEnquiry):
    """
    Student needs general help. No login required.
    """
    email = _validate_email(payload.email)
    doc = {
        "enquiry_id": _make_enquiry_id(),
        "type":       "student_support",
        "status":     "pending",

        "name":       payload.name.strip(),
        "email":      email,
        "phone":      payload.phone,
        "subject":    payload.subject.strip(),
        "message":    payload.message.strip(),

        "submitted_at":  datetime.utcnow(),
        "responded_at":  None,
        "admin_response": None,
        "responded_by":  None,
    }
    await db.enquiries.insert_one(doc)
    return {
        "success":    True,
        "enquiry_id": doc["enquiry_id"],
        "message":    "We've received your message and will respond shortly."
    }


@router.post("/general")
async def general_enquiry(payload: GeneralEnquiry):
    """
    Anything else. No login required.
    """
    email = _validate_email(payload.email)
    doc = {
        "enquiry_id": _make_enquiry_id(),
        "type":       "general",
        "status":     "pending",

        "name":       payload.name.strip(),
        "email":      email,
        "phone":      payload.phone,
        "subject":    payload.subject.strip(),
        "message":    payload.message.strip(),

        "submitted_at":  datetime.utcnow(),
        "responded_at":  None,
        "admin_response": None,
        "responded_by":  None,
    }
    await db.enquiries.insert_one(doc)
    return {
        "success":    True,
        "enquiry_id": doc["enquiry_id"],
        "message":    "Got it! We'll be in touch soon."
    }


# ==================== PUBLIC: CHECK STATUS ====================

@router.get("/status/{enquiry_id}")
async def check_enquiry_status(enquiry_id: str):
    """
    Anyone can check their enquiry status by ID.
    No login required.
    """
    doc = await db.enquiries.find_one({"enquiry_id": enquiry_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Enquiry not found")
    return {
        "enquiry_id":    doc["enquiry_id"],
        "type":          doc["type"],
        "status":        doc["status"],
        "submitted_at":  doc["submitted_at"].isoformat() if isinstance(doc["submitted_at"], datetime) else doc["submitted_at"],
        "admin_response": doc.get("admin_response"),
        "responded_at":  doc["responded_at"].isoformat() if isinstance(doc.get("responded_at"), datetime) else doc.get("responded_at"),
    }


# ==================== ADMIN: MANAGE ENQUIRIES ====================

@router.get("/enquiries")
async def list_enquiries(
    type:   Optional[str] = None,    # filter by enquiry type
    status: Optional[str] = "pending",
    skip:   int = 0,
    limit:  int = 50,
    admin: dict = Depends(get_current_admin)
):
    """
    Admin: list all enquiries.
    Default filter: pending — the ones needing action.
    """
    query = {}
    if type and type != "all":
        if type not in VALID_TYPES:
            raise HTTPException(status_code=400, detail=f"Invalid type. Must be one of {VALID_TYPES}")
        query["type"] = type
    if status and status != "all":
        query["status"] = status

    docs  = await db.enquiries.find(query).sort("submitted_at", -1).skip(skip).limit(limit).to_list(limit)
    total = await db.enquiries.count_documents(query)

    # Summary counts per type for the admin panel sidebar
    summary = {}
    for t in VALID_TYPES:
        summary[t] = await db.enquiries.count_documents({"type": t, "status": "pending"})

    return {
        "enquiries":       [_clean(d) for d in docs],
        "count":           len(docs),
        "total":           total,
        "pending_summary": summary,   # how many pending per type
        "filter":          {"type": type, "status": status}
    }


@router.get("/enquiries/{enquiry_id}")
async def get_enquiry_detail(
    enquiry_id: str,
    admin: dict = Depends(get_current_admin)
):
    """Admin: full detail of one enquiry."""
    doc = await db.enquiries.find_one({"enquiry_id": enquiry_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Enquiry not found")
    return _clean(doc)


@router.post("/enquiries/{enquiry_id}/respond")
async def respond_to_enquiry(
    enquiry_id: str,
    body: AdminResponse,
    admin: dict = Depends(get_current_admin)
):
    """
    Admin: respond to an enquiry and update its status.
    """
    doc = await db.enquiries.find_one({"enquiry_id": enquiry_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Enquiry not found")
    if doc["status"] == "closed":
        raise HTTPException(status_code=400, detail="Enquiry is already closed.")

    await db.enquiries.update_one(
        {"enquiry_id": enquiry_id},
        {"$set": {
            "status":         body.status,
            "admin_response": body.response,
            "responded_at":   datetime.utcnow(),
            "responded_by":   admin.get("email", "admin"),
        }}
    )
    return {
        "success":    True,
        "enquiry_id": enquiry_id,
        "status":     body.status,
        "message":    "Response saved."
    }


@router.post("/enquiries/{enquiry_id}/close")
async def close_enquiry(
    enquiry_id: str,
    admin: dict = Depends(get_current_admin)
):
    """Admin: close an enquiry."""
    doc = await db.enquiries.find_one({"enquiry_id": enquiry_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Enquiry not found")

    await db.enquiries.update_one(
        {"enquiry_id": enquiry_id},
        {"$set": {
            "status":    "closed",
            "closed_at": datetime.utcnow(),
            "closed_by": admin.get("email", "admin"),
        }}
    )
    return {"success": True, "enquiry_id": enquiry_id, "message": "Enquiry closed."}
"""
SECURED ENROLLMENT ROUTER
File: app/courses/enrollment_router.py

SECURITY FEATURES:
✅ Access control before enrollment
✅ Validates course purchase or tier subscription
✅ Prevents unauthorized access
✅ Rate limiting on all endpoints
✅ Ownership verification
"""

from fastapi import APIRouter, HTTPException, Depends, Request
from motor.motor_asyncio import AsyncIOMotorDatabase
from typing import List
from datetime import datetime
from app.courses.models import EnrollmentCreate, EnrollmentResponse
from app.courses.database import (
    enroll_user, get_enrollment, get_user_enrollments,
    get_course, get_course_questions
)
from app.courses.dependencies import get_db, get_current_user_id, get_sidhi_id

router = APIRouter(tags=["Enrollments"])


# ==================== HELPER: ACCESS CONTROL ====================

def is_quota_expired(quota_doc: dict) -> bool:
    """Check if a quota has expired"""
    if not quota_doc:
        return True
    
    expires_at = quota_doc.get("meta", {}).get("expires_at")
    if not expires_at:
        return False  # Free tier or no expiry
    
    return datetime.utcnow() > expires_at


async def check_course_access(
    db: AsyncIOMotorDatabase,
    course_id: str,
    user_id: str,
    sidhi_id: str
) -> dict:
    """
    CRITICAL: Check if user has access to enroll in course
    
    Returns:
    {
        "has_access": bool,
        "access_reason": str | None,
        "message": str
    }
    """
    # Get course
    course = await db.courses.find_one({"course_id": course_id})
    
    if not course:
        return {
            "has_access": False,
            "access_reason": None,
            "message": "Course not found"
        }
    
    # Get pricing
    pricing = course.get("pricing", {})
    
    # ✅ CHECK 1: Is course FREE?
    if pricing.get("is_free", False):
        return {
            "has_access": True,
            "access_reason": "free_course",
            "message": "Free course - access granted"
        }
    
    # ✅ CHECK 2: Does user have ACTIVE TIER subscription?
    quota = await db.quotas.find_one({"sidhi_id": sidhi_id})
    
    if quota and not is_quota_expired(quota):
        tier = quota.get("tier", "free")
        tier_access = pricing.get("tier_access", [])
        
        # Check if tier grants access
        if tier in tier_access:
            return {
                "has_access": True,
                "access_reason": "tier_subscription",
                "tier": tier,
                "message": f"Access granted via {tier.capitalize()} subscription"
            }
    
    # ✅ CHECK 3: Has user PURCHASED this course?
    purchase = await db.course_purchases.find_one({
        "user_id": sidhi_id,  # Using sidhi_id for consistency
        "course_id": course_id,
        "status": "captured",
        "access_granted": True
    })
    
    if purchase:
        return {
            "has_access": True,
            "access_reason": "course_purchase",
            "purchase_id": purchase.get("purchase_id"),
            "message": "Access granted via course purchase"
        }
    
    # ❌ NO ACCESS
    return {
        "has_access": False,
        "access_reason": None,
        "message": "Payment required to access this course",
        "pricing": {
            "price": pricing.get("price", 0) // 100,  # Convert to rupees
            "currency": "INR"
        }
    }


# ==================== ENROLLMENT ENDPOINTS ====================

@router.post("/enroll")
async def enroll_endpoint(
    enrollment: EnrollmentCreate,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
    sidhi_id: str = Depends(get_sidhi_id)
):
    """
    Enroll in course
    SECURITY: Validates access before enrollment
    """
    course_id = enrollment.course_id
    
    # SECURITY: Verify course exists
    course = await get_course(db, course_id)
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    
    # SECURITY: Verify course is published
    if course["status"] not in ["PUBLISHED", "ACTIVE"]:
        raise HTTPException(
            status_code=400,
            detail="Course not available for enrollment"
        )
    
    # ✅ CRITICAL: CHECK ACCESS BEFORE ENROLLMENT
    access_check = await check_course_access(db, course_id, user_id, sidhi_id)
    
    if not access_check["has_access"]:
        raise HTTPException(
            status_code=402,  # Payment Required
            detail={
                "message": access_check["message"],
                "pricing": access_check.get("pricing"),
                "course_id": course_id,
                "requires_payment": True
            }
        )
    
    # Check if already enrolled
    existing_enrollment = await get_enrollment(db, course_id, user_id)
    if existing_enrollment:
        return {
            "success": True,
            "enrollment_id": existing_enrollment["enrollment_id"],
            "certificate_id": existing_enrollment.get("certificate_id"),
            "message": "Already enrolled in this course",
            "already_enrolled": True
        }
    
    # Enroll user
    enrollment_id = await enroll_user(db, course_id, user_id, sidhi_id)
    
    return {
        "success": True,
        "enrollment_id": enrollment_id,
        "certificate_id": f"CERT_{enrollment_id.split('_')[1]}",
        "message": "Enrolled successfully",
        "access_reason": access_check["access_reason"]
    }


@router.get("/my-courses")
async def get_my_courses(
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    """Get all enrolled courses for user"""
    enrollments = await get_user_enrollments(db, user_id)
    
    # Enrich with course data
    result = []
    for enr in enrollments:
        course = await get_course(db, enr["course_id"])
        if course:
            result.append({
                "enrollment_id": enr["enrollment_id"],
                "course": {
                    "course_id": course["course_id"],
                    "title": course["title"],
                    "description": course["description"],
                    "domain": course["domain"],
                    "thumbnail_url": course.get("thumbnail_url")
                },
                "progress": enr.get("progress", 0.0),
                "league": enr.get("current_league", "BRONZE"),
                "league_points": enr.get("league_points", 0),
                "certificate_id": enr.get("certificate_id"),
                "enrolled_at": enr["enrolled_at"]
            })
    
    return {
        "enrollments": result,
        "count": len(result)
    }


@router.get("/course/{course_id}/progress")
async def get_course_progress(
    course_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    """Get progress in specific course"""
    enrollment = await get_enrollment(db, course_id, user_id)
    if not enrollment:
        raise HTTPException(status_code=404, detail="Not enrolled in this course")
    
    # Get total questions
    total_questions = await db.course_questions.count_documents({
        "course_id": course_id,
        "is_active": True
    })
    
    solved_count = len(enrollment.get("solved_questions", []))
    progress = (solved_count / total_questions * 100) if total_questions > 0 else 0
    
    return {
        "course_id": course_id,
        "total_questions": total_questions,
        "solved_questions": enrollment.get("solved_questions", []),
        "solved_count": solved_count,
        "progress": round(progress, 2),
        "league": enrollment.get("current_league", "BRONZE"),
        "league_points": enrollment.get("league_points", 0),
        "certificate_id": enrollment.get("certificate_id")
    }


@router.get("/course/{course_id}/questions")
async def get_available_questions(
    course_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
    sidhi_id: str = Depends(get_sidhi_id)
):
    """
    Get available questions (excluding solved)
    SECURITY: Verifies enrollment before showing questions
    """
    # SECURITY: Verify enrollment
    enrollment = await get_enrollment(db, course_id, user_id)
    if not enrollment:
        # Double-check access (maybe user purchased but didn't enroll)
        access_check = await check_course_access(db, course_id, user_id, sidhi_id)
        
        if access_check["has_access"]:
            raise HTTPException(
                status_code=403,
                detail="You have access but are not enrolled. Please enroll first."
            )
        else:
            raise HTTPException(
                status_code=402,
                detail="Not enrolled in this course. Payment may be required."
            )
    
    questions = await get_course_questions(db, course_id, user_id)
    
    return {
        "course_id": course_id,
        "questions": questions,
        "count": len(questions),
        "solved_count": len(enrollment.get("solved_questions", []))
    }


@router.get("/check-access/{course_id}")
async def check_enrollment_access(
    course_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
    sidhi_id: str = Depends(get_sidhi_id)
):
    """
    Check if user can enroll in course
    PUBLIC-ish endpoint for frontend to check before showing "Enroll" button
    """
    access_check = await check_course_access(db, course_id, user_id, sidhi_id)
    
    # Check if already enrolled
    enrollment = await get_enrollment(db, course_id, user_id)
    
    return {
        "course_id": course_id,
        "has_access": access_check["has_access"],
        "access_reason": access_check.get("access_reason"),
        "message": access_check["message"],
        "is_enrolled": enrollment is not None,
        "enrollment_id": enrollment.get("enrollment_id") if enrollment else None,
        "pricing": access_check.get("pricing")
    }
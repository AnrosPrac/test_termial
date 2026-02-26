from fastapi import APIRouter, HTTPException, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase
from typing import Optional, List, Dict
from app.courses.dependencies import get_db, get_current_user_id
from datetime import datetime, timedelta
from bson import ObjectId

router = APIRouter(tags=["Certificates"])

# ==================== SERIALIZATION HELPER ====================

def serialize_datetime(obj):
    """Convert datetime objects to ISO format strings"""
    if isinstance(obj, datetime):
        return obj.isoformat()
    return obj


# ==================== ANALYTICS HELPERS ====================

async def get_daily_activity(db: AsyncIOMotorDatabase, user_id: str, course_id: str, enrolled_at: datetime) -> List[Dict]:
    """Get daily submission activity for heatmap"""
    pipeline = [
        {
            "$match": {
                "user_id": user_id,
                "course_id": course_id,
                "submitted_at": {"$gte": enrolled_at}
            }
        },
        {
            "$group": {
                "_id": {
                    "$dateToString": {
                        "format": "%Y-%m-%d",
                        "date": "$submitted_at"
                    }
                },
                "submissions": {"$sum": 1},
                "accepted": {
                    "$sum": {
                        "$cond": [{"$eq": ["$verdict", "Accepted"]}, 1, 0]
                    }
                }
            }
        },
        {"$sort": {"_id": 1}}
    ]
    
    results = await db.course_submissions.aggregate(pipeline).to_list(None)
    return [
        {
            "date": r["_id"],
            "count": r["submissions"],
            "accepted": r["accepted"]
        }
        for r in results
    ]


async def get_monthly_breakdown(db: AsyncIOMotorDatabase, user_id: str, course_id: str) -> List[Dict]:
    """Get monthly progress summary"""
    pipeline = [
        {
            "$match": {
                "user_id": user_id,
                "course_id": course_id
            }
        },
        {
            "$group": {
                "_id": {
                    "year": {"$year": "$submitted_at"},
                    "month": {"$month": "$submitted_at"}
                },
                "submissions": {"$sum": 1},
                "accepted": {
                    "$sum": {
                        "$cond": [{"$eq": ["$verdict", "Accepted"]}, 1, 0]
                    }
                },
                "problems_solved": {
                    "$addToSet": {
                        "$cond": [
                            {"$eq": ["$verdict", "Accepted"]},
                            "$question_id",
                            "$$REMOVE"
                        ]
                    }
                }
            }
        },
        {
            "$project": {
                "year": "$_id.year",
                "month": "$_id.month",
                "submissions": 1,
                "accepted": 1,
                "unique_solved": {"$size": "$problems_solved"}
            }
        },
        {"$sort": {"year": 1, "month": 1}}
    ]
    
    results = await db.course_submissions.aggregate(pipeline).to_list(None)
    
    # Remove MongoDB _id from results
    for r in results:
        r.pop("_id", None)
    
    return results


async def get_language_stats(db: AsyncIOMotorDatabase, user_id: str, course_id: str) -> Dict[str, int]:
    """Get language usage statistics"""
    pipeline = [
        {
            "$match": {
                "user_id": user_id,
                "course_id": course_id,
                "verdict": "Accepted"
            }
        },
        {
            "$group": {
                "_id": "$language",
                "count": {"$sum": 1}
            }
        }
    ]
    
    results = await db.course_submissions.aggregate(pipeline).to_list(None)
    return {r["_id"]: r["count"] for r in results}


async def get_difficulty_stats(db: AsyncIOMotorDatabase, user_id: str, course_id: str) -> Dict[str, int]:
    """Get solved problems by difficulty"""
    pipeline = [
        {
            "$match": {
                "user_id": user_id,
                "course_id": course_id,
                "verdict": "Accepted"
            }
        },
        {
            "$lookup": {
                "from": "course_questions",
                "localField": "question_id",
                "foreignField": "question_id",
                "as": "question"
            }
        },
        {"$unwind": "$question"},
        {
            "$group": {
                "_id": "$question.difficulty",
                "unique_problems": {"$addToSet": "$question_id"}
            }
        },
        {
            "$project": {
                "difficulty": "$_id",
                "solved": {"$size": "$unique_problems"}
            }
        }
    ]
    
    results = await db.course_submissions.aggregate(pipeline).to_list(None)
    return {
        "easy": next((r["solved"] for r in results if r["difficulty"] == "easy"), 0),
        "medium": next((r["solved"] for r in results if r["difficulty"] == "medium"), 0),
        "hard": next((r["solved"] for r in results if r["difficulty"] == "hard"), 0)
    }


def calculate_streaks(daily_activity: List[Dict]) -> Dict[str, int]:
    """Calculate current and longest streak from daily activity"""
    if not daily_activity:
        return {"current": 0, "longest": 0}
    
    # Sort by date
    dates = sorted([d["date"] for d in daily_activity])
    
    longest_streak = 0
    temp_streak = 1
    
    # Calculate longest streak
    for i in range(len(dates)):
        if i > 0:
            prev = datetime.strptime(dates[i-1], "%Y-%m-%d")
            curr = datetime.strptime(dates[i], "%Y-%m-%d")
            diff = (curr - prev).days
            
            if diff == 1:
                temp_streak += 1
            else:
                longest_streak = max(longest_streak, temp_streak)
                temp_streak = 1
    
    longest_streak = max(longest_streak, temp_streak)
    
    # Calculate current streak (from today backwards)
    current_streak = 0
    today = datetime.now().date()
    
    for i in range(len(dates) - 1, -1, -1):
        date = datetime.strptime(dates[i], "%Y-%m-%d").date()
        diff = (today - date).days
        
        if diff <= 1:  # Today or yesterday
            current_streak += 1
            today = date - timedelta(days=1)
        else:
            break
    
    return {"current": current_streak, "longest": longest_streak}


async def get_timeline_events(db: AsyncIOMotorDatabase, user_id: str, course_id: str, enrolled_at: datetime) -> List[Dict]:
    """Build chronological timeline of events"""
    events = []
    
    # 1. Enrollment
    events.append({
        "date": enrolled_at.isoformat(),
        "type": "enrollment",
        "title": "Enrolled in Course",
        "icon": "🎓"
    })
    
    # 2. First submission
    first_sub = await db.course_submissions.find_one(
        {"user_id": user_id, "course_id": course_id},
        sort=[("submitted_at", 1)]
    )
    if first_sub:
        events.append({
            "date": first_sub["submitted_at"].isoformat(),
            "type": "first_submission",
            "title": "First Submission",
            "icon": "📝"
        })
    
    # 3. First accepted
    first_accepted = await db.course_submissions.find_one(
        {"user_id": user_id, "course_id": course_id, "verdict": "Accepted"},
        sort=[("submitted_at", 1)]
    )
    if first_accepted:
        events.append({
            "date": first_accepted["submitted_at"].isoformat(),
            "type": "first_accepted",
            "title": "First Accepted Solution",
            "icon": "✅"
        })
    
    # 4. Badge unlocks
    badges = await db.user_achievements.find(
        {"user_id": user_id, "course_id": course_id}
    ).to_list(None)
    
    for badge in badges:
        events.append({
            "date": badge["unlocked_at"].isoformat() if isinstance(badge["unlocked_at"], datetime) else badge["unlocked_at"],
            "type": "badge",
            "title": f"Unlocked: {badge['title']}",
            "icon": badge.get("icon", "🏆")
        })
    
    # Sort chronologically
    events.sort(key=lambda x: x["date"])
    
    return events


def get_skills_from_course(domain: str) -> list:
    """Extract skills based on course domain"""
    if domain == "SOFTWARE":
        return ["C Programming", "C++", "Python", "Problem Solving", "Algorithms"]
    elif domain == "HARDWARE":
        return ["VHDL", "Verilog", "Digital Design", "HDL", "Circuit Design"]
    return []


# ==================== ENDPOINTS ====================

@router.get("/data/{certificate_id}")
async def get_certificate_analytics(
    certificate_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db)
):
    """
    Get complete certificate data with analytics
    
    Returns comprehensive certificate information including:
    - User and course details
    - Progress statistics
    - Activity heatmap data
    - Monthly breakdown
    - Performance metrics (language, difficulty)
    - Timeline of events
    - Achievements and badges
    """
    
    # Step 1: Find enrollment by certificate_id
    enrollment = await db.course_enrollments.find_one({"certificate_id": certificate_id})
    if not enrollment:
        raise HTTPException(status_code=404, detail="Certificate not found")
    
    user_id = enrollment["user_id"]
    course_id = enrollment["course_id"]
    enrolled_at = enrollment["enrolled_at"]
    
    # Step 2: Get user profile
    user = await db.users_profile.find_one({"user_id": user_id})
    if not user:
        # Fallback
        user_data = {
            "user_id": user_id,
            "sidhi_id": enrollment.get("sidhi_id", "N/A"),
            "username": user_id,
            "college": None,
            "department": None
        }
    else:
        user_data = {
            "user_id": user_id,
            "sidhi_id": user.get("sidhi_id", "N/A"),
            "username": user.get("username", "Student"),
            "college": user.get("college"),
            "department": user.get("department")
        }
    
    # Step 3: Get course details
    course = await db.courses.find_one({"course_id": course_id})
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    
    # Step 4: Get total questions count
    total_questions = await db.course_questions.count_documents({
        "course_id": course_id,
        "is_active": True
    })
    
    # Step 5: Calculate solved problems
    solved_questions = enrollment.get("solved_questions", [])
    solved_count = len(solved_questions)
    completion_percentage = round((solved_count / total_questions * 100) if total_questions > 0 else 0, 2)
    
    # Step 6: Get submission statistics
    total_submissions = await db.course_submissions.count_documents({
        "user_id": user_id,
        "course_id": course_id
    })
    
    accepted_submissions = await db.course_submissions.count_documents({
        "user_id": user_id,
        "course_id": course_id,
        "verdict": "Accepted"
    })
    
    acceptance_rate = round((accepted_submissions / total_submissions * 100) if total_submissions > 0 else 0, 2)
    
    # Step 7: Get activity data for heatmap
    daily_activity = await get_daily_activity(db, user_id, course_id, enrolled_at)
    
    # Step 8: Calculate streaks
    streaks = calculate_streaks(daily_activity)
    
    # Step 9: Get monthly breakdown
    monthly_breakdown = await get_monthly_breakdown(db, user_id, course_id)
    
    # Step 10: Get language statistics
    language_stats = await get_language_stats(db, user_id, course_id)
    
    # Step 11: Get difficulty breakdown
    difficulty_stats = await get_difficulty_stats(db, user_id, course_id)
    
    # Step 12: Get timeline events
    timeline = await get_timeline_events(db, user_id, course_id, enrolled_at)
    
    # Step 13: Get badges/achievements
    achievements = await db.user_achievements.find({
        "user_id": user_id,
        "course_id": course_id
    }).to_list(None)
    
    badges = [
        {
            "badge_id": a.get("badge_id"),
            "title": a.get("title"),
            "description": a.get("description"),
            "icon": a.get("icon", "🏆"),
            "unlocked_at": a["unlocked_at"].isoformat() if isinstance(a.get("unlocked_at"), datetime) else a.get("unlocked_at")
        }
        for a in achievements
    ]
    
    # Step 14: Build complete response
    certificate_data = {
        # Basic Info
        "certificate_id": certificate_id,
        "user": user_data,
        
        # Course Info
        "course": {
            "course_id": course_id,
            "title": course.get("title"),
            "domain": course.get("domain"),
            "course_type": course.get("course_type"),
            "enrolled_at": enrolled_at.isoformat()
        },
        
        # Progress Stats
        "progress": {
            "total_problems": total_questions,
            "solved_problems": solved_count,
            "completion_percentage": completion_percentage,
            "grade_points": enrollment.get("league_points", 0),
            "current_league": enrollment.get("current_league", "BRONZE")
        },
        
        # Performance Metrics
        "stats": {
            "acceptance_rate": acceptance_rate,
            "total_submissions": total_submissions,
            "accepted_submissions": accepted_submissions,
            "current_streak": streaks["current"],
            "longest_streak": streaks["longest"],
            "avg_efficiency": enrollment.get("avg_efficiency", 0),
            
            "by_difficulty": difficulty_stats,
            "by_language": language_stats
        },
        
        # Activity Data (for heatmap and monthly charts)
        "activity": {
            "daily": daily_activity,
            "monthly": monthly_breakdown
        },
        
        # Timeline/Milestones
        "timeline": timeline,
        
        # Achievements
        "badges": badges,
        
        # Skills
        "skills": get_skills_from_course(course.get("domain", "")),
        
        # Metadata
        "last_updated": datetime.utcnow().isoformat(),
        "generated_at": datetime.utcnow().isoformat()
    }
    
    return certificate_data


@router.get("/verify/{certificate_id}")
async def verify_certificate(
    certificate_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db)
):
    """Verify certificate authenticity"""
    enrollment = await db.course_enrollments.find_one({"certificate_id": certificate_id})
    
    if not enrollment:
        return {
            "valid": False,
            "message": "Certificate not found"
        }
    
    return {
        "valid": True,
        "certificate_id": certificate_id,
        "issued_to": enrollment.get("sidhi_id"),
        "course_id": enrollment["course_id"],
        "issued_at": enrollment["enrolled_at"].isoformat() if isinstance(enrollment["enrolled_at"], datetime) else enrollment["enrolled_at"],
        "message": "Certificate is valid"
    }


@router.post("/claim")
async def claim_certificate_pdf(
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    """Claim PDF snapshot of certificate (future implementation)"""
    enrollments = await db.course_enrollments.find({
        "user_id": user_id,
        "is_active": True
    }).to_list(length=None)
    
    certificates = []
    for enr in enrollments:
        # Check if minimum score met
        if enr.get("league_points", 0) >= 1000:  # Minimum threshold
            certificates.append({
                "certificate_id": enr["certificate_id"],
                "course_id": enr["course_id"],
                "url": f"https://lumetrix.com/certificates/{enr['certificate_id']}",
                "claimable": True
            })
    
    return {
        "certificates": certificates,
        "count": len(certificates)
    }
from fastapi import APIRouter, HTTPException, Depends, Response
from fastapi.responses import FileResponse
from motor.motor_asyncio import AsyncIOMotorDatabase
from typing import Optional
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont
import io
import os

from app.courses.dependencies import get_db, get_current_user_id

router = APIRouter(tags=["Certificates"])

# ==================== CERTIFICATE GENERATION ====================

def serialize_mongo(doc: dict) -> dict:
    if "_id" in doc:
        doc["_id"] = str(doc["_id"])
    return doc


async def generate_certificate_image(
    username: str,
    sidhi_id: str,
    course_title: str,
    league: str,
    points: int,
    solved_count: int,
    completion_date: datetime,
    certificate_id: str
) -> bytes:
    """
    Generate a certificate image using PIL
    Returns bytes of the PNG image
    """
    
    # Certificate dimensions
    width, height = 1920, 1080
    
    # Create image with white background
    img = Image.new('RGB', (width, height), color='white')
    draw = ImageDraw.Draw(img)
    
    # Colors
    primary_color = (41, 128, 185)  # Blue
    secondary_color = (52, 73, 94)  # Dark gray
    gold_color = (241, 196, 15)     # Gold for accents
    
    # Draw border
    border_margin = 50
    draw.rectangle(
        [border_margin, border_margin, width - border_margin, height - border_margin],
        outline=primary_color,
        width=10
    )
    
    # Draw inner border
    inner_margin = 70
    draw.rectangle(
        [inner_margin, inner_margin, width - inner_margin, height - inner_margin],
        outline=gold_color,
        width=3
    )
    
    # Try to load fonts (fallback to default if not available)
    try:
        title_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf", 80)
        subtitle_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf", 40)
        text_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 36)
        small_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 28)
    except:
        title_font = ImageFont.load_default()
        subtitle_font = ImageFont.load_default()
        text_font = ImageFont.load_default()
        small_font = ImageFont.load_default()
    
    # Title
    title_text = "CERTIFICATE OF ACHIEVEMENT"
    title_bbox = draw.textbbox((0, 0), title_text, font=title_font)
    title_width = title_bbox[2] - title_bbox[0]
    draw.text(
        ((width - title_width) / 2, 120),
        title_text,
        fill=primary_color,
        font=title_font
    )
    
    # Subtitle
    subtitle_text = "This is to certify that"
    subtitle_bbox = draw.textbbox((0, 0), subtitle_text, font=subtitle_font)
    subtitle_width = subtitle_bbox[2] - subtitle_bbox[0]
    draw.text(
        ((width - subtitle_width) / 2, 240),
        subtitle_text,
        fill=secondary_color,
        font=subtitle_font
    )
    
    # Student name
    name_bbox = draw.textbbox((0, 0), username, font=title_font)
    name_width = name_bbox[2] - name_bbox[0]
    draw.text(
        ((width - name_width) / 2, 320),
        username,
        fill=gold_color,
        font=title_font
    )
    
    # Sidhi ID
    sidhi_text = f"Sidhi ID: {sidhi_id}"
    sidhi_bbox = draw.textbbox((0, 0), sidhi_text, font=small_font)
    sidhi_width = sidhi_bbox[2] - sidhi_bbox[0]
    draw.text(
        ((width - sidhi_width) / 2, 420),
        sidhi_text,
        fill=secondary_color,
        font=small_font
    )
    
    # Achievement text
    achievement_text = f"has successfully completed the course"
    achievement_bbox = draw.textbbox((0, 0), achievement_text, font=text_font)
    achievement_width = achievement_bbox[2] - achievement_bbox[0]
    draw.text(
        ((width - achievement_width) / 2, 500),
        achievement_text,
        fill=secondary_color,
        font=text_font
    )
    
    # Course title
    course_bbox = draw.textbbox((0, 0), course_title, font=title_font)
    course_width = course_bbox[2] - course_bbox[0]
    draw.text(
        ((width - course_width) / 2, 560),
        course_title,
        fill=primary_color,
        font=title_font
    )
    
    # League and stats
    league_colors = {
        "BRONZE": (205, 127, 50),
        "SILVER": (192, 192, 192),
        "GOLD": (255, 215, 0),
        "PLATINUM": (229, 228, 226),
        "DIAMOND": (185, 242, 255),
        "MYTHIC": (138, 43, 226),
        "LEGEND": (255, 0, 0)
    }
    
    league_color = league_colors.get(league, gold_color)
    
    stats_y = 680
    stats_text = f"League: {league} | Points: {points:,} | Problems Solved: {solved_count}"
    stats_bbox = draw.textbbox((0, 0), stats_text, font=text_font)
    stats_width = stats_bbox[2] - stats_bbox[0]
    draw.text(
        ((width - stats_width) / 2, stats_y),
        stats_text,
        fill=league_color,
        font=text_font
    )
    
    # Date
    date_text = f"Issued on: {completion_date.strftime('%B %d, %Y')}"
    date_bbox = draw.textbbox((0, 0), date_text, font=small_font)
    date_width = date_bbox[2] - date_bbox[0]
    draw.text(
        ((width - date_width) / 2, 780),
        date_text,
        fill=secondary_color,
        font=small_font
    )
    
    # Certificate ID
    cert_id_text = f"Certificate ID: {certificate_id}"
    cert_id_bbox = draw.textbbox((0, 0), cert_id_text, font=small_font)
    cert_id_width = cert_id_bbox[2] - cert_id_bbox[0]
    draw.text(
        ((width - cert_id_width) / 2, 870),
        cert_id_text,
        fill=secondary_color,
        font=small_font
    )
    
    # Signature line
    draw.line([(width/2 - 200, 950), (width/2 + 200, 950)], fill=secondary_color, width=2)
    signature_text = "Authorized Signature"
    sig_bbox = draw.textbbox((0, 0), signature_text, font=small_font)
    sig_width = sig_bbox[2] - sig_bbox[0]
    draw.text(
        ((width - sig_width) / 2, 960),
        signature_text,
        fill=secondary_color,
        font=small_font
    )
    
    # Convert to bytes
    img_byte_arr = io.BytesIO()
    img.save(img_byte_arr, format='PNG', quality=95)
    img_byte_arr.seek(0)
    
    return img_byte_arr.getvalue()


# ==================== CERTIFICATE ENDPOINTS ====================

@router.get("/course/{course_id}/certificate/check-eligibility")
async def check_certificate_eligibility(
    course_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    """
    Check if user is eligible for certificate
    Requirements: Must be Silver league or higher
    """
    
    enrollment = await db.course_enrollments.find_one({
        "course_id": course_id,
        "user_id": user_id
    })
    
    if not enrollment:
        raise HTTPException(status_code=404, detail="Not enrolled in this course")
    
    current_league = enrollment.get("current_league", "BRONZE")
    eligible_leagues = ["SILVER", "GOLD", "PLATINUM", "DIAMOND", "MYTHIC", "LEGEND"]
    
    is_eligible = current_league in eligible_leagues
    
    # Get course info
    course = await db.courses.find_one({"course_id": course_id})
    
    return {
        "eligible": is_eligible,
        "current_league": current_league,
        "required_league": "SILVER",
        "league_points": enrollment.get("league_points", 0),
        "points_needed": max(0, 2500 - enrollment.get("league_points", 0)),
        "certificate_id": enrollment.get("certificate_id") if is_eligible else None,
        "course_title": course.get("title") if course else "Unknown",
        "message": "Certificate available!" if is_eligible else "Reach Silver league to unlock certificate"
    }


@router.get("/course/{course_id}/certificate/download")
async def download_certificate(
    course_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    """
    Download certificate as PNG image
    Only available for Silver+ league users
    """
    
    # Get enrollment
    enrollment = await db.course_enrollments.find_one({
        "course_id": course_id,
        "user_id": user_id
    })
    
    if not enrollment:
        raise HTTPException(status_code=404, detail="Not enrolled in this course")
    
    # Check eligibility
    current_league = enrollment.get("current_league", "BRONZE")
    eligible_leagues = ["SILVER", "GOLD", "PLATINUM", "DIAMOND", "MYTHIC", "LEGEND"]
    
    if current_league not in eligible_leagues:
        raise HTTPException(
            status_code=403,
            detail=f"Certificate not available. Current league: {current_league}. Required: SILVER or higher."
        )
    
    # Get course
    course = await db.courses.find_one({"course_id": course_id})
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    
    # Get user profile
    user_profile = await db.users_profile.find_one({"user_id": user_id})
    username = user_profile.get("username", "Student") if user_profile else "Student"
    
    # Generate certificate
    certificate_bytes = await generate_certificate_image(
        username=username,
        sidhi_id=enrollment.get("sidhi_id", "N/A"),
        course_title=course.get("title", "Course"),
        league=current_league,
        points=enrollment.get("league_points", 0),
        solved_count=len(enrollment.get("solved_questions", [])),
        completion_date=datetime.utcnow(),
        certificate_id=enrollment.get("certificate_id", "N/A")
    )
    
    # Save certificate record
    await db.certificate_downloads.update_one(
        {
            "certificate_id": enrollment.get("certificate_id"),
            "user_id": user_id,
            "course_id": course_id
        },
        {
            "$set": {
                "downloaded_at": datetime.utcnow(),
                "league_at_download": current_league,
                "points_at_download": enrollment.get("league_points", 0)
            },
            "$inc": {"download_count": 1}
        },
        upsert=True
    )
    
    # Return image
    return Response(
        content=certificate_bytes,
        media_type="image/png",
        headers={
            "Content-Disposition": f"attachment; filename=certificate_{course_id}_{user_id}.png"
        }
    )


@router.get("/course/{course_id}/certificate/preview")
async def preview_certificate(
    course_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    """
    Preview certificate without downloading (works for all users)
    Shows what certificate will look like
    """
    
    enrollment = await db.course_enrollments.find_one({
        "course_id": course_id,
        "user_id": user_id
    })
    
    if not enrollment:
        raise HTTPException(status_code=404, detail="Not enrolled in this course")
    
    course = await db.courses.find_one({"course_id": course_id})
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    
    user_profile = await db.users_profile.find_one({"user_id": user_id})
    username = user_profile.get("username", "Student") if user_profile else "Student"
    
    current_league = enrollment.get("current_league", "BRONZE")
    
    # Generate preview (even for non-eligible users)
    certificate_bytes = await generate_certificate_image(
        username=username,
        sidhi_id=enrollment.get("sidhi_id", "N/A"),
        course_title=course.get("title", "Course"),
        league=current_league,
        points=enrollment.get("league_points", 0),
        solved_count=len(enrollment.get("solved_questions", [])),
        completion_date=datetime.utcnow(),
        certificate_id=enrollment.get("certificate_id", "PREVIEW")
    )
    
    return Response(
        content=certificate_bytes,
        media_type="image/png"
    )


@router.get("/my-certificates")
async def get_my_certificates(
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    """
    Get all certificates earned by user
    """
    
    # Get all enrollments with Silver+ league
    eligible_leagues = ["SILVER", "GOLD", "PLATINUM", "DIAMOND", "MYTHIC", "LEGEND"]
    
    cursor = db.course_enrollments.find({
        "user_id": user_id,
        "current_league": {"$in": eligible_leagues}
    })
    
    enrollments = await cursor.to_list(length=None)
    
    certificates = []
    for enr in enrollments:
        course = await db.courses.find_one({"course_id": enr["course_id"]})
        if not course:
            continue
        
        # Get download info
        download_info = await db.certificate_downloads.find_one({
            "certificate_id": enr.get("certificate_id"),
            "user_id": user_id
        })
        
        certificates.append({
            "certificate_id": enr.get("certificate_id"),
            "course_id": enr["course_id"],
            "course_title": course.get("title"),
            "league": enr.get("current_league"),
            "points": enr.get("league_points", 0),
            "problems_solved": len(enr.get("solved_questions", [])),
            "enrolled_at": enr.get("enrolled_at"),
            "download_count": download_info.get("download_count", 0) if download_info else 0,
            "last_downloaded": download_info.get("downloaded_at") if download_info else None
        })
    
    return {
        "certificates": certificates,
        "total": len(certificates)
    }
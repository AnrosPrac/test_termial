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

@router.get("/{certificate_id}/data")
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
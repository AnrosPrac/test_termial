from fastapi import APIRouter, HTTPException, Depends, Response
from motor.motor_asyncio import AsyncIOMotorDatabase
from typing import Optional, List, Dict
from app.courses.dependencies import get_db, get_current_user_id
from datetime import datetime, timedelta
from bson import ObjectId
from PIL import Image, ImageDraw, ImageFont
import io
import os

# ── ONE router only ──────────────────────────────────────────────────────────
router = APIRouter(tags=["Certificates"])

# ==================== SERIALIZATION HELPER ====================

def serialize_datetime(obj):
    if isinstance(obj, datetime):
        return obj.isoformat()
    return obj

def serialize_mongo(doc: dict) -> dict:
    if "_id" in doc:
        doc["_id"] = str(doc["_id"])
    return doc

# ==================== ANALYTICS HELPERS ====================

async def get_daily_activity(db, user_id, course_id, enrolled_at):
    pipeline = [
        {"$match": {"user_id": user_id, "course_id": course_id, "submitted_at": {"$gte": enrolled_at}}},
        {"$group": {
            "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$submitted_at"}},
            "submissions": {"$sum": 1},
            "accepted": {"$sum": {"$cond": [{"$eq": ["$verdict", "Accepted"]}, 1, 0]}}
        }},
        {"$sort": {"_id": 1}}
    ]
    results = await db.course_submissions.aggregate(pipeline).to_list(None)
    return [{"date": r["_id"], "count": r["submissions"], "accepted": r["accepted"]} for r in results]


async def get_monthly_breakdown(db, user_id, course_id):
    pipeline = [
        {"$match": {"user_id": user_id, "course_id": course_id}},
        {"$group": {
            "_id": {"year": {"$year": "$submitted_at"}, "month": {"$month": "$submitted_at"}},
            "submissions": {"$sum": 1},
            "accepted": {"$sum": {"$cond": [{"$eq": ["$verdict", "Accepted"]}, 1, 0]}},
            "problems_solved": {"$addToSet": {"$cond": [{"$eq": ["$verdict", "Accepted"]}, "$question_id", "$$REMOVE"]}}
        }},
        {"$project": {"year": "$_id.year", "month": "$_id.month", "submissions": 1, "accepted": 1, "unique_solved": {"$size": "$problems_solved"}}},
        {"$sort": {"year": 1, "month": 1}}
    ]
    results = await db.course_submissions.aggregate(pipeline).to_list(None)
    for r in results:
        r.pop("_id", None)
    return results


async def get_language_stats(db, user_id, course_id):
    pipeline = [
        {"$match": {"user_id": user_id, "course_id": course_id, "verdict": "Accepted"}},
        {"$group": {"_id": "$language", "count": {"$sum": 1}}}
    ]
    results = await db.course_submissions.aggregate(pipeline).to_list(None)
    return {r["_id"]: r["count"] for r in results}


async def get_difficulty_stats(db, user_id, course_id):
    pipeline = [
        {"$match": {"user_id": user_id, "course_id": course_id, "verdict": "Accepted"}},
        {"$lookup": {"from": "course_questions", "localField": "question_id", "foreignField": "question_id", "as": "question"}},
        {"$unwind": "$question"},
        {"$group": {"_id": "$question.difficulty", "unique_problems": {"$addToSet": "$question_id"}}},
        {"$project": {"difficulty": "$_id", "solved": {"$size": "$unique_problems"}}}
    ]
    results = await db.course_submissions.aggregate(pipeline).to_list(None)
    return {
        "easy":   next((r["solved"] for r in results if r.get("difficulty") == "easy"), 0),
        "medium": next((r["solved"] for r in results if r.get("difficulty") == "medium"), 0),
        "hard":   next((r["solved"] for r in results if r.get("difficulty") == "hard"), 0)
    }


def calculate_streaks(daily_activity):
    if not daily_activity:
        return {"current": 0, "longest": 0}
    dates = sorted([d["date"] for d in daily_activity])
    longest_streak = 0
    temp_streak = 1
    for i in range(1, len(dates)):
        prev = datetime.strptime(dates[i-1], "%Y-%m-%d")
        curr = datetime.strptime(dates[i], "%Y-%m-%d")
        if (curr - prev).days == 1:
            temp_streak += 1
        else:
            longest_streak = max(longest_streak, temp_streak)
            temp_streak = 1
    longest_streak = max(longest_streak, temp_streak)
    current_streak = 0
    today = datetime.now().date()
    for i in range(len(dates) - 1, -1, -1):
        date = datetime.strptime(dates[i], "%Y-%m-%d").date()
        if (today - date).days <= 1:
            current_streak += 1
            today = date - timedelta(days=1)
        else:
            break
    return {"current": current_streak, "longest": longest_streak}


async def get_timeline_events(db, user_id, course_id, enrolled_at):
    events = [{"date": enrolled_at.isoformat(), "type": "enrollment", "title": "Enrolled in Course", "icon": "🎓"}]
    first_sub = await db.course_submissions.find_one({"user_id": user_id, "course_id": course_id}, sort=[("submitted_at", 1)])
    if first_sub:
        events.append({"date": first_sub["submitted_at"].isoformat(), "type": "first_submission", "title": "First Submission", "icon": "📝"})
    first_accepted = await db.course_submissions.find_one({"user_id": user_id, "course_id": course_id, "verdict": "Accepted"}, sort=[("submitted_at", 1)])
    if first_accepted:
        events.append({"date": first_accepted["submitted_at"].isoformat(), "type": "first_accepted", "title": "First Accepted Solution", "icon": "✅"})
    badges = await db.user_achievements.find({"user_id": user_id, "course_id": course_id}).to_list(None)
    for badge in badges:
        events.append({"date": badge["unlocked_at"].isoformat() if isinstance(badge.get("unlocked_at"), datetime) else badge.get("unlocked_at"), "type": "badge", "title": f"Unlocked: {badge['title']}", "icon": badge.get("icon", "🏆")})
    events.sort(key=lambda x: x["date"])
    return events


def get_skills_from_course(domain):
    if domain == "SOFTWARE":
        return ["C Programming", "C++", "Python", "Problem Solving", "Algorithms"]
    elif domain == "HARDWARE":
        return ["VHDL", "Verilog", "Digital Design", "HDL", "Circuit Design"]
    return []


# ==================== CERTIFICATE IMAGE GENERATION ====================

async def generate_certificate_image(username, sidhi_id, course_title, league, points, solved_count, completion_date, certificate_id):
    width, height = 1920, 1080
    img = Image.new('RGB', (width, height), color='white')
    draw = ImageDraw.Draw(img)
    primary_color   = (41, 128, 185)
    secondary_color = (52, 73, 94)
    gold_color      = (241, 196, 15)
    draw.rectangle([50, 50, width-50, height-50], outline=primary_color, width=10)
    draw.rectangle([70, 70, width-70, height-70], outline=gold_color, width=3)
    try:
        title_font    = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf", 80)
        subtitle_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf", 40)
        text_font     = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 36)
        small_font    = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 28)
    except:
        title_font = subtitle_font = text_font = small_font = ImageFont.load_default()

    def centered(text, font, y, fill):
        bbox = draw.textbbox((0,0), text, font=font)
        draw.text(((width - (bbox[2]-bbox[0])) / 2, y), text, fill=fill, font=font)

    centered("CERTIFICATE OF ACHIEVEMENT", title_font, 120, primary_color)
    centered("This is to certify that", subtitle_font, 240, secondary_color)
    centered(username, title_font, 320, gold_color)
    centered(f"Sidhi ID: {sidhi_id}", small_font, 420, secondary_color)
    centered("has successfully completed the course", text_font, 500, secondary_color)
    centered(course_title, title_font, 560, primary_color)
    league_colors = {"BRONZE": (205,127,50), "SILVER": (150,150,150), "GOLD": (255,215,0), "PLATINUM": (180,180,200), "DIAMOND": (100,180,255), "MYTHIC": (138,43,226), "LEGEND": (255,0,0)}
    centered(f"League: {league}  |  Points: {points:,}  |  Problems Solved: {solved_count}", text_font, 680, league_colors.get(league, gold_color))
    centered(f"Issued on: {completion_date.strftime('%B %d, %Y')}", small_font, 780, secondary_color)
    centered(f"Certificate ID: {certificate_id}", small_font, 870, secondary_color)
    draw.line([(width//2-200, 950), (width//2+200, 950)], fill=secondary_color, width=2)
    centered("Authorized Signature", small_font, 960, secondary_color)

    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return buf.getvalue()


# ==================== ENDPOINTS ====================

@router.get("/data/{certificate_id}")
async def get_certificate_analytics(
    certificate_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db)
):
    enrollment = await db.course_enrollments.find_one({"certificate_id": certificate_id})
    if not enrollment:
        raise HTTPException(status_code=404, detail="Certificate not found")
    user_id    = enrollment["user_id"]
    course_id  = enrollment["course_id"]
    enrolled_at = enrollment["enrolled_at"]

    user = await db.users_profile.find_one({"user_id": user_id})
    user_data = {
        "user_id":    user_id,
        "sidhi_id":   (user or enrollment).get("sidhi_id", "N/A"),
        "username":   user.get("username", "Student") if user else user_id,
        "college":    user.get("college") if user else None,
        "department": user.get("department") if user else None
    }

    course = await db.courses.find_one({"course_id": course_id})
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")

    total_questions     = await db.course_questions.count_documents({"course_id": course_id, "is_active": True})
    solved_questions    = enrollment.get("solved_questions", [])
    solved_count        = len(solved_questions)
    completion_pct      = round((solved_count / total_questions * 100) if total_questions > 0 else 0, 2)
    total_submissions   = await db.course_submissions.count_documents({"user_id": user_id, "course_id": course_id})
    accepted_submissions = await db.course_submissions.count_documents({"user_id": user_id, "course_id": course_id, "verdict": "Accepted"})
    acceptance_rate     = round((accepted_submissions / total_submissions * 100) if total_submissions > 0 else 0, 2)

    daily_activity  = await get_daily_activity(db, user_id, course_id, enrolled_at)
    streaks         = calculate_streaks(daily_activity)
    monthly         = await get_monthly_breakdown(db, user_id, course_id)
    lang_stats      = await get_language_stats(db, user_id, course_id)
    diff_stats      = await get_difficulty_stats(db, user_id, course_id)
    timeline        = await get_timeline_events(db, user_id, course_id, enrolled_at)

    achievements = await db.user_achievements.find({"user_id": user_id, "course_id": course_id}).to_list(None)
    badges = [{"badge_id": a.get("badge_id"), "title": a.get("title"), "description": a.get("description"), "icon": a.get("icon", "🏆"), "unlocked_at": a["unlocked_at"].isoformat() if isinstance(a.get("unlocked_at"), datetime) else a.get("unlocked_at")} for a in achievements]

    return {
        "certificate_id": certificate_id,
        "user": user_data,
        "course": {"course_id": course_id, "title": course.get("title"), "domain": course.get("domain"), "course_type": course.get("course_type"), "enrolled_at": enrolled_at.isoformat()},
        "progress": {"total_problems": total_questions, "solved_problems": solved_count, "completion_percentage": completion_pct, "grade_points": enrollment.get("league_points", 0), "current_league": enrollment.get("current_league", "BRONZE")},
        "stats": {"acceptance_rate": acceptance_rate, "total_submissions": total_submissions, "accepted_submissions": accepted_submissions, "current_streak": streaks["current"], "longest_streak": streaks["longest"], "avg_efficiency": enrollment.get("avg_efficiency", 0), "by_difficulty": diff_stats, "by_language": lang_stats},
        "activity": {"daily": daily_activity, "monthly": monthly},
        "timeline": timeline,
        "badges": badges,
        "skills": get_skills_from_course(course.get("domain", "")),
        "last_updated": datetime.utcnow().isoformat(),
        "generated_at": datetime.utcnow().isoformat()
    }


@router.get("/verify/{certificate_id}")
async def verify_certificate(certificate_id: str, db: AsyncIOMotorDatabase = Depends(get_db)):
    enrollment = await db.course_enrollments.find_one({"certificate_id": certificate_id})
    if not enrollment:
        return {"valid": False, "message": "Certificate not found"}
    return {
        "valid": True,
        "certificate_id": certificate_id,
        "issued_to": enrollment.get("sidhi_id"),
        "course_id": enrollment["course_id"],
        "issued_at": enrollment["enrolled_at"].isoformat() if isinstance(enrollment["enrolled_at"], datetime) else enrollment["enrolled_at"],
        "message": "Certificate is valid"
    }


@router.post("/claim")
async def claim_certificate_pdf(db: AsyncIOMotorDatabase = Depends(get_db), user_id: str = Depends(get_current_user_id)):
    enrollments = await db.course_enrollments.find({"user_id": user_id, "is_active": True}).to_list(length=None)
    certificates = [
        {"certificate_id": enr["certificate_id"], "course_id": enr["course_id"], "url": f"https://lumetrix.com/certificates/{enr['certificate_id']}", "claimable": True}
        for enr in enrollments if enr.get("league_points", 0) >= 1000
    ]
    return {"certificates": certificates, "count": len(certificates)}


@router.get("/course/{course_id}/certificate/check-eligibility")
async def check_certificate_eligibility(course_id: str, db: AsyncIOMotorDatabase = Depends(get_db), user_id: str = Depends(get_current_user_id)):
    enrollment = await db.course_enrollments.find_one({"course_id": course_id, "user_id": user_id})
    if not enrollment:
        raise HTTPException(status_code=404, detail="Not enrolled in this course")
    current_league = enrollment.get("current_league", "BRONZE")
    eligible_leagues = ["SILVER", "GOLD", "PLATINUM", "DIAMOND", "MYTHIC", "LEGEND"]
    is_eligible = current_league in eligible_leagues
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
async def download_certificate(course_id: str, db: AsyncIOMotorDatabase = Depends(get_db), user_id: str = Depends(get_current_user_id)):
    enrollment = await db.course_enrollments.find_one({"course_id": course_id, "user_id": user_id})
    if not enrollment:
        raise HTTPException(status_code=404, detail="Not enrolled in this course")
    current_league = enrollment.get("current_league", "BRONZE")
    if current_league not in ["SILVER", "GOLD", "PLATINUM", "DIAMOND", "MYTHIC", "LEGEND"]:
        raise HTTPException(status_code=403, detail=f"Certificate not available. Current league: {current_league}. Required: SILVER or higher.")
    course = await db.courses.find_one({"course_id": course_id})
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    user_profile = await db.users_profile.find_one({"user_id": user_id})
    username = user_profile.get("username", "Student") if user_profile else "Student"
    certificate_bytes = await generate_certificate_image(username=username, sidhi_id=enrollment.get("sidhi_id", "N/A"), course_title=course.get("title", "Course"), league=current_league, points=enrollment.get("league_points", 0), solved_count=len(enrollment.get("solved_questions", [])), completion_date=datetime.utcnow(), certificate_id=enrollment.get("certificate_id", "N/A"))
    await db.certificate_downloads.update_one({"certificate_id": enrollment.get("certificate_id"), "user_id": user_id, "course_id": course_id}, {"$set": {"downloaded_at": datetime.utcnow(), "league_at_download": current_league, "points_at_download": enrollment.get("league_points", 0)}, "$inc": {"download_count": 1}}, upsert=True)
    return Response(content=certificate_bytes, media_type="image/png", headers={"Content-Disposition": f"attachment; filename=certificate_{course_id}_{user_id}.png"})


@router.get("/course/{course_id}/certificate/preview")
async def preview_certificate(course_id: str, db: AsyncIOMotorDatabase = Depends(get_db), user_id: str = Depends(get_current_user_id)):
    enrollment = await db.course_enrollments.find_one({"course_id": course_id, "user_id": user_id})
    if not enrollment:
        raise HTTPException(status_code=404, detail="Not enrolled in this course")
    course = await db.courses.find_one({"course_id": course_id})
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    user_profile = await db.users_profile.find_one({"user_id": user_id})
    username = user_profile.get("username", "Student") if user_profile else "Student"
    current_league = enrollment.get("current_league", "BRONZE")
    certificate_bytes = await generate_certificate_image(username=username, sidhi_id=enrollment.get("sidhi_id", "N/A"), course_title=course.get("title", "Course"), league=current_league, points=enrollment.get("league_points", 0), solved_count=len(enrollment.get("solved_questions", [])), completion_date=datetime.utcnow(), certificate_id=enrollment.get("certificate_id", "PREVIEW"))
    return Response(content=certificate_bytes, media_type="image/png")


@router.get("/my-certificates")
async def get_my_certificates(db: AsyncIOMotorDatabase = Depends(get_db), user_id: str = Depends(get_current_user_id)):
    eligible_leagues = ["SILVER", "GOLD", "PLATINUM", "DIAMOND", "MYTHIC", "LEGEND"]
    enrollments = await db.course_enrollments.find({"user_id": user_id, "current_league": {"$in": eligible_leagues}}).to_list(length=None)
    certificates = []
    for enr in enrollments:
        course = await db.courses.find_one({"course_id": enr["course_id"]})
        if not course:
            continue
        download_info = await db.certificate_downloads.find_one({"certificate_id": enr.get("certificate_id"), "user_id": user_id})
        certificates.append({"certificate_id": enr.get("certificate_id"), "course_id": enr["course_id"], "course_title": course.get("title"), "league": enr.get("current_league"), "points": enr.get("league_points", 0), "problems_solved": len(enr.get("solved_questions", [])), "enrolled_at": enr.get("enrolled_at"), "download_count": download_info.get("download_count", 0) if download_info else 0, "last_downloaded": download_info.get("downloaded_at") if download_info else None})
    return {"certificates": certificates, "total": len(certificates)}
"""
Lumetrix Certificate Router — The Moat
Every field on this page is a recruiting signal.
"""

from fastapi import APIRouter, HTTPException, Depends, Response
from motor.motor_asyncio import AsyncIOMotorDatabase
from typing import List, Dict, Optional
from app.courses.dependencies import get_db, get_current_user_id
from datetime import datetime, timedelta
from PIL import Image, ImageDraw, ImageFont
import io

router = APIRouter(tags=["Certificates"])


# ══════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════

def _iso(dt) -> Optional[str]:
    if isinstance(dt, datetime):
        return dt.isoformat()
    return dt


def _humanize_duration(seconds: float) -> str:
    """Turn seconds into '3 days 4 hrs' etc."""
    if seconds < 60:
        return f"{int(seconds)}s"
    m = int(seconds // 60)
    if m < 60:
        return f"{m} min"
    h = int(m // 60)
    if h < 24:
        return f"{h} hr {'%dm' % (m % 60) if m % 60 else ''}"
    d = int(h // 24)
    return f"{d}d {h % 24}h"


def get_skills_dynamic(domain: str, by_language: Dict[str, int], by_difficulty: Dict[str, int]) -> List[dict]:
    """
    Dynamic skill tags derived from actual solved data.
    Each skill carries a level (beginner/intermediate/advanced) based on volume.
    """
    base_skills = {
        "SOFTWARE": ["Problem Solving", "Algorithmic Thinking", "Code Optimization", "Debugging"],
        "HARDWARE": ["Digital Design", "HDL Programming", "Circuit Optimization", "RTL Design"],
    }

    lang_display = {
        "c": "C Programming", "cpp": "C++", "python": "Python",
        "java": "Java", "javascript": "JavaScript",
        "verilog": "Verilog", "vhdl": "VHDL", "systemverilog": "SystemVerilog",
    }

    skills = []

    # Language-based skills with proficiency
    for lang, count in by_language.items():
        level = "Advanced" if count >= 20 else "Intermediate" if count >= 5 else "Beginner"
        skills.append({
            "name": lang_display.get(lang, lang.title()),
            "category": "Language",
            "level": level,
            "evidence": f"{count} accepted solutions"
        })

    # Difficulty-based skills
    hard_solved = by_difficulty.get("hard", 0)
    med_solved  = by_difficulty.get("medium", 0)
    easy_solved = by_difficulty.get("easy", 0)

    if hard_solved >= 5:
        skills.append({"name": "Advanced Algorithms", "category": "Skill", "level": "Advanced", "evidence": f"{hard_solved} hard problems solved"})
    elif hard_solved >= 1:
        skills.append({"name": "Advanced Algorithms", "category": "Skill", "level": "Intermediate", "evidence": f"{hard_solved} hard problems solved"})

    if med_solved >= 10:
        skills.append({"name": "Data Structures", "category": "Skill", "level": "Intermediate", "evidence": f"{med_solved} medium problems solved"})

    if easy_solved + med_solved + hard_solved >= 20:
        skills.append({"name": "Consistent Practice", "category": "Habit", "level": "Verified", "evidence": f"{easy_solved + med_solved + hard_solved} total problems"})

    # Domain base skills
    for s in base_skills.get(domain, []):
        skills.append({"name": s, "category": "Domain", "level": "Demonstrated", "evidence": "Course completion"})

    return skills


async def get_daily_activity(db, user_id, course_id, enrolled_at) -> List[Dict]:
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


async def get_monthly_breakdown(db, user_id, course_id) -> List[Dict]:
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


async def get_language_stats(db, user_id, course_id) -> Dict[str, int]:
    pipeline = [
        {"$match": {"user_id": user_id, "course_id": course_id, "verdict": "Accepted"}},
        {"$group": {"_id": "$language", "count": {"$sum": 1}}}
    ]
    results = await db.course_submissions.aggregate(pipeline).to_list(None)
    return {r["_id"]: r["count"] for r in results if r["_id"]}


async def get_difficulty_stats(db, user_id, course_id) -> Dict[str, int]:
    pipeline = [
        {"$match": {"user_id": user_id, "course_id": course_id, "verdict": "Accepted"}},
        {"$lookup": {"from": "course_questions", "localField": "question_id", "foreignField": "question_id", "as": "q"}},
        {"$unwind": "$q"},
        {"$group": {"_id": "$q.difficulty", "unique_problems": {"$addToSet": "$question_id"}}},
        {"$project": {"difficulty": "$_id", "solved": {"$size": "$unique_problems"}}}
    ]
    results = await db.course_submissions.aggregate(pipeline).to_list(None)
    return {
        "easy":   next((r["solved"] for r in results if r.get("difficulty") == "easy"), 0),
        "medium": next((r["solved"] for r in results if r.get("difficulty") == "medium"), 0),
        "hard":   next((r["solved"] for r in results if r.get("difficulty") == "hard"), 0),
    }


def calculate_streaks(daily_activity: List[Dict]) -> Dict:
    if not daily_activity:
        return {"current": 0, "longest": 0, "total_active_days": 0}
    dates = sorted([d["date"] for d in daily_activity])
    longest = cur_streak = 1
    for i in range(1, len(dates)):
        diff = (datetime.strptime(dates[i], "%Y-%m-%d") - datetime.strptime(dates[i-1], "%Y-%m-%d")).days
        if diff == 1:
            cur_streak += 1
            longest = max(longest, cur_streak)
        else:
            cur_streak = 1
    # Current streak from today
    current = 0
    today = datetime.utcnow().date()
    for d in reversed(dates):
        date = datetime.strptime(d, "%Y-%m-%d").date()
        if (today - date).days <= 1:
            current += 1
            today = date - timedelta(days=1)
        else:
            break
    return {"current": current, "longest": longest, "total_active_days": len(dates)}


async def get_speed_metrics(db, user_id, course_id, enrolled_at) -> Dict:
    """
    Time-based metrics that signal work ethic to recruiters:
    - time_to_first_solve: how fast they cracked their first problem
    - avg_time_between_solves: consistency signal
    - fastest_solve_minutes: best performance
    """
    accepted = await db.course_submissions.find(
        {"user_id": user_id, "course_id": course_id, "verdict": "Accepted"}
    ).sort("submitted_at", 1).to_list(None)

    if not accepted:
        return {"time_to_first_solve_hours": None, "avg_time_between_solves_hours": None, "fastest_solve_minutes": None, "solve_dates": []}

    first_solve_at = accepted[0]["submitted_at"]
    enrolled_dt    = enrolled_at if isinstance(enrolled_at, datetime) else datetime.fromisoformat(str(enrolled_at))
    time_to_first  = (first_solve_at - enrolled_dt).total_seconds() / 3600  # hours

    solve_times = [s["submitted_at"] for s in accepted]
    gaps = []
    for i in range(1, len(solve_times)):
        gap = (solve_times[i] - solve_times[i-1]).total_seconds() / 3600
        if gap < 720:  # ignore gaps > 30 days (they took a break)
            gaps.append(gap)

    # Fastest solve: smallest time from first submission on a question to accepted
    fastest_minutes = None
    question_first_sub = {}
    all_subs = await db.course_submissions.find(
        {"user_id": user_id, "course_id": course_id}
    ).sort("submitted_at", 1).to_list(None)

    for sub in all_subs:
        qid = sub["question_id"]
        if qid not in question_first_sub:
            question_first_sub[qid] = sub["submitted_at"]
        if sub.get("verdict") == "Accepted":
            elapsed = (sub["submitted_at"] - question_first_sub[qid]).total_seconds() / 60
            if fastest_minutes is None or elapsed < fastest_minutes:
                fastest_minutes = round(elapsed, 1)

    return {
        "time_to_first_solve_hours": round(time_to_first, 1),
        "avg_time_between_solves_hours": round(sum(gaps) / len(gaps), 1) if gaps else None,
        "fastest_solve_minutes": fastest_minutes,
        "solve_dates": [_iso(s["submitted_at"]) for s in accepted],
    }


async def get_rank_context(db, user_id, course_id, league_points: int, college: Optional[str], department: Optional[str]) -> Dict:
    """
    Rank data at 3 scopes: course-wide, college, department.
    This is the most powerful recruiter signal — percentile context.
    """
    total_enrolled = await db.course_enrollments.count_documents({"course_id": course_id, "is_active": True})

    course_rank = await db.course_enrollments.count_documents({
        "course_id": course_id,
        "is_active": True,
        "league_points": {"$gt": league_points}
    }) + 1

    percentile = round((1 - (course_rank - 1) / max(total_enrolled, 1)) * 100, 1)

    result = {
        "course_rank": course_rank,
        "total_enrolled": total_enrolled,
        "percentile": percentile,  # top X% of all students
        "college_rank": None,
        "college_total": None,
        "department_rank": None,
        "department_total": None,
    }

    if college:
        # Get all enrolled users in this course who are from same college
        pipeline = [
            {"$match": {"course_id": course_id, "is_active": True}},
            {"$lookup": {"from": "users_profile", "localField": "user_id", "foreignField": "user_id", "as": "profile"}},
            {"$unwind": "$profile"},
            {"$match": {"profile.college": college}},
            {"$sort": {"league_points": -1}},
            {"$project": {"user_id": 1, "league_points": 1}}
        ]
        college_students = await db.course_enrollments.aggregate(pipeline).to_list(None)
        result["college_total"] = len(college_students)
        college_rank = next((i+1 for i, s in enumerate(college_students) if s["user_id"] == user_id), None)
        result["college_rank"] = college_rank

    if college and department:
        pipeline = [
            {"$match": {"course_id": course_id, "is_active": True}},
            {"$lookup": {"from": "users_profile", "localField": "user_id", "foreignField": "user_id", "as": "profile"}},
            {"$unwind": "$profile"},
            {"$match": {"profile.college": college, "profile.department": department}},
            {"$sort": {"league_points": -1}},
            {"$project": {"user_id": 1, "league_points": 1}}
        ]
        dept_students = await db.course_enrollments.aggregate(pipeline).to_list(None)
        result["department_total"] = len(dept_students)
        dept_rank = next((i+1 for i, s in enumerate(dept_students) if s["user_id"] == user_id), None)
        result["department_rank"] = dept_rank

    return result


async def get_timeline_events(db, user_id, course_id, enrolled_at, league_points, current_league) -> List[Dict]:
    events = []

    events.append({"date": _iso(enrolled_at), "type": "enrollment", "title": "Enrolled in Course", "icon": "🎓", "description": "Started the learning journey"})

    first_sub = await db.course_submissions.find_one({"user_id": user_id, "course_id": course_id}, sort=[("submitted_at", 1)])
    if first_sub:
        events.append({"date": _iso(first_sub["submitted_at"]), "type": "first_submission", "title": "First Submission", "icon": "📝", "description": "Began solving problems"})

    first_accepted = await db.course_submissions.find_one({"user_id": user_id, "course_id": course_id, "verdict": "Accepted"}, sort=[("submitted_at", 1)])
    if first_accepted:
        events.append({"date": _iso(first_accepted["submitted_at"]), "type": "first_accepted", "title": "First Accepted Solution", "icon": "✅", "description": "Cracked their first problem"})

    # League milestones
    LEAGUE_ORDER  = ["BRONZE", "SILVER", "GOLD", "PLATINUM", "DIAMOND", "MYTHIC", "LEGEND"]
    LEAGUE_THRESHOLDS = {"BRONZE": 0, "SILVER": 2500, "GOLD": 5000, "PLATINUM": 10000, "DIAMOND": 20000, "MYTHIC": 35000, "LEGEND": 55000}
    LEAGUE_ICONS  = {"SILVER": "🥈", "GOLD": "🥇", "PLATINUM": "💠", "DIAMOND": "💎", "MYTHIC": "🔮", "LEGEND": "🔥"}

    current_idx = LEAGUE_ORDER.index(current_league) if current_league in LEAGUE_ORDER else 0
    for league in LEAGUE_ORDER[1:current_idx+1]:
        threshold = LEAGUE_THRESHOLDS[league]
        # Find approximate date when threshold was crossed by looking at cumulative points via submissions
        # Best approximation: find when they got enough accepted submissions to cross the threshold
        events.append({
            "date": None,  # we don't store exact league-up timestamps yet
            "type": "league_up",
            "title": f"Reached {league.title()} League",
            "icon": LEAGUE_ICONS.get(league, "🏆"),
            "description": f"Crossed {threshold:,} points"
        })

    # Badges
    badges = await db.user_achievements.find({"user_id": user_id, "course_id": course_id}).to_list(None)
    for badge in badges:
        events.append({"date": _iso(badge.get("unlocked_at")), "type": "badge", "title": f"Unlocked: {badge['title']}", "icon": badge.get("icon", "🏆"), "description": badge.get("description", "")})

    events.sort(key=lambda x: x["date"] or "0000")
    return events


async def get_consistency_score(daily_activity: List[Dict], enrolled_days: int) -> Dict:
    """
    Consistency score: what % of days since enrollment did they code?
    This is a massive signal for recruiters.
    """
    if enrolled_days <= 0:
        return {"score": 0, "active_days": 0, "enrolled_days": 0, "label": "No data"}
    active = len(daily_activity)
    score  = round((active / max(enrolled_days, 1)) * 100, 1)
    label  = "Exceptional" if score >= 60 else "Strong" if score >= 30 else "Growing" if score >= 10 else "Early Stage"
    return {"score": score, "active_days": active, "enrolled_days": enrolled_days, "label": label}


# ══════════════════════════════════════════════════════════════════
#  CERTIFICATE IMAGE GENERATION
# ══════════════════════════════════════════════════════════════════

async def generate_certificate_image(username, sidhi_id, course_title, league, points, solved_count, completion_date, certificate_id) -> bytes:
    width, height = 1920, 1080
    img  = Image.new("RGB", (width, height), color="white")
    draw = ImageDraw.Draw(img)

    primary   = (41, 128, 185)
    secondary = (52, 73, 94)
    gold      = (241, 196, 15)

    draw.rectangle([50, 50, width-50, height-50], outline=primary, width=10)
    draw.rectangle([70, 70, width-70, height-70], outline=gold, width=3)

    try:
        f_title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf", 80)
        f_sub   = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf", 40)
        f_text  = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 36)
        f_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 28)
    except:
        f_title = f_sub = f_text = f_small = ImageFont.load_default()

    def centered(text, font, y, fill):
        bbox = draw.textbbox((0, 0), text, font=font)
        draw.text(((width - (bbox[2] - bbox[0])) / 2, y), text, fill=fill, font=font)

    centered("CERTIFICATE OF ACHIEVEMENT", f_title, 120, primary)
    centered("This is to certify that", f_sub, 240, secondary)
    centered(username, f_title, 310, gold)
    centered(f"ID: {sidhi_id}", f_small, 415, secondary)
    centered("has successfully completed", f_text, 490, secondary)
    centered(course_title, f_title, 550, primary)

    LEAGUE_COLORS = {"BRONZE": (205,127,50), "SILVER": (150,150,150), "GOLD": (255,215,0), "PLATINUM": (180,180,200), "DIAMOND": (100,180,255), "MYTHIC": (138,43,226), "LEGEND": (255,0,0)}
    centered(f"League: {league}  ·  {points:,} Points  ·  {solved_count} Problems Solved", f_text, 680, LEAGUE_COLORS.get(league, gold))
    centered(f"Issued: {completion_date.strftime('%B %d, %Y')}", f_small, 780, secondary)
    centered(f"Certificate ID: {certificate_id}", f_small, 850, secondary)
    draw.line([(width//2-200, 950), (width//2+200, 950)], fill=secondary, width=2)
    centered("Lumetrix · Authorized Certificate", f_small, 960, secondary)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf.getvalue()


# ══════════════════════════════════════════════════════════════════
#  ENDPOINTS
# ══════════════════════════════════════════════════════════════════

@router.get("/data/{certificate_id}")
async def get_certificate_data(certificate_id: str, db: AsyncIOMotorDatabase = Depends(get_db)):
    """
    The main certificate endpoint — loaded with every recruiting signal we have.
    Public — no auth required. This is the shareable link.
    """
    # ── 1. Enrollment ────────────────────────────────────────────
    enrollment = await db.course_enrollments.find_one({"certificate_id": certificate_id})
    if not enrollment:
        raise HTTPException(status_code=404, detail="Certificate not found")

    user_id     = enrollment["user_id"]
    course_id   = enrollment["course_id"]
    enrolled_at = enrollment["enrolled_at"]
    if not isinstance(enrolled_at, datetime):
        enrolled_at = datetime.fromisoformat(str(enrolled_at))

    league_points   = enrollment.get("league_points", 0)
    current_league  = enrollment.get("current_league", "BRONZE")

    # ── 2. User profile ──────────────────────────────────────────
    user = await db.users_profile.find_one({"user_id": user_id})
    college    = user.get("college")    if user else None
    department = user.get("department") if user else None
    username   = user.get("username", "Student") if user else enrollment.get("sidhi_id", user_id)

    # Clean sidhi_id — if it's an email keep only the local part for display
    raw_sidhi = user.get("sidhi_id", "") if user else enrollment.get("sidhi_id", "")
    display_sidhi = raw_sidhi.split("@")[0] if "@" in str(raw_sidhi) else raw_sidhi

    user_data = {
        "user_id":       user_id,
        "sidhi_id":      raw_sidhi,
        "display_id":    display_sidhi,   # clean version for UI
        "username":      username,
        "college":       college,
        "department":    department,
        "state":         user.get("state") if user else None,
    }

    # ── 3. Course ────────────────────────────────────────────────
    course = await db.courses.find_one({"course_id": course_id})
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")

    # ── 4. Progress ──────────────────────────────────────────────
    total_questions  = await db.course_questions.count_documents({"course_id": course_id, "is_active": True})
    solved_questions = enrollment.get("solved_questions", [])
    solved_count     = len(solved_questions)
    completion_pct   = round((solved_count / total_questions * 100) if total_questions > 0 else 0, 1)

    # ── 5. Submission stats ──────────────────────────────────────
    total_subs    = await db.course_submissions.count_documents({"user_id": user_id, "course_id": course_id})
    accepted_subs = await db.course_submissions.count_documents({"user_id": user_id, "course_id": course_id, "verdict": "Accepted"})
    acceptance_rate = round((accepted_subs / total_subs * 100) if total_subs > 0 else 0, 1)

    # ── 6. Parallel data fetches ─────────────────────────────────
    daily_activity  = await get_daily_activity(db, user_id, course_id, enrolled_at)
    monthly         = await get_monthly_breakdown(db, user_id, course_id)
    lang_stats      = await get_language_stats(db, user_id, course_id)
    diff_stats      = await get_difficulty_stats(db, user_id, course_id)
    streaks         = calculate_streaks(daily_activity)
    speed           = await get_speed_metrics(db, user_id, course_id, enrolled_at)
    rank_ctx        = await get_rank_context(db, user_id, course_id, league_points, college, department)
    timeline        = await get_timeline_events(db, user_id, course_id, enrolled_at, league_points, current_league)

    # ── 7. Consistency ───────────────────────────────────────────
    enrolled_days = (datetime.utcnow() - enrolled_at).days + 1
    consistency   = await get_consistency_score(daily_activity, enrolled_days)

    # ── 8. avg_efficiency — compute from actual accepted submissions ──
    eff_pipeline = [
        {"$match": {"user_id": user_id, "course_id": course_id, "verdict": "Accepted"}},
        {"$group": {"_id": None, "avg_eff": {"$avg": {"$ifNull": ["$efficiency_multiplier", 1.0]}}}}
    ]
    eff_result  = await db.course_submissions.aggregate(eff_pipeline).to_list(1)
    avg_efficiency = round(eff_result[0]["avg_eff"], 3) if eff_result else 0.0

    # Also persist it back to enrollment so dashboard shows it too
    if avg_efficiency > 0:
        await db.course_enrollments.update_one(
            {"certificate_id": certificate_id},
            {"$set": {"avg_efficiency": avg_efficiency}}
        )

    # ── 9. Certificate earned timestamp ─────────────────────────
    # We define "earned" as when they crossed Silver league.
    # Best proxy: timestamp of the submission that pushed them to 2500 pts
    # For now: first accepted submission date if Silver+, else None
    ELIGIBLE_LEAGUES = ["SILVER", "GOLD", "PLATINUM", "DIAMOND", "MYTHIC", "LEGEND"]
    certificate_earned_at = None
    if current_league in ELIGIBLE_LEAGUES and accepted_subs > 0:
        # find the submission that got them to ≥2500 pts (cumulative points approximation)
        # simplest reliable approach: date of Nth accepted where N crossed threshold
        first_acc = await db.course_submissions.find_one(
            {"user_id": user_id, "course_id": course_id, "verdict": "Accepted"},
            sort=[("submitted_at", 1)]
        )
        if first_acc:
            certificate_earned_at = _iso(first_acc["submitted_at"])

    # ── 10. Skills ───────────────────────────────────────────────
    skills = get_skills_dynamic(course.get("domain", ""), lang_stats, diff_stats)

    # ── 11. Badges ───────────────────────────────────────────────
    achievements = await db.user_achievements.find({"user_id": user_id, "course_id": course_id}).to_list(None)
    badges = [{"badge_id": a.get("badge_id"), "title": a.get("title"), "description": a.get("description"), "icon": a.get("icon", "🏆"), "unlocked_at": _iso(a.get("unlocked_at"))} for a in achievements]

    # ── 12. Build response ───────────────────────────────────────
    return {
        "certificate_id": certificate_id,
        "certificate_earned_at": certificate_earned_at,
        "generated_at": datetime.utcnow().isoformat(),

        # ── WHO ──────────────────────────────────────────────────
        "user": user_data,

        # ── WHAT COURSE ──────────────────────────────────────────
        "course": {
            "course_id":   course_id,
            "title":       course.get("title"),
            "description": course.get("description"),  # ← added
            "domain":      course.get("domain"),
            "course_type": course.get("course_type"),
            "enrolled_at": _iso(enrolled_at),
        },

        # ── HOW FAR ──────────────────────────────────────────────
        "progress": {
            "total_problems":      total_questions,
            "solved_problems":     solved_count,
            "completion_percentage": completion_pct,
            "grade_points":        league_points,
            "current_league":      current_league,
        },

        # ── RANK CONTEXT (the moat) ───────────────────────────────
        "ranking": {
            "course_rank":       rank_ctx["course_rank"],       # e.g. 14
            "total_enrolled":    rank_ctx["total_enrolled"],    # e.g. 430
            "percentile":        rank_ctx["percentile"],        # e.g. 96.7  → "Top 4%"
            "college_rank":      rank_ctx["college_rank"],      # e.g. 3
            "college_total":     rank_ctx["college_total"],     # e.g. 47
            "department_rank":   rank_ctx["department_rank"],  # e.g. 1
            "department_total":  rank_ctx["department_total"], # e.g. 12
        },

        # ── HOW WELL ─────────────────────────────────────────────
        "stats": {
            "acceptance_rate":    acceptance_rate,
            "total_submissions":  total_subs,
            "accepted_submissions": accepted_subs,
            "avg_efficiency":     avg_efficiency,
            "current_streak":     streaks["current"],
            "longest_streak":     streaks["longest"],
            "total_active_days":  streaks["total_active_days"],
            "by_difficulty":      diff_stats,
            "by_language":        lang_stats,
        },

        # ── HOW FAST ─────────────────────────────────────────────
        "speed": {
            "time_to_first_solve_hours":    speed["time_to_first_solve_hours"],
            "avg_time_between_solves_hours":speed["avg_time_between_solves_hours"],
            "fastest_solve_minutes":        speed["fastest_solve_minutes"],
            "enrolled_days":                enrolled_days,
        },

        # ── HOW CONSISTENT ───────────────────────────────────────
        "consistency": consistency,

        # ── ACTIVITY ─────────────────────────────────────────────
        "activity": {
            "daily":   daily_activity,
            "monthly": monthly,
        },

        # ── JOURNEY ──────────────────────────────────────────────
        "timeline": timeline,

        # ── WHAT THEY KNOW ───────────────────────────────────────
        "skills":  skills,
        "badges":  badges,
    }


@router.get("/verify/{certificate_id}")
async def verify_certificate(certificate_id: str, db: AsyncIOMotorDatabase = Depends(get_db)):
    enrollment = await db.course_enrollments.find_one({"certificate_id": certificate_id})
    if not enrollment:
        return {"valid": False, "message": "Certificate not found"}
    user = await db.users_profile.find_one({"user_id": enrollment["user_id"]})
    return {
        "valid":          True,
        "certificate_id": certificate_id,
        "issued_to":      user.get("username") if user else enrollment.get("sidhi_id"),
        "sidhi_id":       enrollment.get("sidhi_id"),
        "course_id":      enrollment["course_id"],
        "issued_at":      _iso(enrollment["enrolled_at"]),
        "league":         enrollment.get("current_league", "BRONZE"),
        "message":        "Certificate is authentic and verified by Lumetrix"
    }


@router.post("/claim")
async def claim_certificate(db: AsyncIOMotorDatabase = Depends(get_db), user_id: str = Depends(get_current_user_id)):
    enrollments = await db.course_enrollments.find({"user_id": user_id, "is_active": True}).to_list(None)
    certs = [
        {"certificate_id": e["certificate_id"], "course_id": e["course_id"], "url": f"https://lumetrix.com/certificates/{e['certificate_id']}", "claimable": True}
        for e in enrollments if e.get("league_points", 0) >= 1000
    ]
    return {"certificates": certs, "count": len(certs)}


@router.get("/course/{course_id}/certificate/check-eligibility")
async def check_eligibility(course_id: str, db: AsyncIOMotorDatabase = Depends(get_db), user_id: str = Depends(get_current_user_id)):
    enrollment = await db.course_enrollments.find_one({"course_id": course_id, "user_id": user_id})
    if not enrollment:
        raise HTTPException(status_code=404, detail="Not enrolled")
    current_league  = enrollment.get("current_league", "BRONZE")
    ELIGIBLE        = ["SILVER", "GOLD", "PLATINUM", "DIAMOND", "MYTHIC", "LEGEND"]
    is_eligible     = current_league in ELIGIBLE
    course          = await db.courses.find_one({"course_id": course_id})
    pts             = enrollment.get("league_points", 0)
    return {
        "eligible":        is_eligible,
        "current_league":  current_league,
        "required_league": "SILVER",
        "league_points":   pts,
        "points_needed":   max(0, 2500 - pts),
        "certificate_id":  enrollment.get("certificate_id") if is_eligible else None,
        "course_title":    course.get("title") if course else "Unknown",
        "message":         "Certificate available!" if is_eligible else f"Reach Silver league to unlock — {max(0, 2500-pts):,} pts to go"
    }


@router.get("/course/{course_id}/certificate/download")
async def download_certificate(course_id: str, db: AsyncIOMotorDatabase = Depends(get_db), user_id: str = Depends(get_current_user_id)):
    enrollment = await db.course_enrollments.find_one({"course_id": course_id, "user_id": user_id})
    if not enrollment:
        raise HTTPException(status_code=404, detail="Not enrolled")
    league = enrollment.get("current_league", "BRONZE")
    if league not in ["SILVER", "GOLD", "PLATINUM", "DIAMOND", "MYTHIC", "LEGEND"]:
        raise HTTPException(status_code=403, detail=f"Reach Silver league to download certificate. Current: {league}")
    course   = await db.courses.find_one({"course_id": course_id})
    profile  = await db.users_profile.find_one({"user_id": user_id})
    username = profile.get("username", "Student") if profile else "Student"
    img_bytes = await generate_certificate_image(
        username=username, sidhi_id=enrollment.get("sidhi_id", "N/A"),
        course_title=course.get("title", "Course"), league=league,
        points=enrollment.get("league_points", 0),
        solved_count=len(enrollment.get("solved_questions", [])),
        completion_date=datetime.utcnow(),
        certificate_id=enrollment.get("certificate_id", "N/A")
    )
    await db.certificate_downloads.update_one(
        {"certificate_id": enrollment.get("certificate_id"), "user_id": user_id, "course_id": course_id},
        {"$set": {"downloaded_at": datetime.utcnow(), "league_at_download": league, "points_at_download": enrollment.get("league_points", 0)}, "$inc": {"download_count": 1}},
        upsert=True
    )
    return Response(content=img_bytes, media_type="image/png", headers={"Content-Disposition": f"attachment; filename=lumetrix_certificate_{course_id}.png"})


@router.get("/course/{course_id}/certificate/preview")
async def preview_certificate(course_id: str, db: AsyncIOMotorDatabase = Depends(get_db), user_id: str = Depends(get_current_user_id)):
    enrollment = await db.course_enrollments.find_one({"course_id": course_id, "user_id": user_id})
    if not enrollment:
        raise HTTPException(status_code=404, detail="Not enrolled")
    course   = await db.courses.find_one({"course_id": course_id})
    profile  = await db.users_profile.find_one({"user_id": user_id})
    username = profile.get("username", "Student") if profile else "Student"
    img_bytes = await generate_certificate_image(
        username=username, sidhi_id=enrollment.get("sidhi_id", "N/A"),
        course_title=course.get("title", "Course"), league=enrollment.get("current_league", "BRONZE"),
        points=enrollment.get("league_points", 0),
        solved_count=len(enrollment.get("solved_questions", [])),
        completion_date=datetime.utcnow(),
        certificate_id=enrollment.get("certificate_id", "PREVIEW")
    )
    return Response(content=img_bytes, media_type="image/png")


@router.get("/my-certificates")
async def get_my_certificates(db: AsyncIOMotorDatabase = Depends(get_db), user_id: str = Depends(get_current_user_id)):
    ELIGIBLE = ["SILVER", "GOLD", "PLATINUM", "DIAMOND", "MYTHIC", "LEGEND"]
    enrollments = await db.course_enrollments.find({"user_id": user_id, "current_league": {"$in": ELIGIBLE}}).to_list(None)
    certs = []
    for enr in enrollments:
        course = await db.courses.find_one({"course_id": enr["course_id"]})
        if not course:
            continue
        dl = await db.certificate_downloads.find_one({"certificate_id": enr.get("certificate_id"), "user_id": user_id})
        total_enrolled = await db.course_enrollments.count_documents({"course_id": enr["course_id"], "is_active": True})
        course_rank    = await db.course_enrollments.count_documents({"course_id": enr["course_id"], "is_active": True, "league_points": {"$gt": enr.get("league_points", 0)}}) + 1
        certs.append({
            "certificate_id":  enr.get("certificate_id"),
            "course_id":       enr["course_id"],
            "course_title":    course.get("title"),
            "league":          enr.get("current_league"),
            "points":          enr.get("league_points", 0),
            "problems_solved": len(enr.get("solved_questions", [])),
            "course_rank":     course_rank,
            "total_enrolled":  total_enrolled,
            "percentile":      round((1 - (course_rank-1)/max(total_enrolled,1))*100, 1),
            "enrolled_at":     _iso(enr.get("enrolled_at")),
            "download_count":  dl.get("download_count", 0) if dl else 0,
            "last_downloaded": _iso(dl.get("downloaded_at")) if dl else None,
            "share_url":       f"https://lumetrix.com/certificates/{enr.get('certificate_id')}",
        })
    return {"certificates": certs, "total": len(certs)}
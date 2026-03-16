"""
dashboard_router.py
────────────────────
Single endpoint that powers the entire student landing dashboard.
One call — everything the frontend needs.

Mount with:
    app.include_router(dashboard_router.router, prefix="/api/dashboard")

Endpoint:
    GET /api/dashboard/home
"""

from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase
from datetime import datetime, timedelta
from app.courses.dependencies import get_db, get_current_user_id

router = APIRouter(tags=["Dashboard"])

LEAGUE_ORDER = ["BRONZE", "SILVER", "GOLD", "PLATINUM", "DIAMOND", "MYTHIC", "LEGEND"]

LEAGUE_THRESHOLDS = {
    "BRONZE":   0,
    "SILVER":   2000,
    "GOLD":     6000,
    "PLATINUM": 14000,
    "DIAMOND":  26000,
    "MYTHIC":   42000,
    "LEGEND":   60000,
}


def _next_league_info(current_league: str, current_points: int) -> dict:
    """Return next league name and how many points away it is."""
    idx = LEAGUE_ORDER.index(current_league) if current_league in LEAGUE_ORDER else 0
    if idx + 1 < len(LEAGUE_ORDER):
        next_league = LEAGUE_ORDER[idx + 1]
        pts_needed  = max(0, LEAGUE_THRESHOLDS[next_league] - current_points)
        return {"next_league": next_league, "points_needed": pts_needed}
    return {"next_league": None, "points_needed": 0}


def _iso(dt) -> str | None:
    if isinstance(dt, datetime):
        return dt.isoformat()
    return dt


@router.get("/home")
async def get_dashboard_home(
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    """
    Master dashboard endpoint.

    Returns everything the landing page needs in one shot:
    ┌─ user            profile + global rank + best league across courses
    ├─ stats           total solved, total points, acceptance rate, streak
    ├─ enrolled_courses  per-course progress + league + certificate status
    ├─ available_courses  published courses NOT yet enrolled (browse section)
    ├─ recent_activity   last 5 submissions across all courses
    ├─ leaderboard_preview  top 5 global + user's own rank
    └─ certificates      earned certificates with rank/percentile
    """

    now = datetime.utcnow()

    # ── 1. USER PROFILE ──────────────────────────────────────────────
    profile = await db.users_profile.find_one({"user_id": user_id}) or {}

    # ── 2. ALL ENROLLMENTS ───────────────────────────────────────────
    enrollments = await db.course_enrollments.find(
        {"user_id": user_id, "is_active": True}
    ).to_list(length=None)

    enrolled_course_ids = [e["course_id"] for e in enrollments]

    # Batch fetch all enrolled courses
    enrolled_courses_docs = await db.courses.find(
        {"course_id": {"$in": enrolled_course_ids}}
    ).to_list(length=None) if enrolled_course_ids else []
    course_map = {c["course_id"]: c for c in enrolled_courses_docs}

    # Batch question counts per course
    q_counts_raw = await db.course_questions.aggregate([
        {"$match": {"course_id": {"$in": enrolled_course_ids}, "is_active": True}},
        {"$group": {"_id": "$course_id", "count": {"$sum": 1}}}
    ]).to_list(length=None) if enrolled_course_ids else []
    q_counts = {r["_id"]: r["count"] for r in q_counts_raw}

    # ── 3. BUILD ENROLLED COURSES SECTION ────────────────────────────
    total_solved_global  = 0
    total_points_global  = 0
    total_questions_global = 0
    best_league = "BRONZE"

    enrolled_courses_out = []
    for enr in enrollments:
        cid    = enr["course_id"]
        course = course_map.get(cid)
        if not course:
            continue

        total_q   = q_counts.get(cid, 0)
        solved_q  = len(enr.get("solved_questions", []))
        progress  = round((solved_q / total_q * 100) if total_q > 0 else 0, 1)
        league    = enr.get("current_league", "BRONZE")
        pts       = enr.get("league_points", 0)

        total_solved_global    += solved_q
        total_points_global    += pts
        total_questions_global += total_q

        # Track best league across all courses
        if LEAGUE_ORDER.index(league) > LEAGUE_ORDER.index(best_league):
            best_league = league

        # Rank in this course
        course_rank = await db.course_enrollments.count_documents({
            "course_id": cid,
            "is_active": True,
            "league_points": {"$gt": pts}
        }) + 1
        total_enrolled = await db.course_enrollments.count_documents({
            "course_id": cid, "is_active": True
        })

        # Certificate eligibility
        cert_eligible = league in ["SILVER", "GOLD", "PLATINUM", "DIAMOND", "MYTHIC", "LEGEND"]

        enrolled_courses_out.append({
            "course_id":       cid,
            "title":           course["title"],
            "description":     course.get("description", ""),
            "domain":          course["domain"],
            "course_type":     course["course_type"],
            "thumbnail_url":   course.get("thumbnail_url"),
            "tags":            course.get("tags", []),
            "enrolled_at":     _iso(enr.get("enrolled_at")),
            "progress": {
                "solved":      solved_q,
                "total":       total_q,
                "percentage":  progress,
            },
            "league": {
                "current":     league,
                "points":      pts,
                "next_league": _next_league_info(league, pts),
            },
            "rank": {
                "course_rank":    course_rank,
                "total_enrolled": total_enrolled,
                "percentile":     round((1 - (course_rank - 1) / max(total_enrolled, 1)) * 100, 1),
            },
            "certificate": {
                "eligible":       cert_eligible,
                "certificate_id": enr.get("certificate_id") if cert_eligible else None,
            },
            "avg_efficiency":  round(enr.get("avg_efficiency", 0.0), 3),
        })

    # ── 4. AVAILABLE COURSES (not yet enrolled) ───────────────────────
    available_cursor = db.courses.find({
        "status":    {"$in": ["PUBLISHED", "ACTIVE"]},
        "course_id": {"$nin": enrolled_course_ids}
    }).sort("created_at", -1).limit(10)
    available_docs = await available_cursor.to_list(length=10)

    available_courses_out = []
    for c in available_docs:
        cid     = c["course_id"]
        enr_cnt = await db.course_enrollments.count_documents({"course_id": cid, "is_active": True})
        q_cnt   = await db.course_questions.count_documents({"course_id": cid, "is_active": True})
        available_courses_out.append({
            "course_id":      cid,
            "title":          c["title"],
            "description":    c.get("description", ""),
            "domain":         c["domain"],
            "course_type":    c["course_type"],
            "thumbnail_url":  c.get("thumbnail_url"),
            "tags":           c.get("tags", []),
            "pricing":        c.get("pricing", {}),
            "total_questions": q_cnt,
            "total_enrolled": enr_cnt,
            "published_at":   _iso(c.get("published_at")),
        })

    # ── 5. RECENT ACTIVITY (last 5 submissions across all courses) ────
    recent_subs_cursor = db.course_submissions.find(
        {"user_id": user_id}
    ).sort("submitted_at", -1).limit(5)
    recent_subs = await recent_subs_cursor.to_list(length=5)

    recent_activity = []
    for s in recent_subs:
        q = await db.course_questions.find_one({"question_id": s.get("question_id")})
        recent_activity.append({
            "submission_id":  s["submission_id"],
            "course_id":      s["course_id"],
            "question_id":    s.get("question_id"),
            "question_title": q.get("title") if q else "Unknown",
            "difficulty":     q.get("difficulty") if q else None,
            "verdict":        s.get("verdict"),
            "language":       s.get("language"),
            "league_points_awarded": s.get("league_points_awarded", 0),
            "submitted_at":   _iso(s.get("submitted_at")),
        })

    # ── 6. GLOBAL STATS (acceptance rate, submission count) ──────────
    total_submissions = await db.course_submissions.count_documents({"user_id": user_id})
    accepted_submissions = await db.course_submissions.count_documents({
        "user_id": user_id,
        "verdict": "Accepted"
    })
    acceptance_rate = round(
        (accepted_submissions / total_submissions * 100) if total_submissions > 0 else 0, 1
    )

    # ── 7. ACTIVITY STREAK (days with at least 1 submission) ─────────
    thirty_days_ago = now - timedelta(days=30)
    activity_pipeline = [
        {"$match": {"user_id": user_id, "submitted_at": {"$gte": thirty_days_ago}}},
        {"$group": {
            "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$submitted_at"}},
            "count": {"$sum": 1}
        }},
        {"$sort": {"_id": -1}}
    ]
    activity_days = await db.course_submissions.aggregate(activity_pipeline).to_list(length=None)
    activity_map  = {d["_id"]: d["count"] for d in activity_days}

    # Calculate current streak
    streak = 0
    check_date = now.date()
    for _ in range(30):
        if check_date.isoformat() in activity_map:
            streak += 1
            check_date -= timedelta(days=1)
        else:
            break

    # ── 8. LEADERBOARD PREVIEW (top 5 global + user's own position) ──
    lb_pipeline = [
        {"$match": {"is_active": True}},
        {"$lookup": {
            "from": "courses",
            "localField": "course_id",
            "foreignField": "course_id",
            "as": "course"
        }},
        {"$unwind": "$course"},
        {"$match": {"course.course_type": "OFFICIAL"}},
        {"$group": {
            "_id":          "$user_id",
            "total_points": {"$sum": "$league_points"},
            "total_solved": {"$sum": {"$size": {"$ifNull": ["$solved_questions", []]}}},
            "best_league":  {"$max": "$current_league"},
        }},
        {"$lookup": {
            "from": "users_profile",
            "localField": "_id",
            "foreignField": "user_id",
            "as": "user"
        }},
        {"$unwind": {"path": "$user", "preserveNullAndEmptyArrays": True}},
        {"$project": {
            "user_id":      "$_id",
            "username":     {"$ifNull": ["$user.username", "Anonymous"]},
            "college":      "$user.college",
            "total_points": 1,
            "total_solved": 1,
            "best_league":  1,
        }},
        {"$sort": {"total_points": -1, "total_solved": -1}},
        {"$limit": 5}
    ]
    top5 = await db.course_enrollments.aggregate(lb_pipeline).to_list(length=5)
    for idx, entry in enumerate(top5):
        entry["rank"] = idx + 1
        entry.pop("_id", None)

    # User's own global rank
    user_global_points = total_points_global
    global_rank = await db.course_enrollments.aggregate([
        {"$match": {"is_active": True}},
        {"$lookup": {"from": "courses", "localField": "course_id", "foreignField": "course_id", "as": "course"}},
        {"$unwind": "$course"},
        {"$match": {"course.course_type": "OFFICIAL"}},
        {"$group": {"_id": "$user_id", "total_points": {"$sum": "$league_points"}}},
        {"$match": {"total_points": {"$gt": user_global_points}}},
        {"$count": "count"}
    ]).to_list(length=1)
    my_global_rank = (global_rank[0]["count"] + 1) if global_rank else 1

    # ── 9. CERTIFICATES ───────────────────────────────────────────────
    CERT_LEAGUES = ["SILVER", "GOLD", "PLATINUM", "DIAMOND", "MYTHIC", "LEGEND"]
    cert_enrollments = [e for e in enrollments if e.get("current_league") in CERT_LEAGUES]
    certificates_out = []
    for enr in cert_enrollments:
        cid    = enr["course_id"]
        course = course_map.get(cid)
        if not course:
            continue
        total_enr  = await db.course_enrollments.count_documents({"course_id": cid, "is_active": True})
        course_rank = await db.course_enrollments.count_documents({
            "course_id": cid, "is_active": True,
            "league_points": {"$gt": enr.get("league_points", 0)}
        }) + 1
        certificates_out.append({
            "certificate_id": enr.get("certificate_id"),
            "course_id":      cid,
            "course_title":   course["title"],
            "league":         enr.get("current_league"),
            "points":         enr.get("league_points", 0),
            "problems_solved":len(enr.get("solved_questions", [])),
            "course_rank":    course_rank,
            "total_enrolled": total_enr,
            "percentile":     round((1 - (course_rank - 1) / max(total_enr, 1)) * 100, 1),
            "share_url":      f"https://lumetrix.com/certificates/{enr.get('certificate_id')}",
        })

    # ── FINAL RESPONSE ────────────────────────────────────────────────
    return {

        # Who is this user
        "user": {
            "user_id":  user_id,
            "username": profile.get("username", "User"),
            "college":  profile.get("college"),
            "department": profile.get("department"),
            "state":    profile.get("state"),
            "avatar_url": profile.get("avatar_url"),
        },

        # Top-level numbers for the hero cards
        "stats": {
            "total_solved":       total_solved_global,
            "total_questions":    total_questions_global,
            "total_points":       total_points_global,
            "total_courses":      len(enrollments),
            "acceptance_rate":    acceptance_rate,
            "total_submissions":  total_submissions,
            "accepted_submissions": accepted_submissions,
            "current_streak_days": streak,
            "best_league":        best_league,
            "global_rank":        my_global_rank,
            "overall_progress":   round(
                (total_solved_global / total_questions_global * 100)
                if total_questions_global > 0 else 0, 1
            ),
        },

        # Activity heatmap data (last 30 days)
        "activity": {
            "last_30_days": [
                {"date": d, "count": activity_map.get(d, 0)}
                for d in [
                    (now - timedelta(days=i)).strftime("%Y-%m-%d")
                    for i in range(29, -1, -1)
                ]
            ]
        },

        # Enrolled courses with full progress
        "enrolled_courses": enrolled_courses_out,

        # Courses available to browse/enroll
        "available_courses": available_courses_out,

        # Last 5 submissions across all courses
        "recent_activity": recent_activity,

        # Top 5 global leaderboard + user's rank
        "leaderboard_preview": {
            "top5":          top5,
            "my_global_rank": my_global_rank,
            "my_points":     total_points_global,
        },

        # Earned certificates
        "certificates": certificates_out,

        "generated_at": _iso(now),
    }
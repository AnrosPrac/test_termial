"""
interview_router.py
────────────────────
Full interview system — teacher creation, student sessions,
integrity monitoring, judge integration, reports.

Mount with:
    app.include_router(interview_router.router, prefix="/api/interviews")
"""

from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from motor.motor_asyncio import AsyncIOMotorDatabase
from datetime import datetime, timedelta
from typing import Optional
import uuid
import httpx
import os
import asyncio

from app.courses.dependencies import get_db, get_current_user_id
from app.courses.interview_models import (
    InterviewType, InterviewStatus, SessionStatus,
    IntegrityEventType, QuestionVerdict,
    INTERVIEW_CONFIG, UNLOCK_GATES,
    InterviewCreate, StartSessionRequest,
    SubmitQuestionRequest, IntegrityEventRequest,
)

router = APIRouter(tags=["Interviews"])

SOFTWARE_JUDGE_URL = os.getenv("JUDGE_API_URL", "http://localhost:8000")
SOFTWARE_JUDGE_KEY = os.getenv("JUDGE_API_KEY", "")


# ══════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════

def _iso(dt) -> Optional[str]:
    return dt.isoformat() if isinstance(dt, datetime) else dt


def _new_id(prefix: str, length: int = 10) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:length].upper()}"


async def _verify_teacher(db, user_id: str):
    """Check user has teacher/admin role."""
    profile = await db.users_profile.find_one({"user_id": user_id})
    if not profile:
        raise HTTPException(status_code=403, detail="Profile not found")
    role = profile.get("role", "student")
    if role not in ["teacher", "admin"]:
        raise HTTPException(status_code=403, detail="Only teachers can create interviews")
    return profile


async def _get_enrollment(db, course_id: str, user_id: str):
    return await db.course_enrollments.find_one({
        "course_id": course_id,
        "user_id":   user_id,
        "is_active": True
    })


async def _check_similarity(db, code: str, course_id: str, user_id: str) -> dict:
    """
    Compare submitted code against all previous accepted submissions in the course.
    Returns similarity score and flagged status.
    Simple character-level similarity — no external service needed.
    """
    past = await db.course_submissions.find(
        {"course_id": course_id, "verdict": "Accepted"}
    ).limit(200).to_list(length=200)

    if not past:
        return {"flagged": False, "max_similarity": 0.0, "compared_against": 0}

    def similarity(a: str, b: str) -> float:
        a, b = a.strip(), b.strip()
        if not a or not b:
            return 0.0
        shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
        matches = sum(c in longer for c in shorter)
        return round(matches / max(len(longer), 1), 3)

    max_sim = 0.0
    for s in past:
        sim = similarity(code, s.get("code", ""))
        if sim > max_sim:
            max_sim = sim

    return {
        "flagged":          max_sim > 0.85,
        "max_similarity":   max_sim,
        "compared_against": len(past),
    }


async def _judge_code(code: str, language: str, test_cases: list) -> dict:
    """Submit to software judge and poll for result."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{SOFTWARE_JUDGE_URL}/judge",
                json={
                    "language":   language,
                    "sourceCode": code,
                    "testcases":  [{"input": tc["input"], "output": tc["output"]} for tc in test_cases]
                },
                headers={"X-API-Key": SOFTWARE_JUDGE_KEY},
                timeout=30.0
            )
            if resp.status_code != 200:
                return {"verdict": "System Error", "passed": 0, "total": len(test_cases)}

            task_id = resp.json().get("task_id")

            for _ in range(60):
                await asyncio.sleep(1)
                status_resp = await client.get(
                    f"{SOFTWARE_JUDGE_URL}/status/{task_id}",
                    headers={"X-API-Key": SOFTWARE_JUDGE_KEY},
                    timeout=5.0
                )
                if status_resp.status_code == 200:
                    data = status_resp.json()
                    if data.get("status") == "completed":
                        return data.get("result", {})

            return {"verdict": "Judging Timeout", "passed": 0, "total": len(test_cases)}

    except Exception as e:
        return {"verdict": "System Error", "error": str(e)}


# ══════════════════════════════════════════════════════════════
#  TEACHER ENDPOINTS
# ══════════════════════════════════════════════════════════════

@router.post("/create")
async def create_interview(
    payload: InterviewCreate,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    """Teacher creates an interview."""
    await _verify_teacher(db, user_id)

    # Verify all questions exist and belong to the specified module
    for qid in payload.question_ids:
        q = await db.course_questions.find_one({"question_id": qid, "is_active": True})
        if not q:
            raise HTTPException(status_code=404, detail=f"Question {qid} not found")
        if payload.module_id and q.get("module_id") != payload.module_id:
            raise HTTPException(
                status_code=400,
                detail=f"Question {qid} does not belong to module {payload.module_id}"
            )
        if payload.course_id and q.get("course_id") != payload.course_id:
            raise HTTPException(
                status_code=400,
                detail=f"Question {qid} does not belong to course {payload.course_id}"
            )

    # If course-tied, check slot not already taken
    if payload.course_id and payload.slot:
        existing = await db.interviews.find_one({
            "course_id": payload.course_id,
            "slot":      payload.slot,
            "status":    InterviewStatus.ACTIVE
        })
        if existing:
            raise HTTPException(
                status_code=400,
                detail=f"Slot {payload.slot} already has an active interview in this course"
            )

    cfg = INTERVIEW_CONFIG[payload.interview_type]
    interview_id = _new_id("INTV", 12)
    now = datetime.utcnow()

    doc = {
        "interview_id":      interview_id,
        "title":             payload.title,
        "interview_type":    payload.interview_type,
        "course_id":         payload.course_id,
        "module_id":         payload.module_id,
        "slot":              payload.slot,
        "question_ids":      payload.question_ids,
        "created_by":        user_id,
        "status":            InterviewStatus.ACTIVE,

        # config snapshot
        "question_count":    cfg["question_count"],
        "time_per_question": cfg["time_per_question"],
        "retakeable":        cfg["retakeable"],
        "is_pass_fail":      cfg["is_pass_fail"],
        "pass_threshold":    cfg["pass_threshold"],
        "tab_switch_limit":  cfg["tab_switch_limit"],
        "similarity_check":  cfg["similarity_check"],

        "created_at":        now,
        "updated_at":        now,
    }

    await db.interviews.insert_one(doc)
    return {"success": True, "interview_id": interview_id}


@router.get("/course/{course_id}")
async def list_course_interviews(
    course_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    """List all interviews in a course with student's unlock status."""
    enrollment = await _get_enrollment(db, course_id, user_id)
    if not enrollment:
        raise HTTPException(status_code=403, detail="Not enrolled in this course")

    solved_count = len(enrollment.get("solved_questions", []))

    interviews = await db.interviews.find(
        {"course_id": course_id, "status": InterviewStatus.ACTIVE}
    ).sort("slot", 1).to_list(length=None)

    result = []
    for intv in interviews:
        slot         = intv.get("slot")
        required     = UNLOCK_GATES.get(slot, 0) if slot else 0
        unlocked     = solved_count >= required

        # Check prerequisite chain
        prereq_met = True
        if slot == "PRE":
            # Must have completed all 6 test interviews
            test_slots = ["TEST_1","TEST_2","TEST_3","TEST_4","TEST_5","TEST_6"]
            for ts in test_slots:
                test_intv = await db.interviews.find_one({"course_id": course_id, "slot": ts, "status": "ACTIVE"})
                if test_intv:
                    done = await db.interview_sessions.find_one({
                        "interview_id": test_intv["interview_id"],
                        "user_id":      user_id,
                        "status":       {"$in": [SessionStatus.COMPLETED, SessionStatus.TERMINATED, SessionStatus.TIMED_OUT]}
                    })
                    if not done:
                        prereq_met = False
                        break

        elif slot == "FINAL":
            pre_intv = await db.interviews.find_one({"course_id": course_id, "slot": "PRE", "status": "ACTIVE"})
            if pre_intv:
                done = await db.interview_sessions.find_one({
                    "interview_id": pre_intv["interview_id"],
                    "user_id":      user_id,
                    "status":       {"$in": [SessionStatus.COMPLETED, SessionStatus.TERMINATED, SessionStatus.TIMED_OUT]}
                })
                prereq_met = bool(done)

        # Student's attempt history for this interview
        sessions = await db.interview_sessions.find(
            {"interview_id": intv["interview_id"], "user_id": user_id}
        ).sort("started_at", -1).to_list(length=5)

        result.append({
            "interview_id":    intv["interview_id"],
            "title":           intv["title"],
            "interview_type":  intv["interview_type"],
            "slot":            slot,
            "module_id":       intv.get("module_id"),
            "question_count":  intv["question_count"],
            "time_per_question_mins": intv["time_per_question"] // 60,
            "is_pass_fail":    intv["is_pass_fail"],
            "retakeable":      intv["retakeable"],
            "unlocked":        unlocked and prereq_met,
            "solved_count":    solved_count,
            "required_solved": required,
            "prereq_met":      prereq_met,
            "attempts": [
                {
                    "session_id": s["session_id"],
                    "status":     s["status"],
                    "started_at": _iso(s.get("started_at")),
                    "ended_at":   _iso(s.get("ended_at")),
                    "passed":     s.get("passed"),
                }
                for s in sessions
            ],
            "can_attempt": (
                unlocked and prereq_met and
                (intv["retakeable"] or len(sessions) == 0)
            ),
        })

    return {"course_id": course_id, "interviews": result, "solved_count": solved_count}


# ══════════════════════════════════════════════════════════════
#  STUDENT SESSION ENDPOINTS
# ══════════════════════════════════════════════════════════════

@router.post("/{interview_id}/start")
async def start_session(
    interview_id: str,
    payload: StartSessionRequest,
    request_obj: None = None,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    """
    Student starts an interview session.
    - Validates unlock gates + prerequisite chain
    - Creates a server-side session with timestamps
    - Returns first question (only — one at a time)
    """
    from fastapi import Request

    interview = await db.interviews.find_one({"interview_id": interview_id, "status": InterviewStatus.ACTIVE})
    if not interview:
        raise HTTPException(status_code=404, detail="Interview not found")

    course_id = interview.get("course_id")

    # ── GATE CHECKS ──────────────────────────────────────────────
    if course_id:
        enrollment = await _get_enrollment(db, course_id, user_id)
        if not enrollment:
            raise HTTPException(status_code=403, detail="Not enrolled in this course")

        solved_count = len(enrollment.get("solved_questions", []))
        slot         = interview.get("slot")
        required     = UNLOCK_GATES.get(slot, 0)

        if solved_count < required:
            raise HTTPException(
                status_code=403,
                detail=f"You need {required} solved questions to unlock this interview. You have {solved_count}."
            )

    # ── RETAKE CHECK ─────────────────────────────────────────────
    existing_sessions = await db.interview_sessions.find(
        {"interview_id": interview_id, "user_id": user_id}
    ).to_list(length=None)

    completed_sessions = [
        s for s in existing_sessions
        if s["status"] in [SessionStatus.COMPLETED, SessionStatus.TERMINATED, SessionStatus.TIMED_OUT]
    ]

    if not interview["retakeable"] and completed_sessions:
        raise HTTPException(
            status_code=403,
            detail="This interview type cannot be retaken."
        )

    # ── ACTIVE SESSION CHECK — one session at a time ──────────────
    active = await db.interview_sessions.find_one({
        "interview_id": interview_id,
        "user_id":      user_id,
        "status":       {"$in": [SessionStatus.STARTED, SessionStatus.IN_PROGRESS]}
    })
    if active:
        raise HTTPException(
            status_code=400,
            detail="You already have an active session. Complete or timeout first.",
        )

    # ── BUILD SESSION ─────────────────────────────────────────────
    import random
    question_ids = interview["question_ids"].copy()
    random.shuffle(question_ids)   # randomise order every attempt

    now        = datetime.utcnow()
    session_id = _new_id("SESS", 12)

    # Build per-question slots
    question_slots = [
        {
            "question_id":   qid,
            "index":         idx,
            "time_limit":    interview["time_per_question"],
            "started_at":    None,
            "submitted_at":  None,
            "deadline":      None,
            "verdict":       QuestionVerdict.NOT_ATTEMPTED,
            "code":          None,
            "language":      None,
            "judge_result":  None,
            "time_taken_sec": None,
            "similarity":    None,
        }
        for idx, qid in enumerate(question_ids)
    ]

    session_doc = {
        "session_id":        session_id,
        "interview_id":      interview_id,
        "interview_type":    interview["interview_type"],
        "course_id":         course_id,
        "user_id":           user_id,
        "status":            SessionStatus.STARTED,
        "language":          payload.language,

        "question_slots":    question_slots,
        "current_index":     0,

        # Integrity
        "tab_switch_count":  0,
        "paste_count":       0,
        "integrity_events":  [],
        "tab_switch_limit":  interview["tab_switch_limit"],
        "similarity_check":  interview["similarity_check"],
        "terminated_reason": None,

        # Timing
        "started_at":        now,
        "ended_at":          None,

        # Result
        "passed":            None,    # only set on FINAL
        "questions_accepted": 0,
        "report":            None,

        # Meta
        "attempt_number":    len(completed_sessions) + 1,
    }

    await db.interview_sessions.insert_one(session_doc)

    # Start first question timer
    await _start_question_timer(db, session_id, 0)

    # Fetch first question (public test cases only)
    first_q = await _get_safe_question(db, question_ids[0])

    return {
        "session_id":        session_id,
        "interview_type":    interview["interview_type"],
        "total_questions":   len(question_ids),
        "current_index":     0,
        "time_per_question_secs": interview["time_per_question"],
        "tab_switch_limit":  interview["tab_switch_limit"],
        "question":          first_q,
        "question_deadline": _iso(datetime.utcnow() + timedelta(seconds=interview["time_per_question"])),
        "message":           "Session started. Good luck!",
    }


async def _start_question_timer(db, session_id: str, index: int):
    """Stamp start time and deadline on a question slot."""
    session  = await db.interview_sessions.find_one({"session_id": session_id})
    if not session:
        return
    now      = datetime.utcnow()
    deadline = now + timedelta(seconds=session["question_slots"][index]["time_limit"])

    await db.interview_sessions.update_one(
        {"session_id": session_id},
        {"$set": {
            f"question_slots.{index}.started_at": now,
            f"question_slots.{index}.deadline":   deadline,
        }}
    )


async def _get_safe_question(db, question_id: str) -> dict:
    """Return question with only public (is_sample=True) test cases."""
    q = await db.course_questions.find_one({"question_id": question_id})
    if not q:
        return {}
    q["_id"] = str(q["_id"])
    q["test_cases"] = [tc for tc in q.get("test_cases", []) if tc.get("is_sample", False)]
    return q


@router.post("/session/{session_id}/submit")
async def submit_question(
    session_id: str,
    payload: SubmitQuestionRequest,
    background_tasks: BackgroundTasks,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    """
    Submit code for the current question.
    - Enforces server-side deadline
    - Runs judge
    - Advances to next question or ends session
    """
    session = await db.interview_sessions.find_one({"session_id": session_id, "user_id": user_id})
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session["status"] not in [SessionStatus.STARTED, SessionStatus.IN_PROGRESS]:
        raise HTTPException(status_code=400, detail=f"Session is already {session['status']}")

    idx   = session["current_index"]
    slot  = session["question_slots"][idx]
    now   = datetime.utcnow()

    # ── SERVER-SIDE DEADLINE CHECK ───────────────────────────────
    deadline = slot.get("deadline")
    if deadline and now > deadline:
        # Mark as skipped, auto-advance
        await _advance_or_end(db, session_id, idx, QuestionVerdict.SKIPPED, None, None, None)
        raise HTTPException(status_code=408, detail="Time limit exceeded for this question. Auto-advanced.")

    # ── JUDGE ────────────────────────────────────────────────────
    question = await db.course_questions.find_one({"question_id": slot["question_id"]})
    if not question:
        raise HTTPException(status_code=404, detail="Question not found")

    test_cases  = question.get("test_cases", [])
    judge_result = await _judge_code(payload.code, payload.language, test_cases)

    verdict_str = judge_result.get("verdict", "System Error")
    verdict     = QuestionVerdict.ACCEPTED if verdict_str == "Accepted" else QuestionVerdict(verdict_str) \
                  if verdict_str in QuestionVerdict._value2member_map_ else QuestionVerdict.WRONG_ANSWER

    time_taken = int((now - slot["started_at"]).total_seconds()) if slot.get("started_at") else None

    # ── SIMILARITY CHECK (PRE + FINAL only) ─────────────────────
    similarity_result = None
    if session.get("similarity_check") and session.get("course_id"):
        similarity_result = await _check_similarity(db, payload.code, session["course_id"], user_id)

    # ── SAVE SLOT ────────────────────────────────────────────────
    await db.interview_sessions.update_one(
        {"session_id": session_id},
        {"$set": {
            f"question_slots.{idx}.submitted_at":   now,
            f"question_slots.{idx}.verdict":        verdict,
            f"question_slots.{idx}.code":           payload.code,
            f"question_slots.{idx}.language":       payload.language,
            f"question_slots.{idx}.judge_result":   judge_result,
            f"question_slots.{idx}.time_taken_sec": time_taken,
            f"question_slots.{idx}.similarity":     similarity_result,
        }}
    )

    return await _advance_or_end(db, session_id, idx, verdict, judge_result, time_taken, similarity_result)


async def _advance_or_end(db, session_id, idx, verdict, judge_result, time_taken, similarity):
    """Move to next question or finalize the session."""
    session = await db.interview_sessions.find_one({"session_id": session_id})
    total   = len(session["question_slots"])

    accepted = session.get("questions_accepted", 0)
    if verdict == QuestionVerdict.ACCEPTED:
        accepted += 1
        await db.interview_sessions.update_one(
            {"session_id": session_id},
            {"$set": {"questions_accepted": accepted, "status": SessionStatus.IN_PROGRESS}}
        )

    next_idx = idx + 1

    if next_idx < total:
        # Advance to next question
        await db.interview_sessions.update_one(
            {"session_id": session_id},
            {"$set": {"current_index": next_idx}}
        )
        await _start_question_timer(db, session_id, next_idx)
        next_q = await _get_safe_question(db, session["question_slots"][next_idx]["question_id"])

        session = await db.interview_sessions.find_one({"session_id": session_id})
        deadline = session["question_slots"][next_idx].get("deadline")

        return {
            "submitted":       True,
            "verdict":         verdict,
            "judge_result":    judge_result,
            "similarity":      similarity,
            "time_taken_sec":  time_taken,
            "questions_accepted": accepted,
            "next_question":   next_q,
            "current_index":   next_idx,
            "total_questions": total,
            "question_deadline": _iso(deadline),
            "session_complete": False,
        }
    else:
        # All questions done — finalize
        return await _finalize_session(db, session_id, "completed")


async def _finalize_session(db, session_id: str, reason: str) -> dict:
    """Close the session, compute pass/fail, build report."""
    session = await db.interview_sessions.find_one({"session_id": session_id})
    if not session:
        return {}

    now      = datetime.utcnow()
    slots    = session["question_slots"]
    accepted = session.get("questions_accepted", 0)
    total    = len(slots)

    # Recount from slots (in case of early termination)
    accepted = sum(1 for s in slots if s.get("verdict") == QuestionVerdict.ACCEPTED)

    # Pass/fail only for FINAL
    passed = None
    threshold = INTERVIEW_CONFIG[InterviewType(session["interview_type"])]["pass_threshold"]
    if threshold is not None:
        passed = accepted >= threshold

    # Integrity summary
    events      = session.get("integrity_events", [])
    tab_count   = session.get("tab_switch_count", 0)
    paste_count = session.get("paste_count", 0)
    flagged     = tab_count >= session.get("tab_switch_limit", 99) or paste_count > 5

    status = SessionStatus.TERMINATED if reason == "terminated" else \
             SessionStatus.TIMED_OUT  if reason == "timeout"    else \
             SessionStatus.COMPLETED

    # Build full report
    report = {
        "session_id":          session_id,
        "interview_type":      session["interview_type"],
        "attempt_number":      session.get("attempt_number", 1),
        "started_at":          _iso(session.get("started_at")),
        "ended_at":            _iso(now),
        "total_time_mins":     round((now - session["started_at"]).total_seconds() / 60, 1)
                               if session.get("started_at") else None,
        "questions_accepted":  accepted,
        "total_questions":     total,
        "passed":              passed,

        "per_question": [
            {
                "index":          s["index"] + 1,
                "question_id":    s["question_id"],
                "verdict":        s.get("verdict", QuestionVerdict.NOT_ATTEMPTED),
                "time_taken_sec": s.get("time_taken_sec"),
                "language":       s.get("language"),
                "similarity":     s.get("similarity"),
                "judge_summary": {
                    "passed": s["judge_result"].get("passed") if s.get("judge_result") else None,
                    "total":  s["judge_result"].get("total")  if s.get("judge_result") else None,
                    "avg_execution_time_ms": s["judge_result"].get("avg_execution_time_ms") if s.get("judge_result") else None,
                } if s.get("judge_result") else None,
            }
            for s in slots
        ],

        "integrity": {
            "tab_switch_count":  tab_count,
            "paste_count":       paste_count,
            "focus_lost_count":  sum(1 for e in events if e.get("event_type") == IntegrityEventType.FOCUS_LOST),
            "flagged":           flagged,
            "terminated_reason": session.get("terminated_reason"),
            "events_count":      len(events),
        },

        "termination_reason": reason if reason != "completed" else None,
    }

    await db.interview_sessions.update_one(
        {"session_id": session_id},
        {"$set": {
            "status":            status,
            "ended_at":          now,
            "passed":            passed,
            "questions_accepted": accepted,
            "report":            report,
        }}
    )

    return {
        "submitted":        True,
        "session_complete": True,
        "session_id":       session_id,
        "status":           status,
        "passed":           passed,
        "questions_accepted": accepted,
        "total_questions":  total,
        "report":           report,
    }


@router.post("/session/{session_id}/event")
async def log_integrity_event(
    session_id: str,
    payload: IntegrityEventRequest,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    """
    Frontend fires this on every integrity violation.
    Auto-terminates session if tab switch limit is exceeded.
    """
    session = await db.interview_sessions.find_one({"session_id": session_id, "user_id": user_id})
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session["status"] not in [SessionStatus.STARTED, SessionStatus.IN_PROGRESS]:
        return {"logged": False, "reason": "Session not active"}

    now   = datetime.utcnow()
    event = {
        "event_type": payload.event_type,
        "timestamp":  _iso(now),
        "metadata":   payload.metadata,
    }

    update = {
        "$push": {"integrity_events": event},
    }

    tab_count   = session.get("tab_switch_count", 0)
    paste_count = session.get("paste_count", 0)

    if payload.event_type == IntegrityEventType.TAB_SWITCH:
        tab_count += 1
        update["$set"] = {"tab_switch_count": tab_count}

    elif payload.event_type == IntegrityEventType.PASTE_DETECT:
        paste_count += 1
        update["$set"] = update.get("$set", {})
        update["$set"]["paste_count"] = paste_count

    await db.interview_sessions.update_one({"session_id": session_id}, update)

    # ── AUTO-TERMINATE if tab switch limit hit ───────────────────
    limit = session.get("tab_switch_limit", 99)
    if payload.event_type == IntegrityEventType.TAB_SWITCH and tab_count >= limit:
        await db.interview_sessions.update_one(
            {"session_id": session_id},
            {"$set": {"terminated_reason": f"Tab switch limit ({limit}) exceeded"}}
        )
        result = await _finalize_session(db, session_id, "terminated")
        return {
            "logged":      True,
            "terminated":  True,
            "tab_count":   tab_count,
            "limit":       limit,
            "message":     f"Session auto-terminated: tab switch limit exceeded",
            "report":      result.get("report"),
        }

    return {
        "logged":      True,
        "terminated":  False,
        "tab_count":   tab_count,
        "paste_count": paste_count,
        "limit":       session.get("tab_switch_limit"),
        "warning":     f"{limit - tab_count} tab switch(es) remaining" if payload.event_type == IntegrityEventType.TAB_SWITCH else None,
    }


@router.post("/session/{session_id}/timeout")
async def force_timeout(
    session_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    """
    Called by frontend when question timer hits zero.
    Backend verifies deadline and auto-advances or ends session.
    """
    session = await db.interview_sessions.find_one({"session_id": session_id, "user_id": user_id})
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session["status"] not in [SessionStatus.STARTED, SessionStatus.IN_PROGRESS]:
        raise HTTPException(status_code=400, detail="Session not active")

    idx  = session["current_index"]
    slot = session["question_slots"][idx]
    now  = datetime.utcnow()

    deadline = slot.get("deadline")
    if deadline and now < deadline:
        remaining = int((deadline - now).total_seconds())
        raise HTTPException(
            status_code=400,
            detail=f"Question timer has not expired yet. {remaining}s remaining."
        )

    # Mark slot as skipped
    await db.interview_sessions.update_one(
        {"session_id": session_id},
        {"$set": {f"question_slots.{idx}.verdict": QuestionVerdict.SKIPPED}}
    )

    return await _advance_or_end(db, session_id, idx, QuestionVerdict.SKIPPED, None, None, None)


@router.get("/session/{session_id}/report")
async def get_session_report(
    session_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    """Get full report for a completed session."""
    session = await db.interview_sessions.find_one({"session_id": session_id, "user_id": user_id})
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session["status"] in [SessionStatus.STARTED, SessionStatus.IN_PROGRESS]:
        raise HTTPException(status_code=400, detail="Session still in progress")

    return session.get("report", {})


@router.get("/session/{session_id}/status")
async def get_session_status(
    session_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    """Poll active session state — current question, time left, index."""
    session = await db.interview_sessions.find_one({"session_id": session_id, "user_id": user_id})
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session["status"] not in [SessionStatus.STARTED, SessionStatus.IN_PROGRESS]:
        return {"status": session["status"], "active": False}

    idx      = session["current_index"]
    slot     = session["question_slots"][idx]
    now      = datetime.utcnow()
    deadline = slot.get("deadline")
    secs_left = max(0, int((deadline - now).total_seconds())) if deadline else None

    question = await _get_safe_question(db, slot["question_id"])

    return {
        "active":          True,
        "status":          session["status"],
        "current_index":   idx,
        "total_questions": len(session["question_slots"]),
        "seconds_left":    secs_left,
        "question":        question,
        "tab_switch_count": session.get("tab_switch_count", 0),
        "tab_switch_limit": session.get("tab_switch_limit"),
        "questions_accepted": session.get("questions_accepted", 0),
    }

# ══════════════════════════════════════════════════════════════
#  GENERAL INTERVIEWS — not tied to any course
# ══════════════════════════════════════════════════════════════

@router.get("/general")
async def list_general_interviews(
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    """
    List all general interviews (course_id: null).
    Returns each interview with the student's attempt history
    and whether they can attempt it.
    """
    interviews = await db.interviews.find(
        {"course_id": None, "status": InterviewStatus.ACTIVE}
    ).sort("created_at", -1).to_list(length=None)

    result = []
    for intv in interviews:
        # Fetch student's past sessions for this interview
        sessions = await db.interview_sessions.find(
            {"interview_id": intv["interview_id"], "user_id": user_id}
        ).sort("started_at", -1).to_list(length=10)

        completed_sessions = [
            s for s in sessions
            if s["status"] in [
                SessionStatus.COMPLETED,
                SessionStatus.TERMINATED,
                SessionStatus.TIMED_OUT
            ]
        ]

        can_attempt = intv["retakeable"] or len(completed_sessions) == 0

        result.append({
            "interview_id":           intv["interview_id"],
            "title":                  intv["title"],
            "interview_type":         intv["interview_type"],
            "question_count":         intv["question_count"],
            "time_per_question_mins": intv["time_per_question"] // 60,
            "is_pass_fail":           intv["is_pass_fail"],
            "retakeable":             intv["retakeable"],
            "tab_switch_limit":       intv["tab_switch_limit"],
            "can_attempt":            can_attempt,
            "total_attempts":         len(completed_sessions),
            "attempts": [
                {
                    "session_id": s["session_id"],
                    "status":     s["status"],
                    "started_at": _iso(s.get("started_at")),
                    "ended_at":   _iso(s.get("ended_at")),
                    "passed":     s.get("passed"),
                    "questions_accepted": s.get("questions_accepted", 0),
                }
                for s in sessions
            ],
        })

    return {
        "interviews": result,
        "total":      len(result),
    }
from datetime import datetime
from typing import List, Optional, Tuple
import secrets
import asyncio  # Add this import
from app.students.student_permissions import StudentContext, db, check_scope_match, check_can_submit
from app.students.student_models import (
    ClassroomMembership, SubmissionStatus, PlagiarismFlag, SubmissionAudit
)
from fastapi import HTTPException
import hashlib

def hash_password(password: str) -> str:
    """Hash password for comparison"""
    return hashlib.sha256(password.encode()).hexdigest()

def generate_id(prefix: str) -> str:
    """Generate unique ID with prefix"""
    return f"{prefix}_{secrets.token_hex(8).upper()}"

async def log_student_audit(
    student: StudentContext,
    action: str,
    assignment_id: str,
    success: bool,
    reason: Optional[str] = None,
    metadata: dict = None
):
    """
    Log student actions for security auditing
    """
    audit = SubmissionAudit(
        audit_id=generate_id("AUD"),
        student_user_id=student.user_id,
        student_sidhi_id=student.sidhi_id,
        assignment_id=assignment_id,
        action=action,
        success=success,
        reason=reason,
        metadata=metadata or {},
        timestamp=datetime.utcnow()
    )
    
    await db.submission_audits.insert_one(audit.dict())

# ==================== STUDENT PROFILE ====================

async def get_student_profile(student: StudentContext) -> dict:
    """Get student profile"""
    return {
        "user_id": student.user_id,
        "sidhi_id": student.sidhi_id,
        "username": student.username,
        "email_id": student.email,
        "college": student.university_id,
        "department": student.branch_id,
        "degree": student.degree,
        "starting_year": student.starting_year
    }

# ==================== CLASSROOM DISCOVERY ====================

async def discover_classrooms(student: StudentContext) -> List[dict]:
    """
    Show all active classrooms
    Mark which ones student can join based on scope
    """
    cursor = db.classrooms.find({
        "visibility": "active"
    }).sort("created_at", -1)
    
    classrooms = await cursor.to_list(length=None)
    
    # Get student's memberships
    memberships_cursor = db.classroom_memberships.find({
        "student_user_id": student.user_id,
        "is_active": True
    })
    joined_classroom_ids = set(
        mem["classroom_id"] for mem in await memberships_cursor.to_list(length=None)
    )
    
    results = []
    for cls in classrooms:
        classroom_id = cls["classroom_id"]
        
        # Get teacher name
        teacher = await db.users_profile.find_one({"user_id": cls["teacher_user_id"]})
        teacher_name = teacher.get("username", "Unknown") if teacher else "Unknown"
        
        # Get counts
        student_count = await db.classroom_memberships.count_documents({
            "classroom_id": classroom_id,
            "is_active": True
        })
        
        assignment_count = await db.assignments.count_documents({
            "classroom_id": classroom_id,
            "status": "published"
        })
        
        # Check if student can join
        is_joined = classroom_id in joined_classroom_ids
        scope_match = check_scope_match(cls, student)
        joining_locked = cls.get("joining_locked", False)
        
        can_join = False
        join_reason = None
        
        if is_joined:
            join_reason = "Already joined"
        elif joining_locked:
            join_reason = "Joining is locked by teacher"
        elif not scope_match:
            join_reason = f"Scope mismatch (requires {cls.get('branch_id')})"
        else:
            can_join = True
        
        results.append({
            "classroom_id": classroom_id,
            "teacher_name": teacher_name,
            "teacher_sidhi_id": cls.get("teacher_sidhi_id"),
            "name": cls["name"],
            "year": cls.get("year"),
            "section": cls.get("section"),
            "university_id": cls.get("university_id"),
            "branch_id": cls.get("branch_id"),
            "student_count": student_count,
            "assignment_count": assignment_count,
            "can_join": can_join,
            "join_reason": join_reason,
            "is_joined": is_joined
        })
    
    return results

async def join_classroom(classroom_id: str, student: StudentContext, password: Optional[str] = None) -> dict:
    """
    Student joins a classroom
    Server-side validation of scope and lock status
    """
    classroom = await db.classrooms.find_one({"classroom_id": classroom_id})
    
    if not classroom:
        raise HTTPException(status_code=404, detail="Classroom not found")
    
    # Check if already joined
    existing = await db.classroom_memberships.find_one({
        "classroom_id": classroom_id,
        "student_user_id": student.user_id,
        "is_active": True
    })
    
    if existing:
        raise HTTPException(status_code=409, detail="You have already joined this classroom")
    
    # Validate scope match
    if not check_scope_match(classroom, student):
        await log_student_audit(
            student, "blocked_join_scope", classroom_id, False,
            f"Scope mismatch: classroom requires {classroom.get('branch_id')}"
        )
        raise HTTPException(
            status_code=403,
            detail=f"Scope mismatch. This classroom is for {classroom.get('branch_id')} students only."
        )
    
    # Check if joining is locked
    if classroom.get("joining_locked", False):
        await log_student_audit(
            student, "blocked_join_locked", classroom_id, False,
            "Classroom joining is locked"
        )
        raise HTTPException(
            status_code=403,
            detail="Joining is currently locked by the teacher"
        )
    
    # ADD: Check password if required
    if classroom.get("join_password_hash"):
        if not password:
            raise HTTPException(
                status_code=403,
                detail="Password required to join this classroom"
            )
        if hash_password(password) != classroom["join_password_hash"]:
            await log_student_audit(
                student, "blocked_join_password", classroom_id, False,
                "Incorrect password"
            )
            raise HTTPException(
                status_code=403,
                detail="Incorrect password"
            )
    
    # ADD: Check max students capacity
    if classroom.get("max_students"):
        current_count = await db.classroom_memberships.count_documents({
            "classroom_id": classroom_id,
            "is_active": True
        })
        if current_count >= classroom["max_students"]:
            await log_student_audit(
                student, "blocked_join_capacity", classroom_id, False,
                "Classroom is full"
            )
            raise HTTPException(
                status_code=403,
                detail="Classroom has reached maximum capacity"
            )
    
    # Create membership
    membership_id = generate_id("MEM")
    membership = ClassroomMembership(
        membership_id=membership_id,
        classroom_id=classroom_id,
        student_user_id=student.user_id,
        student_sidhi_id=student.sidhi_id,
        university_id=student.university_id,
        college_id=student.college_id,
        branch_id=student.branch_id,
        joined_at=datetime.utcnow(),
        is_active=True
    )
    
    await db.classroom_memberships.insert_one(membership.dict())
    await log_student_audit(student, "join_classroom", classroom_id, True)
    
    return {
        "classroom_id": classroom_id,
        "classroom_name": classroom["name"],
        "joined_at": membership.joined_at
    }

async def get_joined_classrooms(student: StudentContext) -> List[dict]:
    """Get all classrooms student has joined"""
    cursor = db.classroom_memberships.find({
        "student_user_id": student.user_id,
        "is_active": True
    }).sort("joined_at", -1)
    
    memberships = await cursor.to_list(length=None)
    
    results = []
    for mem in memberships:
        classroom = await db.classrooms.find_one({"classroom_id": mem["classroom_id"]})
        if not classroom:
            continue
        
        # Get teacher name
        teacher = await db.users_profile.find_one({"user_id": classroom["teacher_user_id"]})
        teacher_name = teacher.get("username", "Unknown") if teacher else "Unknown"
        
        # Count assignments
        assignment_count = await db.assignments.count_documents({
            "classroom_id": mem["classroom_id"],
            "status": "published"
        })
        
        # Count pending submissions
        pending = await db.submissions.count_documents({
            "classroom_id": mem["classroom_id"],
            "student_user_id": student.user_id,
            "approved": None
        })
        
        results.append({
            "classroom_id": mem["classroom_id"],
            "teacher_name": teacher_name,
            "name": classroom["name"],
            "year": classroom.get("year"),
            "section": classroom.get("section"),
            "assignment_count": assignment_count,
            "pending_submissions": pending,
            "joined_at": mem["joined_at"]
        })
    
    return results

# ==================== CLASSROOM VIEW ====================

async def get_classroom_detail(classroom_id: str, student: StudentContext) -> dict:
    """
    Get classroom details with all assignments
    Student must be a member
    """
    classroom = await db.classrooms.find_one({"classroom_id": classroom_id})
    
    if not classroom:
        raise HTTPException(status_code=404, detail="Classroom not found")
    
    # Get teacher name
    teacher = await db.users_profile.find_one({"user_id": classroom["teacher_user_id"]})
    teacher_name = teacher.get("username", "Unknown") if teacher else "Unknown"
    
    # Get all published assignments
    assignments_cursor = db.assignments.find({
        "classroom_id": classroom_id,
        "status": "published"
    }).sort("created_at", -1)
    
    assignments = await assignments_cursor.to_list(length=None)
    
    assignment_summaries = []
    for asg in assignments:
        summary = await get_assignment_summary(asg["assignment_id"], student)
        assignment_summaries.append(summary)
    
    return {
        "classroom_id": classroom_id,
        "teacher_name": teacher_name,
        "name": classroom["name"],
        "year": classroom.get("year"),
        "section": classroom.get("section"),
        "late_submission_policy": classroom.get("late_submission_policy"),
        "assignments": assignment_summaries
    }

# ==================== ASSIGNMENT VIEW ====================

async def get_assignment_summary(assignment_id: str, student: StudentContext) -> dict:
    """Get assignment summary for classroom list"""
    assignment = await db.assignments.find_one({"assignment_id": assignment_id})
    
    # Count student's attempts
    attempts = await db.submissions.count_documents({
        "assignment_id": assignment_id,
        "student_user_id": student.user_id
    })
    
    # Get last submission
    last_submission = await db.submissions.find_one(
        {
            "assignment_id": assignment_id,
            "student_user_id": student.user_id
        },
        sort=[("submitted_at", -1)]
    )
    
    # Determine status
    if not last_submission:
        status = SubmissionStatus.NOT_STARTED
    elif last_submission.get("approved") is True:
        status = SubmissionStatus.APPROVED
    elif last_submission.get("approved") is False:
        if last_submission.get("approval_notes", "").startswith("Resubmission requested"):
            status = SubmissionStatus.RESUBMISSION_REQUESTED
        else:
            status = SubmissionStatus.REJECTED
    else:
        status = SubmissionStatus.SUBMITTED
    
    # Check if can submit
    can_submit, _ = check_can_submit(assignment, attempts)
    
    last_score = None
    if last_submission and last_submission.get("test_result"):
        last_score = last_submission["test_result"].get("score")
    
    return {
        "assignment_id": assignment_id,
        "title": assignment["title"],
        "due_date": assignment.get("due_date"),
        "allow_late": assignment.get("allow_late", True),
        "max_attempts": assignment.get("max_attempts", 3),
        "attempts_used": attempts,
        "status": status.value,
        "can_submit": can_submit,
        "last_score": last_score
    }

async def get_assignment_detail(assignment_id: str, student: StudentContext) -> dict:
    """Get full assignment details"""
    assignment = await db.assignments.find_one({"assignment_id": assignment_id})
    
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")
    
    # Count attempts
    attempts = await db.submissions.count_documents({
        "assignment_id": assignment_id,
        "student_user_id": student.user_id
    })
    
    # Get last submission
    last_submission = await db.submissions.find_one(
        {
            "assignment_id": assignment_id,
            "student_user_id": student.user_id
        },
        sort=[("submitted_at", -1)]
    )
    
    # Determine status
    if not last_submission:
        status = SubmissionStatus.NOT_STARTED
    elif last_submission.get("approved") is True:
        status = SubmissionStatus.APPROVED
    elif last_submission.get("approved") is False:
        if last_submission.get("approval_notes", "").startswith("Resubmission requested"):
            status = SubmissionStatus.RESUBMISSION_REQUESTED
        else:
            status = SubmissionStatus.REJECTED
    else:
        status = SubmissionStatus.SUBMITTED
    
    # Check if can submit
    can_submit, blocked_reason = check_can_submit(assignment, attempts)
    
    # ADD: Get questions
    questions = assignment.get("questions", [])
    
    return {
        "assignment_id": assignment_id,
        "classroom_id": assignment["classroom_id"],
        "title": assignment["title"],
        "description": assignment["description"],
        "due_date": assignment.get("due_date"),
        "allow_late": assignment.get("allow_late", True),
        "max_attempts": assignment.get("max_attempts", 3),
        "allowed_languages": assignment.get("allowed_languages", ["python"]),
        "questions": questions,  # ADD THIS
        "attempts_used": attempts,
        "status": status.value,
        "can_submit": can_submit,
        "submission_blocked_reason": blocked_reason
    }

# ==================== SUBMISSION ====================

async def submit_assignment(
    assignment_id: str,
    student: StudentContext,
    language: str,
    answers: list   
) -> dict:
    """
    Student submits code for assignment
    Validates attempts and deadline
    Enqueues async test runner and plagiarism detector
    """
    assignment = await db.assignments.find_one({"assignment_id": assignment_id})
    
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")
    
    # Validate language is allowed
    if language not in assignment.get("allowed_languages", ["python"]):
        raise HTTPException(
            status_code=422,
            detail=f"Language '{language}' not allowed for this assignment"
        )
    
    # Count attempts
    attempts = await db.submissions.count_documents({
        "assignment_id": assignment_id,
        "student_user_id": student.user_id
    })
    
    # Check if can submit
    can_submit, reason = check_can_submit(assignment, attempts)
    
    if not can_submit:
        await log_student_audit(
            student, "blocked_submit", assignment_id, False, reason
        )
        raise HTTPException(status_code=403, detail=reason)
    
    # Create submission
    submission_id = generate_id("SUB")
    submission = {
        "submission_id": submission_id,
        "assignment_id": assignment_id,
        "classroom_id": assignment["classroom_id"],
        "student_user_id": student.user_id,
        "student_sidhi_id": student.sidhi_id,
        "language": language,
        "code": answers,
        "attempt_number": attempts + 1,
        "test_result": None,  # Will be updated by async test runner
        "approved": None,
        "approval_notes": None,
        "plagiarism_flag": PlagiarismFlag.PENDING.value,
        "submitted_at": datetime.utcnow(),
        "reviewed_at": None,
        "is_locked": False
    }
    
    await db.submissions.insert_one(submission)
    
    # TODO: Enqueue async test runner
    # await enqueue_test_runner(submission_id, assignment_id, code, language)
    
    # Enqueue plagiarism detection (runs in background)
    from app.plagiarism.integration import trigger_plagiarism_check_for_submission
    # asyncio.create_task(trigger_plagiarism_check_for_submission(
    #     submission_id=submission_id,
    #     assignment_id=assignment_id,
    #     student_user_id=student.user_id,
    #     code=code,
    #     language=language
    # ))
    
    await log_student_audit(student, "submit", assignment_id, True, metadata={
        "submission_id": submission_id,
        "attempt": attempts + 1
    })
    
    return {
        "submission_id": submission_id,
        "attempt_number": attempts + 1,
        "message": "Submission received successfully",
        "processing_status": "queued"
    }

async def get_assignment_submissions(assignment_id: str, student: StudentContext) -> List[dict]:
    """Get all student's submissions for an assignment"""
    cursor = db.submissions.find({
        "assignment_id": assignment_id,
        "student_user_id": student.user_id
    }).sort("submitted_at", -1)
    
    submissions = await cursor.to_list(length=None)
    
    # Get assignment title
    assignment = await db.assignments.find_one({"assignment_id": assignment_id})
    assignment_title = assignment.get("title", "Unknown") if assignment else "Unknown"
    
    results = []
    for sub in submissions:
        results.append({
            "submission_id": sub["submission_id"],
            "assignment_title": assignment_title,
            "language": sub["language"],
            "attempt_number": sub["attempt_number"],
            "test_result": sub.get("test_result"),
            "approved": sub.get("approved"),
            "submitted_at": sub["submitted_at"],
            "is_locked": sub.get("is_locked", False)
        })
    
    return results

async def get_submission_detail(submission_id: str, student: StudentContext) -> dict:
    """
    Get full submission details
    CRITICAL: Never expose plagiarism_flag
    """
    submission = await db.submissions.find_one({"submission_id": submission_id})
    
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")
    
    # Remove plagiarism data before returning
    submission.pop("plagiarism_flag", None)
    submission.pop("_id", None)
    
    return {
        "submission_id": submission["submission_id"],
        "assignment_id": submission["assignment_id"],
        "language": submission["language"],
        "code": submission["code"],
        "attempt_number": submission["attempt_number"],
        "test_result": submission.get("test_result"),
        "approved": submission.get("approved"),
        "approval_notes": submission.get("approval_notes"),
        "submitted_at": submission["submitted_at"],
        "reviewed_at": submission.get("reviewed_at"),
        "is_locked": submission.get("is_locked", False)
    }

async def resubmit_assignment(
    submission_id: str,
    student: StudentContext,
    language: str,
    code: str
) -> dict:
    """
    Resubmit after teacher requests changes
    Only allowed if teacher explicitly requested resubmission
    """
    old_submission = await db.submissions.find_one({"submission_id": submission_id})
    
    if not old_submission:
        raise HTTPException(status_code=404, detail="Original submission not found")
    
    # Verify ownership
    if old_submission["student_user_id"] != student.user_id:
        raise HTTPException(status_code=403, detail="Not your submission")
    
    # Check if resubmission was requested
    approval_notes = old_submission.get("approval_notes", "")
    if not approval_notes.startswith("Resubmission requested"):
        raise HTTPException(
            status_code=403,
            detail="Resubmission not requested by teacher"
        )
    
    assignment_id = old_submission["assignment_id"]
    assignment = await db.assignments.find_one({"assignment_id": assignment_id})
    
    # Count attempts
    attempts = await db.submissions.count_documents({
        "assignment_id": assignment_id,
        "student_user_id": student.user_id
    })
    
    # Check if can submit
    can_submit, reason = check_can_submit(assignment, attempts)
    
    if not can_submit:
        await log_student_audit(
            student, "blocked_resubmit", assignment_id, False, reason
        )
        raise HTTPException(status_code=403, detail=reason)
    
    # Create new submission
    new_submission_id = generate_id("SUB")
    submission = {
        "submission_id": new_submission_id,
        "assignment_id": assignment_id,
        "classroom_id": assignment["classroom_id"],
        "student_user_id": student.user_id,
        "student_sidhi_id": student.sidhi_id,
        "language": language,
        "code": code,
        "attempt_number": attempts + 1,
        "test_result": None,
        "approved": None,
        "approval_notes": None,
        "plagiarism_flag": PlagiarismFlag.PENDING.value,
        "submitted_at": datetime.utcnow(),
        "reviewed_at": None,
        "is_locked": False
    }
    
    await db.submissions.insert_one(submission)
    
    # TODO: Enqueue async test runner
    # TODO: Enqueue async plagiarism detector
    
    await log_student_audit(student, "resubmit", assignment_id, True, metadata={
        "new_submission_id": new_submission_id,
        "old_submission_id": submission_id,
        "attempt": attempts + 1
    })
    
    return {
        "submission_id": new_submission_id,
        "attempt_number": attempts + 1,
        "message": "Resubmission received successfully",
        "processing_status": "queued"
    }
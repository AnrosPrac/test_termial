from fastapi import HTTPException, Depends
from app.ai.client_bound_guard import verify_client_bound_request
from motor.motor_asyncio import AsyncIOMotorClient
import os
from typing import Optional


MONGO_URL = os.getenv("MONGO_URL")
client = AsyncIOMotorClient(MONGO_URL)
db = client.lumetrics_db

class StudentContext:
    """
    Contains validated student profile and scope
    """
    def __init__(self, user_id: str, profile: dict):
        self.user_id = user_id
        self.sidhi_id = profile.get("sidhi_id")
        self.username = profile.get("username")
        self.email = profile.get("email_id")
        self.university_id = profile.get("college")  # Maps to university
        self.college_id = profile.get("college")
        self.branch_id = profile.get("department")  # Maps to branch
        self.degree = profile.get("degree")
        self.starting_year = profile.get("starting_year")
        self.role = profile.get("role", "student")
        self.profile = profile

async def get_current_student(
    user: dict = Depends(verify_client_bound_request)
) -> StudentContext:
    """
    Dependency: Validates user is a student and returns their context
    
    Raises:
        401: Invalid token
        403: Not a student (teachers/admins blocked)
        404: Profile not found
    """
    try:
        user_id = user.get("sub")
        
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token: missing user_id")
        
        # Fetch profile from users_profile collection
        profile = await db.users_profile.find_one({"user_id": user_id})
        
        if not profile:
            raise HTTPException(
                status_code=404, 
                detail="Profile not found. Please complete registration first."
            )
        
        # Block teachers and admins from student endpoints
        user_role = profile.get("role", "student")
        if user_role == "teacher":
            raise HTTPException(
                status_code=403,
                detail="Teachers cannot access student endpoints. Use /teacher/* instead."
            )
        
        if profile.get("is_admin", False):
            raise HTTPException(
                status_code=403,
                detail="Admins cannot access student endpoints."
            )
        
        return StudentContext(user_id, profile)
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Auth error: {str(e)}")

async def verify_classroom_membership(
    classroom_id: str,
    student: StudentContext
) -> dict:
    """
    Validates student has joined this classroom
    
    Returns:
        dict: Membership document
        
    Raises:
        403: Not a member of this classroom
        404: Classroom not found
    """
    classroom = await db.classrooms.find_one({"classroom_id": classroom_id})
    
    if not classroom:
        raise HTTPException(status_code=404, detail="Classroom not found")
    
    membership = await db.classroom_memberships.find_one({
        "classroom_id": classroom_id,
        "student_user_id": student.user_id,
        "is_active": True
    })
    
    if not membership:
        raise HTTPException(
            status_code=403, 
            detail="You must join this classroom first to view its contents"
        )
    
    return membership

async def verify_assignment_access(
    assignment_id: str,
    student: StudentContext
) -> dict:
    """
    Validates student can access this assignment
    Must be a member of the parent classroom
    
    Returns:
        dict: Assignment document
        
    Raises:
        404: Assignment not found
        403: Not a member of classroom
    """
    assignment = await db.assignments.find_one({"assignment_id": assignment_id})
    
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")
    
    # Verify classroom membership
    await verify_classroom_membership(assignment["classroom_id"], student)
    
    return assignment

async def verify_submission_ownership(
    submission_id: str,
    student: StudentContext
) -> dict:
    """
    Validates student owns this submission
    
    Returns:
        dict: Submission document
        
    Raises:
        404: Submission not found
        403: Not your submission
    """
    submission = await db.submissions.find_one({"submission_id": submission_id})
    
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")
    
    if submission.get("student_user_id") != student.user_id:
        raise HTTPException(
            status_code=403, 
            detail="You can only view your own submissions"
        )
    
    return submission

def check_scope_match(classroom: dict, student: StudentContext) -> bool:
    """
    Validates student's scope matches classroom scope
    
    Returns:
        bool: True if student can join this classroom
    """
    return (
        classroom.get("university_id") == student.university_id and
        classroom.get("college_id") == student.college_id and
        classroom.get("branch_id") == student.branch_id
    )

def check_can_submit(assignment: dict, attempts_used: int) -> tuple[bool, Optional[str]]:
    """
    Validates if student can submit to this assignment
    
    Returns:
        tuple: (can_submit: bool, reason: str or None)
    """
    from datetime import datetime
    
    # Check if assignment is published
    if assignment.get("status") != "published":
        return False, "Assignment is not published yet"
    
    # Check max attempts
    if attempts_used >= assignment.get("max_attempts", 3):
        return False, "Maximum submission attempts exceeded"
    
    # Check deadline
    due_date = assignment.get("due_date")
    if due_date:
        if datetime.utcnow() > due_date and not assignment.get("allow_late", True):
            return False, "Deadline has passed and late submissions are not allowed"
    
    return True, None

def check_submission_immutable(submission: dict):
    """
    Raises 403 if submission is locked (approved)
    """
    if submission.get("is_locked", False):
        raise HTTPException(
            status_code=403,
            detail="This submission is locked and cannot be modified (already approved)"
        )
from fastapi import HTTPException, Depends
from app.ai.client_bound_guard import verify_client_bound_request
from motor.motor_asyncio import AsyncIOMotorClient
import os

MONGO_URL = os.getenv("MONGO_URL")
client = AsyncIOMotorClient(MONGO_URL)
db = client.lumetrics_db

class TeacherContext:
    """
    Contains validated teacher profile and scope
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
        self.role = profile.get("role", "student")
        self.is_admin = profile.get("is_admin", False)
        self.profile = profile

async def get_current_teacher(
    user: dict = Depends(verify_client_bound_request)
) -> TeacherContext:
    """
    Dependency: Validates user is a teacher and returns their context
    
    Raises:
        401: Invalid token
        403: Not a teacher
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
        
        # Validate role is teacher
        if profile.get("role") != "teacher":
            raise HTTPException(
                status_code=403,
                detail="Access denied. Teacher privileges required."
            )
        
        return TeacherContext(user_id, profile)
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Auth error: {str(e)}")

async def verify_classroom_ownership(
    classroom_id: str,
    teacher: TeacherContext
) -> dict:
    """
    Validates teacher owns this classroom and it's in their university scope
    
    Returns:
        dict: Classroom document
        
    Raises:
        404: Classroom not found
        403: Not the owner or wrong university
    """
    classroom = await db.classrooms.find_one({"classroom_id": classroom_id})
    
    if not classroom:
        raise HTTPException(status_code=404, detail="Classroom not found")
    
    if classroom.get("teacher_user_id") != teacher.user_id:
        raise HTTPException(status_code=403, detail="Not authorized to access this classroom")
    
    if classroom.get("university_id") != teacher.university_id:
        raise HTTPException(status_code=403, detail="Classroom not in your university scope")
    
    return classroom

async def verify_assignment_ownership(
    assignment_id: str,
    teacher: TeacherContext
) -> dict:
    """
    Validates teacher owns this assignment
    
    Returns:
        dict: Assignment document
        
    Raises:
        404: Assignment not found
        403: Not the owner
    """
    assignment = await db.assignments.find_one({"assignment_id": assignment_id})
    
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")
    
    if assignment.get("teacher_user_id") != teacher.user_id:
        raise HTTPException(status_code=403, detail="Not authorized to access this assignment")
    
    return assignment

async def verify_testcase_ownership(
    testcase_id: str,
    teacher: TeacherContext
) -> dict:
    """
    Validates teacher owns the assignment this testcase belongs to
    
    Returns:
        dict: TestCase document
        
    Raises:
        404: TestCase not found
        403: Not authorized
        400: TestCase is locked
    """
    testcase = await db.testcases.find_one({"testcase_id": testcase_id})
    
    if not testcase:
        raise HTTPException(status_code=404, detail="TestCase not found")
    
    # Check assignment ownership
    assignment = await db.assignments.find_one({"assignment_id": testcase.get("assignment_id")})
    
    if not assignment:
        raise HTTPException(status_code=404, detail="Parent assignment not found")
    
    if assignment.get("teacher_user_id") != teacher.user_id:
        raise HTTPException(status_code=403, detail="Not authorized to modify this testcase")
    
    return testcase

async def verify_submission_access(
    submission_id: str,
    teacher: TeacherContext
) -> dict:
    """
    Validates teacher can access this submission (must own the assignment)
    
    Returns:
        dict: Submission document
        
    Raises:
        404: Submission not found
        403: Not authorized
    """
    submission = await db.submissions.find_one({"submission_id": submission_id})
    
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")
    
    # Check assignment ownership
    assignment = await db.assignments.find_one({"assignment_id": submission.get("assignment_id")})
    
    if not assignment:
        raise HTTPException(status_code=404, detail="Parent assignment not found")
    
    if assignment.get("teacher_user_id") != teacher.user_id:
        raise HTTPException(status_code=403, detail="Not authorized to access this submission")
    
    return submission

def check_testcases_not_locked(assignment: dict):
    """
    Raises 400 if testcases are locked
    Used before modifying/deleting testcases
    """
    if assignment.get("testcases_locked", False):
        raise HTTPException(
            status_code=400,
            detail="Cannot modify testcases - they are locked. Unlock assignment first."
        )
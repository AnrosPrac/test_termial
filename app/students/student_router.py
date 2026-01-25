from fastapi import APIRouter, Depends, HTTPException
from typing import List
from app.students.student_permissions import (
    get_current_student,
    verify_classroom_membership,
    verify_assignment_access,
    verify_submission_ownership,
    StudentContext
)
from app.students.student_schemas import (
    StudentProfile,
    ClassroomDiscovery, JoinedClassroom, ClassroomDetail,
    AssignmentDetail, AssignmentSummary,
    SubmissionCreate, ResubmissionCreate,
    SubmissionResponse, SubmissionListItem, SubmitSuccess
)
from app.students import student_service as service

router = APIRouter(prefix="/student", tags=["Student Portal"])

# ==================== STUDENT PROFILE ====================

@router.get("/me", response_model=StudentProfile)
async def get_my_profile(student: StudentContext = Depends(get_current_student)):
    """
    Get current student's profile
    """
    return await service.get_student_profile(student)

# ==================== CLASSROOM DISCOVERY ====================

@router.get("/classrooms", response_model=List[ClassroomDiscovery])
async def discover_classrooms(student: StudentContext = Depends(get_current_student)):
    """
    Browse all active classrooms
    Shows which ones student can join based on university/college/branch scope
    
    Server enforces:
    - Scope matching (university + college + branch)
    - Join lock status
    - Already joined status
    """
    return await service.discover_classrooms(student)

@router.post("/classrooms/{classroom_id}/join")
async def join_classroom(
    classroom_id: str,
    student: StudentContext = Depends(get_current_student)
):
    """
    Join a classroom
    
    Server-side validations:
    - Scope must match (403 if mismatch)
    - Joining must not be locked (403 if locked)
    - Cannot join twice (409 if already joined)
    """
    result = await service.join_classroom(classroom_id, student)
    return {
        "status": "success",
        "message": f"Successfully joined {result['classroom_name']}",
        "data": result
    }

@router.get("/classrooms/joined", response_model=List[JoinedClassroom])
async def get_joined_classrooms(student: StudentContext = Depends(get_current_student)):
    """
    Get all classrooms student has joined
    """
    return await service.get_joined_classrooms(student)

# ==================== CLASSROOM VIEW ====================

@router.get("/classrooms/{classroom_id}", response_model=ClassroomDetail)
async def get_classroom_detail(
    classroom_id: str,
    student: StudentContext = Depends(get_current_student)
):
    """
    Get classroom details with all assignments
    
    Student must be a member (403 if not joined)
    Shows only published assignments
    """
    await verify_classroom_membership(classroom_id, student)
    return await service.get_classroom_detail(classroom_id, student)

# ==================== ASSIGNMENT VIEW ====================

@router.get("/assignments/{assignment_id}", response_model=AssignmentDetail)
async def get_assignment_detail(
    assignment_id: str,
    student: StudentContext = Depends(get_current_student)
):
    """
    Get full assignment details
    
    Shows:
    - Description
    - Allowed languages
    - Due date and late policy
    - Attempts used/remaining
    - Submission eligibility
    
    Does NOT show:
    - Test cases (hidden)
    - Plagiarism data (never exposed)
    - Other students' submissions
    """
    await verify_assignment_access(assignment_id, student)
    return await service.get_assignment_detail(assignment_id, student)

# ==================== SUBMISSION FLOW ====================

@router.post("/assignments/{assignment_id}/submit", response_model=SubmitSuccess)
async def submit_assignment(
    assignment_id: str,
    data: SubmissionCreate,
    student: StudentContext = Depends(get_current_student)
):
    """
    Submit code for an assignment
    
    Server validates:
    - Student is member of classroom (403 if not)
    - Assignment is published (403 if draft/closed)
    - Language is allowed (422 if invalid)
    - Attempts not exceeded (403 if max reached)
    - Deadline allows submission (403 if late and not allowed)
    
    Process:
    1. Create submission record
    2. Enqueue async test runner
    3. Enqueue async plagiarism detector
    4. Return immediately with "queued" status
    
    Student gets results when:
    - Test runner completes → test_result updated
    - Plagiarism detector completes → flag updated (teacher only)
    - Teacher reviews → approval status updated
    """
    await verify_assignment_access(assignment_id, student)
    
    return await service.submit_assignment(
        assignment_id,
        student,
        data.language,
        data.code
    )

@router.get("/assignments/{assignment_id}/submissions", response_model=List[SubmissionListItem])
async def get_assignment_submissions(
    assignment_id: str,
    student: StudentContext = Depends(get_current_student)
):
    """
    Get all student's submissions for this assignment
    Shows submission history with test results
    
    Does NOT show:
    - Full code (use GET /submissions/{id} for that)
    - Plagiarism data (never exposed to students)
    """
    await verify_assignment_access(assignment_id, student)
    return await service.get_assignment_submissions(assignment_id, student)

@router.get("/submissions/{submission_id}", response_model=SubmissionResponse)
async def get_submission_detail(
    submission_id: str,
    student: StudentContext = Depends(get_current_student)
):
    """
    Get full submission details including code
    
    Shows:
    - Full code
    - Test results (pass/fail counts, score)
    - Approval status
    - Teacher feedback (if reviewed)
    
    Never shows:
    - Plagiarism similarity score
    - Plagiarism flag (green/yellow/red)
    - Comparison with other submissions
    """
    await verify_submission_ownership(submission_id, student)
    return await service.get_submission_detail(submission_id, student)

@router.post("/submissions/{submission_id}/resubmit", response_model=SubmitSuccess)
async def resubmit_assignment(
    submission_id: str,
    data: ResubmissionCreate,
    student: StudentContext = Depends(get_current_student)
):
    """
    Resubmit after teacher requests changes
    
    Only allowed when:
    - Teacher explicitly requested resubmission (403 otherwise)
    - Attempts not exceeded (403 if max reached)
    - Deadline allows (403 if late and not allowed)
    
    Creates a new submission (original remains unchanged)
    """
    await verify_submission_ownership(submission_id, student)
    
    return await service.resubmit_assignment(
        submission_id,
        student,
        data.language,
        data.code
    )
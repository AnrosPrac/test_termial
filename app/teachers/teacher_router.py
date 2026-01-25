from fastapi import APIRouter, Depends, HTTPException
from typing import List
from app.teachers.teacher_permissions import (
    get_current_teacher, 
    verify_classroom_ownership,
    verify_assignment_ownership,
    verify_testcase_ownership,
    verify_submission_access,
    check_testcases_not_locked,
    TeacherContext
)
from app.teachers.teacher_schemas import (
    TeacherProfile, TeacherProfileUpdate,
    ClassroomCreate, ClassroomUpdate, ClassroomResponse,
    AssignmentCreate, AssignmentUpdate, AssignmentResponse,
    TestCaseCreate, TestCaseUpdate, TestCaseResponse,
    SubmissionResponse, SubmissionApproval, SubmissionRejection, TestResultOverride,
    PlagiarismSummary, PlagiarismDetail, PlagiarismReview,
    ClassroomAnalytics, AssignmentScorecard
)
from app.teachers import teacher_service as service
from fastapi.responses import StreamingResponse
import io
from app.teachers.teacher_models import SourceType


router = APIRouter(prefix="/teacher", tags=["Teacher Management"])

# ==================== TEACHER PROFILE ====================

@router.get("/me", response_model=TeacherProfile)
async def get_my_profile(teacher: TeacherContext = Depends(get_current_teacher)):
    """
    Get current teacher's profile with metadata
    """
    return await service.get_teacher_profile(teacher)

@router.patch("/me", response_model=TeacherProfile)
async def update_my_profile(
    data: TeacherProfileUpdate,
    teacher: TeacherContext = Depends(get_current_teacher)
):
    """
    Update teacher's designation and bio
    """
    return await service.update_teacher_profile(teacher, data.dict(exclude_none=True))

# ==================== CLASSROOM MANAGEMENT ====================

@router.post("/classrooms", response_model=ClassroomResponse, status_code=201)
async def create_classroom(
    data: ClassroomCreate,
    teacher: TeacherContext = Depends(get_current_teacher)
):
    """
    Create a new classroom within teacher's university scope
    """
    return await service.create_classroom(teacher, data.dict())

@router.get("/classrooms", response_model=List[ClassroomResponse])
async def get_my_classrooms(teacher: TeacherContext = Depends(get_current_teacher)):
    """
    Get all classrooms created by this teacher
    """
    return await service.get_teacher_classrooms(teacher)

@router.get("/classrooms/{classroom_id}", response_model=ClassroomResponse)
async def get_classroom_detail(
    classroom_id: str,
    teacher: TeacherContext = Depends(get_current_teacher)
):
    """
    Get detailed classroom information with stats
    """
    await verify_classroom_ownership(classroom_id, teacher)
    return await service.get_classroom_with_stats(classroom_id)

@router.patch("/classrooms/{classroom_id}", response_model=ClassroomResponse)
async def update_classroom(
    classroom_id: str,
    data: ClassroomUpdate,
    teacher: TeacherContext = Depends(get_current_teacher)
):
    """
    Update classroom settings
    """
    await verify_classroom_ownership(classroom_id, teacher)
    return await service.update_classroom(classroom_id, teacher, data.dict(exclude_none=True))

@router.delete("/classrooms/{classroom_id}", status_code=204)
async def delete_classroom(
    classroom_id: str,
    teacher: TeacherContext = Depends(get_current_teacher)
):
    """
    Archive a classroom (soft delete)
    """
    await verify_classroom_ownership(classroom_id, teacher)
    await service.delete_classroom(classroom_id, teacher)
    return None

# ==================== CLASSROOM MEMBERSHIP ====================

@router.get("/classrooms/{classroom_id}/students")
async def get_classroom_students(
    classroom_id: str,
    teacher: TeacherContext = Depends(get_current_teacher)
):
    """
    Get all students enrolled in this classroom
    """
    await verify_classroom_ownership(classroom_id, teacher)
    students = await service.get_classroom_students(classroom_id)
    
    return {
        "classroom_id": classroom_id,
        "total_students": len(students),
        "students": students
    }

@router.post("/classrooms/{classroom_id}/lock-joining")
async def lock_classroom_joining(
    classroom_id: str,
    teacher: TeacherContext = Depends(get_current_teacher)
):
    """
    Prevent new students from joining this classroom
    """
    await verify_classroom_ownership(classroom_id, teacher)
    await service.lock_classroom_joining(classroom_id, teacher)
    
    return {"status": "success", "message": "Classroom joining locked"}

@router.post("/classrooms/{classroom_id}/unlock-joining")
async def unlock_classroom_joining(
    classroom_id: str,
    teacher: TeacherContext = Depends(get_current_teacher)
):
    """
    Allow students to join this classroom again
    """
    await verify_classroom_ownership(classroom_id, teacher)
    await service.unlock_classroom_joining(classroom_id, teacher)
    
    return {"status": "success", "message": "Classroom joining unlocked"}

# ==================== ASSIGNMENT MANAGEMENT ====================

# teacher_router.py - MODIFY existing endpoint

@router.post("/classrooms/{classroom_id}/assignments", response_model=AssignmentResponse, status_code=201)
async def create_assignment(
    classroom_id: str,
    data: AssignmentCreate,
    teacher: TeacherContext = Depends(get_current_teacher),
    auto_generate_testcases: bool = True  # NEW: Query parameter to enable/disable
):
    """
    Create a new assignment in this classroom
    
    Query Parameters:
    - auto_generate_testcases: If true (default), automatically generate test cases 
      using AI for all questions. Set to false to create test cases manually.
    
    Examples:
    - POST /teacher/classrooms/CLS_123/assignments (auto-generates test cases)
    - POST /teacher/classrooms/CLS_123/assignments?auto_generate_testcases=false (manual test cases)
    """
    await verify_classroom_ownership(classroom_id, teacher)
    return await service.create_assignment(
        classroom_id, 
        teacher, 
        data.dict(),
        auto_generate_testcases=auto_generate_testcases
    )
@router.get("/assignments/{assignment_id}/questions/{question_id}/testcases", response_model=List[TestCaseResponse])
async def get_question_testcases(
    assignment_id: str,
    question_id: str,
    teacher: TeacherContext = Depends(get_current_teacher)
):
    """
    Get all test cases for a specific question
    ✅ NEW: Query test cases by question within assignment
    """
    await verify_assignment_ownership(assignment_id, teacher)
    return await service.get_question_testcases(assignment_id, question_id)

@router.get("/classrooms/{classroom_id}/assignments", response_model=List[AssignmentResponse])
async def get_classroom_assignments(
    classroom_id: str,
    teacher: TeacherContext = Depends(get_current_teacher)
):
    """
    Get all assignments in this classroom
    """
    await verify_classroom_ownership(classroom_id, teacher)
    return await service.get_classroom_assignments(classroom_id)

@router.get("/assignments/{assignment_id}", response_model=AssignmentResponse)
async def get_assignment_detail(
    assignment_id: str,
    teacher: TeacherContext = Depends(get_current_teacher)
):
    """
    Get detailed assignment information with stats
    """
    await verify_assignment_ownership(assignment_id, teacher)
    return await service.get_assignment_with_stats(assignment_id)

@router.patch("/assignments/{assignment_id}", response_model=AssignmentResponse)
async def update_assignment(
    assignment_id: str,
    data: AssignmentUpdate,
    teacher: TeacherContext = Depends(get_current_teacher)
):
    """
    Update assignment settings
    """
    await verify_assignment_ownership(assignment_id, teacher)
    return await service.update_assignment(assignment_id, teacher, data.dict(exclude_none=True))

@router.delete("/assignments/{assignment_id}", status_code=204)
async def delete_assignment(
    assignment_id: str,
    teacher: TeacherContext = Depends(get_current_teacher)
):
    """
    Close an assignment (soft delete)
    """
    await verify_assignment_ownership(assignment_id, teacher)
    await service.delete_assignment(assignment_id, teacher)
    return None

# ==================== TEST CASE MANAGEMENT ====================

@router.get("/assignments/{assignment_id}/testcases", response_model=List[TestCaseResponse])
async def get_assignment_testcases(
    assignment_id: str,
    teacher: TeacherContext = Depends(get_current_teacher)
):
    """
    Get all test cases for an assignment
    """
    await verify_assignment_ownership(assignment_id, teacher)
    return await service.get_assignment_testcases(assignment_id)

@router.post("/assignments/{assignment_id}/testcases", response_model=TestCaseResponse, status_code=201)
async def create_testcase(
    assignment_id: str,
    data: TestCaseCreate,
    teacher: TeacherContext = Depends(get_current_teacher)
):
    """
    Create a new test case (only if not locked)
    """
    assignment = await verify_assignment_ownership(assignment_id, teacher)
    check_testcases_not_locked(assignment)
    
    return await service.create_testcase(assignment_id, teacher, data.dict())

@router.patch("/testcases/{testcase_id}", response_model=TestCaseResponse)
async def update_testcase(
    testcase_id: str,
    data: TestCaseUpdate,
    teacher: TeacherContext = Depends(get_current_teacher)
):
    """
    Update a test case (only if not locked)
    """
    testcase = await verify_testcase_ownership(testcase_id, teacher)
    
    if testcase.get("locked", False):
        raise HTTPException(status_code=400, detail="Cannot modify locked test case")
    
    return await service.update_testcase(testcase_id, teacher, data.dict(exclude_none=True))

@router.delete("/testcases/{testcase_id}", status_code=204)
async def delete_testcase(
    testcase_id: str,
    teacher: TeacherContext = Depends(get_current_teacher)
):
    """
    Delete a test case (only if not locked)
    """
    testcase = await verify_testcase_ownership(testcase_id, teacher)
    
    if testcase.get("locked", False):
        raise HTTPException(status_code=400, detail="Cannot delete locked test case")
    
    await service.delete_testcase(testcase_id, teacher)
    return None

@router.post("/assignments/{assignment_id}/testcases/lock")
async def lock_assignment_testcases(
    assignment_id: str,
    teacher: TeacherContext = Depends(get_current_teacher)
):
    """
    Lock all test cases - makes them immutable
    Once locked, cannot be unlocked
    """
    await verify_assignment_ownership(assignment_id, teacher)
    await service.lock_assignment_testcases(assignment_id, teacher)
    
    return {"status": "success", "message": "Test cases locked. They are now immutable."}

# ==================== SUBMISSION MANAGEMENT ====================

@router.get("/assignments/{assignment_id}/submissions", response_model=List[SubmissionResponse])
async def get_assignment_submissions(
    assignment_id: str,
    teacher: TeacherContext = Depends(get_current_teacher)
):
    """
    Get all submissions for an assignment
    """
    await verify_assignment_ownership(assignment_id, teacher)
    return await service.get_assignment_submissions(assignment_id)

@router.get("/submissions/{submission_id}", response_model=SubmissionResponse)
async def get_submission_detail(
    submission_id: str,
    teacher: TeacherContext = Depends(get_current_teacher)
):
    """
    Get full submission details including code
    """
    await verify_submission_access(submission_id, teacher)
    return await service.get_submission_detail(submission_id)

@router.post("/submissions/{submission_id}/approve")
async def approve_submission(
    submission_id: str,
    data: SubmissionApproval,
    teacher: TeacherContext = Depends(get_current_teacher)
):
    """
    Approve a student's submission
    """
    await verify_submission_access(submission_id, teacher)
    await service.approve_submission(submission_id, teacher, data.notes)
    
    return {"status": "success", "message": "Submission approved"}

@router.post("/submissions/{submission_id}/reject")
async def reject_submission(
    submission_id: str,
    data: SubmissionRejection,
    teacher: TeacherContext = Depends(get_current_teacher)
):
    """
    Reject a student's submission with feedback
    """
    await verify_submission_access(submission_id, teacher)
    await service.reject_submission(submission_id, teacher, data.notes)
    
    return {"status": "success", "message": "Submission rejected"}

@router.post("/submissions/{submission_id}/request-resubmission")
async def request_resubmission(
    submission_id: str,
    data: SubmissionRejection,
    teacher: TeacherContext = Depends(get_current_teacher)
):
    """
    Request student to resubmit with feedback
    """
    await verify_submission_access(submission_id, teacher)
    await service.request_resubmission(submission_id, teacher, data.notes)
    
    return {"status": "success", "message": "Resubmission requested"}

@router.post("/submissions/{submission_id}/override-test-result")
async def override_test_result(
    submission_id: str,
    data: TestResultOverride,
    teacher: TeacherContext = Depends(get_current_teacher)
):
    """
    Manually override test results
    Use when automated testing is incorrect
    """
    await verify_submission_access(submission_id, teacher)
    
    override_data = {
        "passed": data.passed,
        "failed": data.failed,
        "score": data.score,
        "reason": data.reason,
        "overridden_by": teacher.sidhi_id
    }
    
    await service.override_test_result(submission_id, teacher, override_data)
    
    return {"status": "success", "message": "Test result overridden"}

# ==================== PLAGIARISM DETECTION ====================

@router.get("/assignments/{assignment_id}/plagiarism-summary", response_model=PlagiarismSummary)
async def get_plagiarism_summary(
    assignment_id: str,
    teacher: TeacherContext = Depends(get_current_teacher)
):
    """
    Get plagiarism detection summary for an assignment
    Shows count of green/yellow/red flags
    """
    await verify_assignment_ownership(assignment_id, teacher)
    
    from app.plagiarism.integration import get_assignment_plagiarism_summary
    return await get_assignment_plagiarism_summary(assignment_id)

@router.post("/assignments/{assignment_id}/run-plagiarism-check")
async def run_plagiarism_check(
    assignment_id: str,
    teacher: TeacherContext = Depends(get_current_teacher)
):
    """
    Manually trigger plagiarism detection for all submissions
    Runs in background, returns immediately
    """
    await verify_assignment_ownership(assignment_id, teacher)
    
    from app.plagiarism.integration import batch_check_assignment_submissions
    import asyncio
    
    # Run in background
    asyncio.create_task(batch_check_assignment_submissions(
        assignment_id=assignment_id,
        teacher_user_id=teacher.user_id
    ))
    
    return {
        "status": "success",
        "message": "Plagiarism check started in background. Check summary in a few minutes."
    }

@router.get("/plagiarism/{pair_id}", response_model=PlagiarismDetail)
async def get_plagiarism_detail(
    pair_id: str,
    teacher: TeacherContext = Depends(get_current_teacher)
):
    """
    Get detailed plagiarism report for a specific pair
    Shows code similarity and matched segments
    """
    result = await service.get_plagiarism_detail(pair_id)
    
    # Verify teacher owns the parent assignment
    await verify_assignment_ownership(result["assignment_id"], teacher)
    
    return result

@router.post("/plagiarism/{pair_id}/mark-reviewed")
async def mark_plagiarism_reviewed(
    pair_id: str,
    data: PlagiarismReview,
    teacher: TeacherContext = Depends(get_current_teacher)
):
    """
    Mark a plagiarism case as reviewed
    No auto-punishment - teachers decide action separately
    """
    result = await service.get_plagiarism_detail(pair_id)
    await verify_assignment_ownership(result["assignment_id"], teacher)
    
    await service.mark_plagiarism_reviewed(pair_id, teacher, data.notes)
    
    return {"status": "success", "message": "Plagiarism case marked as reviewed"}

# ==================== ANALYTICS ====================

@router.get("/classrooms/{classroom_id}/analytics", response_model=ClassroomAnalytics)
async def get_classroom_analytics(
    classroom_id: str,
    teacher: TeacherContext = Depends(get_current_teacher)
):
    """
    Get performance analytics for a classroom
    """
    await verify_classroom_ownership(classroom_id, teacher)
    return await service.get_classroom_analytics(classroom_id)

@router.get("/assignments/{assignment_id}/scorecard", response_model=AssignmentScorecard)
async def get_assignment_scorecard(
    assignment_id: str,
    teacher: TeacherContext = Depends(get_current_teacher)
):
    """
    Get detailed scorecard for an assignment
    Shows approval stats, scores, and submission timing
    """
    await verify_assignment_ownership(assignment_id, teacher)
    return await service.get_assignment_scorecard(assignment_id)

# ==================== ACADEMIC OUTPUTS ====================

@router.post("/assignments/{assignment_id}/generate-record-notes")
async def generate_record_notes(
    assignment_id: str,
    teacher: TeacherContext = Depends(get_current_teacher)
):
    """
    Generate academic record notes (implementation pending)
    """
    await verify_assignment_ownership(assignment_id, teacher)
    
    return {
        "status": "success",
        "message": "Record notes generation queued",
        "assignment_id": assignment_id
    }

@router.get("/assignments/{assignment_id}/record-notes")
async def get_record_notes(
    assignment_id: str,
    teacher: TeacherContext = Depends(get_current_teacher)
):
    """
    Retrieve generated record notes (implementation pending)
    """
    await verify_assignment_ownership(assignment_id, teacher)
    
    return {
        "status": "success",
        "assignment_id": assignment_id,
        "record_notes": []
    }


# Add to imports
from fastapi.responses import StreamingResponse
import io

# ADD AI generation endpoint
@router.post("/assignments/generate-with-ai", response_model=AssignmentResponse, status_code=201)
async def generate_assignment_with_ai(
    classroom_id: str,
    data: AssignmentCreate,
    teacher: TeacherContext = Depends(get_current_teacher)
):
    """
    Generate assignment questions and test cases using AI
    Requires: source_type=AI, ai_topic, ai_num_questions
    """
    await verify_classroom_ownership(classroom_id, teacher)
    
    if data.source_type != SourceType.AI:
        raise HTTPException(
            status_code=400,
            detail="Source type must be 'ai' for AI generation"
        )
    
    if not data.ai_topic:
        raise HTTPException(
            status_code=400,
            detail="ai_topic is required for AI generation"
        )
    
    if not data.ai_num_questions:
        raise HTTPException(
            status_code=400,
            detail="ai_num_questions is required (1-15)"
        )
    
    # Generate questions and test cases
    ai_result = await service.generate_assignment_with_ai(
        classroom_id=classroom_id,
        teacher=teacher,
        topic=data.ai_topic,
        num_questions=data.ai_num_questions,
        allowed_languages=data.allowed_languages
    )
    
    # Create assignment with generated questions
    assignment_data = data.dict(exclude={'ai_topic', 'ai_num_questions'})
    assignment_data['questions'] = ai_result['questions']
    
    assignment = await service.create_assignment(classroom_id, teacher, assignment_data)
    assignment_id = assignment['assignment_id']
    
    # Create test cases
    for tc_data in ai_result['testcases']:
        result = await service.create_testcase(assignment_id, teacher, tc_data)
        if result is None:
            continue
    
    await service.log_audit(
        teacher,
        "generate_assignment_ai",
        "assignment",
        assignment_id,
        {
            "topic": data.ai_topic,
            "questions": ai_result['questions_generated'],
            "testcases": ai_result['testcases_generated']
        }
    )
    
    return await service.get_assignment_with_stats(assignment_id)


# MODIFY export endpoint to actually return CSV
@router.get("/assignments/{assignment_id}/export-csv")
async def export_assignment_csv(
    assignment_id: str,
    teacher: TeacherContext = Depends(get_current_teacher)
):
    """
    Export all submissions as CSV file
    """
    await verify_assignment_ownership(assignment_id, teacher)
    
    csv_content = await service.export_assignment_submissions_csv(assignment_id)
    
    # Return as downloadable file
    return StreamingResponse(
        io.StringIO(csv_content),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=assignment_{assignment_id}_submissions.csv"
        }
    )
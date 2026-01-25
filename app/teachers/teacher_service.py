from datetime import datetime
from typing import List, Optional
import secrets
from app.teachers.teacher_permissions import TeacherContext, db
from app.teachers.teacher_models import (
    Classroom, Assignment, TestCase, Submission,
    ClassroomVisibility, AssignmentStatus
)
from app.teachers.common_audit import log_audit
from fastapi import HTTPException
import hashlib
import csv
import io
from app.ai.gemini_core import run_gemini
import json

def generate_id(prefix: str) -> str:
    """Generate unique ID with prefix"""
    return f"{prefix}_{secrets.token_hex(8).upper()}"

def hash_password(password: str) -> str:
    """Hash classroom password using SHA256"""
    return hashlib.sha256(password.encode()).hexdigest()

# ==================== TEACHER PROFILE ====================

async def get_teacher_profile(teacher: TeacherContext) -> dict:
    """Get teacher profile with metadata"""
    meta = await db.teachers_meta.find_one({"user_id": teacher.user_id})
    
    return {
        "user_id": teacher.user_id,
        "sidhi_id": teacher.sidhi_id,
        "username": teacher.username,
        "email_id": teacher.email,
        "college": teacher.university_id,
        "department": teacher.branch_id,
        "degree": teacher.degree,
        "designation": meta.get("designation") if meta else None,
        "bio": meta.get("bio") if meta else None,
        "created_at": teacher.profile.get("created_at", datetime.utcnow())
    }

async def update_teacher_profile(teacher: TeacherContext, data: dict) -> dict:
    """Update teacher metadata"""
    await db.teachers_meta.update_one(
        {"user_id": teacher.user_id},
        {
            "$set": {
                **data,
                "updated_at": datetime.utcnow()
            },
            "$setOnInsert": {
                "user_id": teacher.user_id,
                "sidhi_id": teacher.sidhi_id,
                "created_at": datetime.utcnow()
            }
        },
        upsert=True
    )
    
    return await get_teacher_profile(teacher)

# ==================== CLASSROOM MANAGEMENT ====================

async def create_classroom(teacher: TeacherContext, data: dict) -> dict:
    """Create a new classroom"""
    classroom_id = generate_id("CLS")
    join_password_hash = None
    if data.get('join_password'):
        join_password_hash = hash_password(data.pop('join_password'))
    
    classroom = Classroom(
        classroom_id=classroom_id,
        teacher_user_id=teacher.user_id,
        teacher_sidhi_id=teacher.sidhi_id,
        university_id=teacher.university_id,
        college_id=teacher.college_id,
        branch_id=teacher.branch_id,
        join_password_hash=join_password_hash,
        **data
    )
    
    await db.classrooms.insert_one(classroom.dict())
    await log_audit(teacher, "create_classroom", "classroom", classroom_id)
    
    return await get_classroom_with_stats(classroom_id)

async def get_teacher_classrooms(teacher: TeacherContext) -> List[dict]:
    """Get all classrooms owned by teacher"""
    cursor = db.classrooms.find({
        "teacher_user_id": teacher.user_id,
        "university_id": teacher.university_id
    }).sort("created_at", -1)
    
    classrooms = await cursor.to_list(length=None)
    
    results = []
    for cls in classrooms:
        results.append(await get_classroom_with_stats(cls["classroom_id"]))
    
    return results

async def get_classroom_with_stats(classroom_id: str) -> dict:
    """Get classroom with student and assignment counts"""
    classroom = await db.classrooms.find_one({"classroom_id": classroom_id})
    
    if not classroom:
        raise HTTPException(status_code=404, detail="Classroom not found")
    
    student_count = await db.classroom_memberships.count_documents({
        "classroom_id": classroom_id,
        "is_active": True
    })
    
    assignment_count = await db.assignments.count_documents({
        "classroom_id": classroom_id
    })
    
    classroom["student_count"] = student_count
    classroom["assignment_count"] = assignment_count
    classroom.pop("_id", None)
    
    return classroom

async def update_classroom(classroom_id: str, teacher: TeacherContext, data: dict) -> dict:
    """Update classroom details"""
    update_data = {k: v for k, v in data.items() if v is not None}
    update_data["updated_at"] = datetime.utcnow()
    
    await db.classrooms.update_one(
        {"classroom_id": classroom_id},
        {"$set": update_data}
    )
    
    await log_audit(teacher, "update_classroom", "classroom", classroom_id, update_data)
    
    return await get_classroom_with_stats(classroom_id)

async def delete_classroom(classroom_id: str, teacher: TeacherContext):
    """Delete classroom (soft delete by archiving)"""
    await db.classrooms.update_one(
        {"classroom_id": classroom_id},
        {"$set": {
            "visibility": ClassroomVisibility.ARCHIVED.value,
            "updated_at": datetime.utcnow()
        }}
    )
    
    await log_audit(teacher, "delete_classroom", "classroom", classroom_id)

async def lock_classroom_joining(classroom_id: str, teacher: TeacherContext):
    """Prevent new students from joining"""
    await db.classrooms.update_one(
        {"classroom_id": classroom_id},
        {"$set": {"joining_locked": True, "updated_at": datetime.utcnow()}}
    )
    
    await log_audit(teacher, "lock_joining", "classroom", classroom_id)

async def unlock_classroom_joining(classroom_id: str, teacher: TeacherContext):
    """Allow students to join again"""
    await db.classrooms.update_one(
        {"classroom_id": classroom_id},
        {"$set": {"joining_locked": False, "updated_at": datetime.utcnow()}}
    )
    
    await log_audit(teacher, "unlock_joining", "classroom", classroom_id)

async def get_classroom_students(classroom_id: str) -> List[dict]:
    """Get all students in a classroom"""
    cursor = db.classroom_memberships.find({
        "classroom_id": classroom_id,
        "is_active": True
    }).sort("joined_at", -1)
    
    memberships = await cursor.to_list(length=None)
    
    students = []
    for mem in memberships:
        profile = await db.users_profile.find_one({"user_id": mem["student_user_id"]})
        if profile:
            students.append({
                "user_id": mem["student_user_id"],
                "sidhi_id": mem["student_sidhi_id"],
                "username": profile.get("username"),
                "email_id": profile.get("email_id"),
                "department": profile.get("department"),
                "joined_at": mem["joined_at"]
            })
    
    return students

# ==================== ASSIGNMENT MANAGEMENT ====================
# teacher_service.py - ADD THIS NEW FUNCTION

async def generate_testcases_for_question(
    question_prompt: str,
    language: str,
    num_testcases: int = 3
) -> List[dict]:
    """
    Generate test cases for any question using AI
    Returns list of test case dictionaries
    
    Args:
        question_prompt: The coding problem description
        language: Programming language (python, java, cpp, etc.)
        num_testcases: Number of test cases to generate (default 3)
    
    Returns:
        List of test case dicts with input_data, expected_output, weight, is_hidden
    """
    tc_prompt = f"""Generate {num_testcases} test cases for this coding problem:

Problem: {question_prompt}
Language: {language}

IMPORTANT: Return ONLY valid JSON in this exact format, nothing else:
{{
  "testcases": [
    {{
      "input_data": "sample input as string",
      "expected_output": "expected output as string",
      "weight": 1.0,
      "is_hidden": false
    }}
  ]
}}

Requirements:
1. Generate {num_testcases} test cases
2. First {num_testcases - 1} should be visible (is_hidden: false)
3. Last test case should be hidden (is_hidden: true)
4. Cover edge cases, normal cases, and boundary conditions
5. Input and output should be strings that can be directly used in code execution
6. Make sure test cases are diverse and comprehensive

Generate {num_testcases} test cases now."""

    try:
        tc_response = run_gemini(tc_prompt)
        
        # Clean response (remove markdown code blocks)
        clean_tc = tc_response.strip()
        if clean_tc.startswith('```'):
            lines = clean_tc.split('\n')
            clean_tc = '\n'.join(lines[1:-1])
        
        # Parse JSON
        tc_data = json.loads(clean_tc)
        testcases = tc_data.get('testcases', [])
        
        if not testcases:
            raise ValueError("No test cases generated")
        
        # Ensure last one is hidden
        if len(testcases) >= num_testcases:
            testcases[-1]['is_hidden'] = True
        
        return testcases
        
    except json.JSONDecodeError as e:
        # Fallback: create basic test cases
        print(f"[WARNING] AI generated invalid JSON for test cases: {e}")
        return [
            {
                "input_data": "# Test case 1 - Please update manually",
                "expected_output": "# Expected output",
                "weight": 1.0,
                "is_hidden": False
            }
        ]
    except Exception as e:
        print(f"[ERROR] Failed to generate test cases: {e}")
        return []
# teacher_service.py - REPLACE existing create_assignment function

async def create_assignment(
    classroom_id: str, 
    teacher: TeacherContext, 
    data: dict,
    auto_generate_testcases: bool = True
) -> dict:
    """
    Create a new assignment in classroom
    """
    assignment_id = generate_id("ASG")
    
    assignment = Assignment(
        assignment_id=assignment_id,
        classroom_id=classroom_id,
        teacher_user_id=teacher.user_id,
        teacher_sidhi_id=teacher.sidhi_id,
        **data
    )
    
    # Save assignment to database
    await db.assignments.insert_one(assignment.dict())
    await log_audit(teacher, "create_assignment", "assignment", assignment_id)
    
    # Auto-generate test cases for all questions
    if auto_generate_testcases and data.get('questions'):
        print(f"[INFO] Auto-generating test cases for {len(data['questions'])} questions...")
        
        testcases_created = 0

        for question in data['questions']:
            question_id = question.get('question_id')
            
            if not question_id:
                print("[WARNING] Question missing question_id, skipping test case generation")
                continue
            
            try:
                # Generate 3 test cases per question using AI
                testcases = await generate_testcases_for_question(
                    question_prompt=question.get('prompt', ''),
                    language=question.get('language', 'python'),
                    num_testcases=3
                )
                
                # Create each test case in database with question_id mapping
                for tc_data in testcases:
                    testcase = TestCase(
                        testcase_id=generate_id("TC"),
                        assignment_id=assignment_id,
                        question_id=question_id,  # ✅ CRITICAL
                        **tc_data
                    )
                    await db.testcases.insert_one(testcase.dict())
                    testcases_created += 1
                
                print(f"[INFO] Created {len(testcases)} test cases for question: {question_id}")
                
            except Exception as e:
                print(f"[WARNING] Failed to generate test cases for question {question_id}: {e}")
                continue
        
        # Log test case generation (ONCE, not per question)
        await log_audit(
            teacher, 
            "auto_generate_testcases", 
            "assignment", 
            assignment_id,
            {
                "testcases_created": testcases_created,
                "questions": len(data['questions'])
            }
        )
        
        print(f"[SUCCESS] Auto-generated {testcases_created} test cases for assignment {assignment_id}")
    
    return await get_assignment_with_stats(assignment_id)


async def get_classroom_assignments(classroom_id: str) -> List[dict]:
    """Get all assignments in a classroom"""
    cursor = db.assignments.find({
        "classroom_id": classroom_id
    }).sort("created_at", -1)
    
    assignments = await cursor.to_list(length=None)
    
    results = []
    for asg in assignments:
        results.append(await get_assignment_with_stats(asg["assignment_id"]))
    
    return results

async def get_assignment_with_stats(assignment_id: str) -> dict:
    """Get assignment with testcase and submission counts"""
    assignment = await db.assignments.find_one({"assignment_id": assignment_id})
    
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")
    
    testcase_count = await db.testcases.count_documents({"assignment_id": assignment_id})
    submission_count = await db.submissions.count_documents({"assignment_id": assignment_id})
    
    assignment["testcase_count"] = testcase_count
    assignment["submission_count"] = submission_count
    assignment.pop("_id", None)
    
    return assignment
async def generate_assignment_with_ai(
    classroom_id: str,
    teacher: TeacherContext,
    topic: str,
    num_questions: int,
    allowed_languages: List[str]
) -> dict:
    """
    Generate assignment questions and test cases using AI
    """
    # Build prompt for question generation
    prompt = f"""You are an expert programming instructor. Generate {num_questions} coding questions on the topic: "{topic}".

For each question:
1. Write a clear problem statement
2. Choose ONE language from: {', '.join(allowed_languages)}
3. Assign difficulty-appropriate marks (5-20)
4. Make questions progressively harder

Return ONLY valid JSON in this exact format:
{{
  "questions": [
    {{
      "prompt": "Write a function that...",
      "language": "python",
      "marks": 10
    }}
  ]
}}

Generate {num_questions} questions now."""

    # Call Gemini
    response = run_gemini(prompt)
    
    # Parse response
    try:
        # Remove markdown code blocks if present
        clean_response = response.strip()
        if clean_response.startswith('```'):
            clean_response = '\n'.join(clean_response.split('\n')[1:-1])
        
        questions_data = json.loads(clean_response)
        questions = questions_data.get('questions', [])
        
        if not questions:
            raise ValueError("No questions generated")
        
        # Limit to requested number
        questions = questions[:num_questions]
        
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=500,
            detail="AI generated invalid response. Please try again."
        )
    
    # Generate question IDs
    for q in questions:
        q['question_id'] = generate_id("Q")
    
    # Now generate test cases for each question
    testcases_generated = 0
    all_testcases = []
    
    for question in questions:
        question_id = question.get('question_id')  # ✅ CRITICAL ADDITION
        
        if not question_id:
            print("[WARNING] Question missing question_id, skipping test case generation")
            continue

        tc_prompt = f"""Generate 3 test cases for this coding problem:

Problem: {question['prompt']}
Language: {question['language']}

Return ONLY valid JSON:
{{
  "testcases": [
    {{
      "input_data": "sample input",
      "expected_output": "sample output",
      "weight": 1.0,
      "is_hidden": false
    }}
  ]
}}

Generate 3 test cases (2 visible, 1 hidden)."""

        try:
            tc_response = run_gemini(tc_prompt)
            clean_tc = tc_response.strip()
            if clean_tc.startswith('```'):
                clean_tc = '\n'.join(clean_tc.split('\n')[1:-1])
            
            tc_data = json.loads(clean_tc)
            testcases = tc_data.get('testcases', [])
            
            # Mark last one as hidden
            if len(testcases) >= 3:
                testcases[2]['is_hidden'] = True
            
            # ✅ CRITICAL FIX: attach question_id to every testcase
            for tc in testcases:
                tc['question_id'] = question_id
            
            all_testcases.extend(testcases)
            testcases_generated += len(testcases)
            
        except Exception as e:
            print(f"[WARNING] Testcase generation failed for question {question_id}: {e}")
            continue
    
    return {
        "questions": questions,
        "testcases": all_testcases,
        "questions_generated": len(questions),
        "testcases_generated": testcases_generated
    }



# ADD CSV export function
async def export_assignment_submissions_csv(assignment_id: str) -> str:
    """
    Export all submissions for an assignment as CSV
    Returns CSV content as string
    """
    assignment = await db.assignments.find_one({"assignment_id": assignment_id})
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")
    
    submissions = await db.submissions.find({
        "assignment_id": assignment_id
    }).sort("student_user_id", 1).to_list(length=None)
    
    # Build CSV in memory
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Headers
    writer.writerow([
        'Student ID',
        'Student SIDHI ID',
        'Student Name',
        'Attempt Number',
        'Score',
        'Passed Tests',
        'Failed Tests',
        'Approved',
        'Submission Time',
        'Reviewed Time',
        'Notes'
    ])
    
    # Fetch student profiles
    for sub in submissions:
        profile = await db.users_profile.find_one({"user_id": sub["student_user_id"]})
        
        test_result = sub.get("test_result", {})
        override_result = sub.get("teacher_override_result")
        
        # Use override if exists
        if override_result:
            score = override_result.get("score", 0)
            passed = override_result.get("passed", 0)
            failed = override_result.get("failed", 0)
        else:
            score = test_result.get("score", 0)
            passed = test_result.get("passed", 0)
            failed = test_result.get("failed", 0)
        
        approved_status = "Approved" if sub.get("approved") is True else \
                         "Rejected" if sub.get("approved") is False else \
                         "Pending"
        
        writer.writerow([
            sub["student_user_id"],
            sub["student_sidhi_id"],
            profile.get("username", "Unknown") if profile else "Unknown",
            sub.get("attempt_number", 1),
            f"{score:.2f}",
            passed,
            failed,
            approved_status,
            sub["submitted_at"].strftime("%Y-%m-%d %H:%M:%S"),
            sub["reviewed_at"].strftime("%Y-%m-%d %H:%M:%S") if sub.get("reviewed_at") else "",
            sub.get("approval_notes", "")
        ])
    
    csv_content = output.getvalue()
    output.close()
    
    return csv_content
async def update_assignment(assignment_id: str, teacher: TeacherContext, data: dict) -> dict:
    """Update assignment details"""
    update_data = {k: v for k, v in data.items() if v is not None}
    update_data["updated_at"] = datetime.utcnow()
    
    await db.assignments.update_one(
        {"assignment_id": assignment_id},
        {"$set": update_data}
    )
    
    await log_audit(teacher, "update_assignment", "assignment", assignment_id, update_data)
    
    return await get_assignment_with_stats(assignment_id)

async def delete_assignment(assignment_id: str, teacher: TeacherContext):
    """Delete assignment (soft delete by setting status to closed)"""
    await db.assignments.update_one(
        {"assignment_id": assignment_id},
        {"$set": {
            "status": AssignmentStatus.CLOSED.value,
            "updated_at": datetime.utcnow()
        }}
    )
    
    await log_audit(teacher, "delete_assignment", "assignment", assignment_id)

# ==================== TEST CASE MANAGEMENT ====================

async def create_testcase(assignment_id: str, teacher: TeacherContext, data: dict) -> dict:
    """Create a new test case"""

    # ✅ Extract and validate question_id
    question_id = data.get("question_id")
    if not question_id:
        raise HTTPException(
            status_code=400,
            detail="question_id is required when creating test case"
        )

    # ✅ Validate assignment exists
    assignment = await db.assignments.find_one({"assignment_id": assignment_id})
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")

    # ✅ Validate question belongs to assignment
    if not any(q.get("question_id") == question_id for q in assignment.get("questions", [])):
        raise HTTPException(
            status_code=400,
            detail=f"Question {question_id} does not exist in assignment {assignment_id}"
        )

    # ✅ CRITICAL FIX: remove question_id from data to avoid duplication
    data = data.copy()
    data.pop("question_id", None)

    testcase = TestCase(
        testcase_id=generate_id("TC"),
        assignment_id=assignment_id,
        question_id=question_id,  # ✅ SINGLE SOURCE OF TRUTH
        **data
    )

    await db.testcases.insert_one(testcase.dict())
    await log_audit(teacher, "create_testcase", "testcase", testcase.testcase_id)

    result = testcase.dict()
    result.pop("_id", None)
    return result

async def get_assignment_testcases(assignment_id: str) -> List[dict]:
    """Get all test cases for an assignment"""
    cursor = db.testcases.find({"assignment_id": assignment_id}).sort("created_at", 1)
    testcases = await cursor.to_list(length=None)
    
    for tc in testcases:
        tc.pop("_id", None)
    
    return testcases
async def get_question_testcases(assignment_id: str, question_id: str) -> List[dict]:
    """
    Get all test cases for a specific question
    ✅ NEW: Query test cases by question_id
    """
    cursor = db.testcases.find({
        "assignment_id": assignment_id,
        "question_id": question_id
    }).sort("created_at", 1)
    
    testcases = await cursor.to_list(length=None)
    
    for tc in testcases:
        tc.pop("_id", None)
    
    return testcases

async def update_testcase(testcase_id: str, teacher: TeacherContext, data: dict) -> dict:
    """Update test case (only if not locked)"""
    update_data = {k: v for k, v in data.items() if v is not None}
    update_data["updated_at"] = datetime.utcnow()
    
    await db.testcases.update_one(
        {"testcase_id": testcase_id},
        {"$set": update_data}
    )
    
    await log_audit(teacher, "update_testcase", "testcase", testcase_id, update_data)
    
    testcase = await db.testcases.find_one({"testcase_id": testcase_id})
    testcase.pop("_id", None)
    return testcase

async def delete_testcase(testcase_id: str, teacher: TeacherContext):
    """Delete test case (only if not locked)"""
    await db.testcases.delete_one({"testcase_id": testcase_id})
    await log_audit(teacher, "delete_testcase", "testcase", testcase_id)

async def lock_assignment_testcases(assignment_id: str, teacher: TeacherContext):
    """Lock all testcases - makes them immutable"""
    await db.assignments.update_one(
        {"assignment_id": assignment_id},
        {"$set": {"testcases_locked": True, "updated_at": datetime.utcnow()}}
    )
    
    await db.testcases.update_many(
        {"assignment_id": assignment_id},
        {"$set": {"locked": True}}
    )
    
    await log_audit(teacher, "lock_testcases", "assignment", assignment_id)

# ==================== SUBMISSION MANAGEMENT ====================

async def get_assignment_submissions(assignment_id: str) -> List[dict]:
    """Get all submissions for an assignment"""
    cursor = db.submissions.find({"assignment_id": assignment_id}).sort("submitted_at", -1)
    submissions = await cursor.to_list(length=None)
    
    results = []
    for sub in submissions:
        profile = await db.users_profile.find_one({"user_id": sub["student_user_id"]})
        sub["student_username"] = profile.get("username", "Unknown") if profile else "Unknown"
        sub.pop("_id", None)
        sub.pop("code", None)  # Don't send full code in list view
        
        # Include plagiarism flag for teacher visibility
        sub["plagiarism_flag"] = sub.get("plagiarism_flag", "pending")
        sub["plagiarism_checked"] = sub.get("plagiarism_checked_at") is not None
        
        results.append(sub)
    
    return results

async def get_submission_detail(submission_id: str) -> dict:
    """Get full submission details including code"""
    submission = await db.submissions.find_one({"submission_id": submission_id})
    
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")
    
    profile = await db.users_profile.find_one({"user_id": submission["student_user_id"]})
    submission["student_username"] = profile.get("username", "Unknown") if profile else "Unknown"
    submission.pop("_id", None)
    
    return submission

async def approve_submission(submission_id: str, teacher: TeacherContext, notes: Optional[str]):
    """Approve a submission"""
    await db.submissions.update_one(
        {"submission_id": submission_id},
        {"$set": {
            "approved": True,
            "approval_notes": notes,
            "reviewed_at": datetime.utcnow()
        }}
    )
    
    await log_audit(teacher, "approve_submission", "submission", submission_id, {"notes": notes})

async def reject_submission(submission_id: str, teacher: TeacherContext, notes: str):
    """Reject a submission"""
    await db.submissions.update_one(
        {"submission_id": submission_id},
        {"$set": {
            "approved": False,
            "approval_notes": notes,
            "reviewed_at": datetime.utcnow()
        }}
    )
    
    await log_audit(teacher, "reject_submission", "submission", submission_id, {"notes": notes})

async def request_resubmission(submission_id: str, teacher: TeacherContext, notes: str):
    """Request student to resubmit"""
    submission = await db.submissions.find_one({"submission_id": submission_id})
    
    await db.submissions.update_one(
        {"submission_id": submission_id},
        {"$set": {
            "approved": None,
            "approval_notes": f"Resubmission requested: {notes}",
            "reviewed_at": datetime.utcnow()
        }}
    )
    
    await log_audit(teacher, "request_resubmission", "submission", submission_id, {"notes": notes})

async def override_test_result(
    submission_id: str, 
    teacher: TeacherContext, 
    override_data: dict
):
    """Teacher manually overrides test results"""
    await db.submissions.update_one(
        {"submission_id": submission_id},
        {"$set": {
            "teacher_override_result": override_data,
            "reviewed_at": datetime.utcnow()
        }}
    )
    
    await log_audit(teacher, "override_test_result", "submission", submission_id, override_data)

# ==================== PLAGIARISM ====================

async def get_plagiarism_summary(assignment_id: str) -> dict:
    """Get plagiarism summary for an assignment"""
    cursor = db.plagiarism_results.find({"assignment_id": assignment_id})
    results = await cursor.to_list(length=None)
    
    summary = {
        "assignment_id": assignment_id,
        "total_pairs": len(results),
        "green_flags": sum(1 for r in results if r["flag"] == "green"),
        "yellow_flags": sum(1 for r in results if r["flag"] == "yellow"),
        "red_flags": sum(1 for r in results if r["flag"] == "red"),
        "unreviewed_count": sum(1 for r in results if not r["reviewed_by_teacher"])
    }
    
    return summary

async def get_plagiarism_detail(pair_id: str) -> dict:
    """Get detailed plagiarism report for a pair"""
    result = await db.plagiarism_results.find_one({"pair_id": pair_id})
    
    if not result:
        raise HTTPException(status_code=404, detail="Plagiarism result not found")
    
    # Fetch student usernames
    student1 = await db.users_profile.find_one({"user_id": result["student_1_user_id"]})
    student2 = await db.users_profile.find_one({"user_id": result["student_2_user_id"]})
    
    result["student_1_username"] = student1.get("username", "Unknown") if student1 else "Unknown"
    result["student_2_username"] = student2.get("username", "Unknown") if student2 else "Unknown"
    result.pop("_id", None)
    
    return result

async def mark_plagiarism_reviewed(pair_id: str, teacher: TeacherContext, notes: Optional[str]):
    """Mark plagiarism case as reviewed"""
    await db.plagiarism_results.update_one(
        {"pair_id": pair_id},
        {"$set": {
            "reviewed_by_teacher": True,
            "teacher_notes": notes,
            "reviewed_at": datetime.utcnow()
        }}
    )
    
    await log_audit(teacher, "review_plagiarism", "plagiarism", pair_id, {"notes": notes})

# ==================== ANALYTICS ====================

async def get_classroom_analytics(classroom_id: str) -> dict:
    """Get classroom performance analytics"""
    total_students = await db.classroom_memberships.count_documents({
        "classroom_id": classroom_id,
        "is_active": True
    })
    
    total_assignments = await db.assignments.count_documents({"classroom_id": classroom_id})
    
    submissions = await db.submissions.find({"classroom_id": classroom_id}).to_list(length=None)
    
    if submissions:
        total_submissions = len(submissions)
        avg_score = sum(
            s.get("test_result", {}).get("score", 0) 
            for s in submissions if s.get("test_result")
        ) / total_submissions if total_submissions > 0 else 0
        
        unique_students = len(set(s["student_user_id"] for s in submissions))
        avg_submission_rate = (unique_students / total_students * 100) if total_students > 0 else 0
    else:
        avg_score = 0
        avg_submission_rate = 0
        unique_students = 0
    
    return {
        "classroom_id": classroom_id,
        "total_students": total_students,
        "total_assignments": total_assignments,
        "avg_submission_rate": round(avg_submission_rate, 2),
        "avg_score": round(avg_score, 2),
        "active_students": unique_students
    }

async def get_assignment_scorecard(assignment_id: str) -> dict:
    """Get detailed scorecard for an assignment"""
    assignment = await db.assignments.find_one({"assignment_id": assignment_id})
    submissions = await db.submissions.find({"assignment_id": assignment_id}).to_list(length=None)
    
    approved = sum(1 for s in submissions if s.get("approved") is True)
    pending = sum(1 for s in submissions if s.get("approved") is None)
    rejected = sum(1 for s in submissions if s.get("approved") is False)
    
    scores = [s.get("test_result", {}).get("score", 0) for s in submissions if s.get("test_result")]
    avg_score = sum(scores) / len(scores) if scores else 0
    
    attempts = [s.get("attempt_number", 1) for s in submissions]
    avg_attempts = sum(attempts) / len(attempts) if attempts else 0
    
    due_date = assignment.get("due_date")
    on_time = 0
    late = 0
    
    if due_date:
        on_time = sum(1 for s in submissions if s["submitted_at"] <= due_date)
        late = sum(1 for s in submissions if s["submitted_at"] > due_date)
    
    return {
        "assignment_id": assignment_id,
        "total_submissions": len(submissions),
        "approved_submissions": approved,
        "pending_submissions": pending,
        "rejected_submissions": rejected,
        "avg_score": round(avg_score, 2),
        "avg_attempts": round(avg_attempts, 2),
        "on_time_submissions": on_time,
        "late_submissions": late
    }
"""
Test Runner Service for Student Assignment Submissions
Integrates with existing judge service from coding_practice.py
✅ FIXED: Handles partial submissions, tracks per-question results, better error handling
"""

import httpx
import os
from typing import Dict, List
from motor.motor_asyncio import AsyncIOMotorClient
import asyncio

# Reuse your existing judge service configuration
JUDGE_API_URL = os.getenv("JUDGE_API_URL")
JUDGE_API_KEY = os.getenv("JUDGE_API_KEY")

MONGO_URL = os.getenv("MONGO_URL")
client = AsyncIOMotorClient(MONGO_URL)
db = client.lumetrics_db


async def run_assignment_tests(
    submission_id: str,
    assignment_id: str,
    student_answers: List[Dict],  # [{"question_id": "Q_XXX", "code": "..."}]
    language: str
):
    """
    Execute test cases for student assignment submission.
    This runs asynchronously after submission is created.
    
    ✅ NEW FEATURES:
    - Handles partial submissions (only tests answered questions)
    - Tracks per-question results
    - Better error handling and logging
    - Validates test case availability
    
    Args:
        submission_id: The submission ID (SUB_XXXXXX)
        assignment_id: The assignment ID (ASG_XXXXXX)
        student_answers: List of question-code pairs
        language: Programming language (python, java, cpp, c, javascript)
    
    Process:
        1. Fetch all test cases for the assignment (grouped by question_id)
        2. For each ANSWERED question, run student's code against test cases
        3. Aggregate results (total passed, total failed, score)
        4. Update submission record with detailed test_result
    """
    
    try:
        print(f"[TEST RUNNER] Starting tests for submission {submission_id}")
        print(f"[TEST RUNNER] Language: {language}, Questions answered: {len(student_answers)}")
        
        # Step 1: Get all test cases for this assignment
        testcases_cursor = db.testcases.find({
            "assignment_id": assignment_id
        })
        all_testcases = await testcases_cursor.to_list(length=None)
        
        if not all_testcases:
            print(f"[WARNING] No test cases found for assignment {assignment_id}")
            await db.submissions.update_one(
                {"submission_id": submission_id},
                {"$set": {
                    "test_result": {
                        "passed": 0,
                        "failed": 0,
                        "score": 0,
                        "total_tests": 0,
                        "error": "No test cases available for this assignment"
                    }
                }}
            )
            return
        
        # Step 2: Group test cases by question_id
        testcases_by_question = {}
        for tc in all_testcases:
            q_id = tc.get("question_id")
            if q_id not in testcases_by_question:
                testcases_by_question[q_id] = []
            testcases_by_question[q_id].append(tc)
        
        # ✅ NEW: Get list of answered question IDs
        answered_question_ids = {ans.get("question_id") for ans in student_answers}
        print(f"[TEST RUNNER] Answered questions: {answered_question_ids}")
        
        # ✅ NEW: Track per-question results for detailed feedback
        question_results = {}
        
        # Step 3: Run tests ONLY for answered questions
        total_passed = 0
        total_failed = 0
        total_weight = 0
        weighted_score = 0
        
        for answer in student_answers:
            question_id = answer.get("question_id")
            student_code = answer.get("code")
            
            # ✅ VALIDATION: Skip if no code provided
            if not student_code or not student_code.strip():
                print(f"[WARNING] Empty code for question {question_id}, skipping")
                question_results[question_id] = {
                    "status": "skipped",
                    "reason": "Empty code submission",
                    "passed": 0,
                    "failed": 0,
                    "score": 0
                }
                continue
            
            # ✅ VALIDATION: Check if test cases exist for this question
            if question_id not in testcases_by_question:
                print(f"[WARNING] No test cases for question {question_id}")
                question_results[question_id] = {
                    "status": "no_tests",
                    "reason": "No test cases available for this question",
                    "passed": 0,
                    "failed": 0,
                    "score": 0
                }
                continue
            
            question_testcases = testcases_by_question[question_id]
            print(f"[TEST RUNNER] Running {len(question_testcases)} tests for question {question_id}")
            
            # Prepare test cases for judge service
            judge_testcases = [
                {
                    "input": tc["input_data"],
                    "output": tc["expected_output"]
                }
                for tc in question_testcases
            ]
            
            # Submit to judge service (reuse coding_practice logic)
            try:
                async with httpx.AsyncClient() as http_client:
                    response = await http_client.post(
                        f"{JUDGE_API_URL}/judge",
                        json={
                            "language": language,
                            "sourceCode": student_code,
                            "testcases": judge_testcases
                        },
                        headers={
                            "X-API-Key": JUDGE_API_KEY,
                            "Content-Type": "application/json"
                        },
                        timeout=30.0  # Increased timeout for assignment tests
                    )
                    
                    if response.status_code != 200:
                        print(f"[ERROR] Judge service failed for question {question_id}: HTTP {response.status_code}")
                        error_msg = response.text[:200] if hasattr(response, 'text') else "Unknown error"
                        
                        question_results[question_id] = {
                            "status": "error",
                            "reason": f"Judge service error: {error_msg}",
                            "passed": 0,
                            "failed": len(question_testcases),
                            "score": 0
                        }
                        total_failed += len(question_testcases)
                        continue
                    
                    judge_response = response.json()
                    task_id = judge_response.get("task_id")
                    
                    if not task_id:
                        print(f"[ERROR] No task_id received for question {question_id}")
                        question_results[question_id] = {
                            "status": "error",
                            "reason": "Judge service did not return task_id",
                            "passed": 0,
                            "failed": len(question_testcases),
                            "score": 0
                        }
                        total_failed += len(question_testcases)
                        continue
                    
                    # Poll for result (max 30 seconds)
                    poll_success = False
                    for attempt in range(30):
                        await asyncio.sleep(1)
                        
                        try:
                            status_response = await http_client.get(
                                f"{JUDGE_API_URL}/status/{task_id}",
                                headers={"X-API-Key": JUDGE_API_KEY},
                                timeout=5.0
                            )
                            
                            if status_response.status_code == 200:
                                status_data = status_response.json()
                                
                                if status_data.get("status") in ["completed", "failed"]:
                                    result = status_data.get("result", {})
                                    passed = result.get("passed", 0)
                                    total_tests = result.get("total", len(question_testcases))
                                    failed = total_tests - passed
                                    
                                    total_passed += passed
                                    total_failed += failed
                                    
                                    # Calculate weighted score for this question
                                    question_weight_sum = sum(tc.get("weight", 1.0) for tc in question_testcases)
                                    total_weight += question_weight_sum
                                    
                                    # Pro-rata scoring based on passed tests
                                    if len(question_testcases) > 0:
                                        question_score = (passed / len(question_testcases)) * 100
                                        weighted_score += (passed / len(question_testcases)) * question_weight_sum
                                    else:
                                        question_score = 0
                                    
                                    # ✅ NEW: Store per-question result
                                    question_results[question_id] = {
                                        "status": "completed",
                                        "passed": passed,
                                        "failed": failed,
                                        "total": total_tests,
                                        "score": round(question_score, 2)
                                    }
                                    
                                    print(f"[SUCCESS] Question {question_id}: {passed}/{total_tests} passed ({question_score:.2f}%)")
                                    poll_success = True
                                    break
                        
                        except Exception as poll_error:
                            print(f"[WARNING] Polling error for question {question_id}, attempt {attempt+1}: {poll_error}")
                            continue
                    
                    if not poll_success:
                        print(f"[ERROR] Polling timeout for question {question_id}")
                        question_results[question_id] = {
                            "status": "timeout",
                            "reason": "Test execution timed out",
                            "passed": 0,
                            "failed": len(question_testcases),
                            "score": 0
                        }
                        total_failed += len(question_testcases)
                    
            except httpx.RequestError as e:
                print(f"[ERROR] Network error for question {question_id}: {e}")
                question_results[question_id] = {
                    "status": "error",
                    "reason": f"Network error: {str(e)[:100]}",
                    "passed": 0,
                    "failed": len(question_testcases),
                    "score": 0
                }
                total_failed += len(question_testcases)
                
            except Exception as e:
                print(f"[ERROR] Test execution failed for question {question_id}: {e}")
                import traceback
                traceback.print_exc()
                
                question_results[question_id] = {
                    "status": "error",
                    "reason": f"Unexpected error: {str(e)[:100]}",
                    "passed": 0,
                    "failed": len(question_testcases),
                    "score": 0
                }
                total_failed += len(question_testcases)
        
        # Step 4: Calculate final score
        if total_weight > 0:
            final_score = (weighted_score / total_weight) * 100
        else:
            # Fallback: simple percentage
            total_tests = total_passed + total_failed
            final_score = 0 if total_tests == 0 else (total_passed / total_tests) * 100
        
        # ✅ NEW: More detailed test result
        test_result = {
            "passed": total_passed,
            "failed": total_failed,
            "score": round(final_score, 2),
            "total_tests": total_passed + total_failed,
            "questions_tested": len(answered_question_ids),
            "question_results": question_results,  # ✅ Per-question breakdown
            "is_complete": final_score == 100.0,  # ✅ Flag for auto-approval
            "tested_at": db.submissions.find_one({"submission_id": submission_id})
        }
        
        # Step 5: Update submission with results
        await db.submissions.update_one(
            {"submission_id": submission_id},
            {"$set": {"test_result": test_result}}
        )
        
        print(f"[SUCCESS] Tests completed for {submission_id}")
        print(f"[RESULT] Score: {final_score:.2f}% | Passed: {total_passed}/{total_passed + total_failed}")
        
    except Exception as e:
        print(f"[ERROR] Test runner failed catastrophically: {e}")
        import traceback
        traceback.print_exc()
        
        # Update with error
        try:
            await db.submissions.update_one(
                {"submission_id": submission_id},
                {"$set": {
                    "test_result": {
                        "passed": 0,
                        "failed": 0,
                        "score": 0,
                        "total_tests": 0,
                        "is_complete": False,
                        "error": f"Test runner crashed: {str(e)[:200]}"
                    }
                }}
            )
        except Exception as update_error:
            print(f"[CRITICAL] Could not update submission with error: {update_error}")
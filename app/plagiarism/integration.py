# app/plagiarism/integration.py
"""
Plagiarism Detection Integration
Connects student submissions with automatic plagiarism checking
"""

from typing import List, Optional
from datetime import datetime
from motor.motor_asyncio import AsyncIOMotorClient
import os
from app.plagiarism.plagiarism_main import PlagiarismDetector, BatchDetector
import asyncio

MONGO_URL = os.getenv("MONGO_URL")
client = AsyncIOMotorClient(MONGO_URL)
db = client.lumetrics_db

async def trigger_plagiarism_check_for_submission(
    submission_id: str,
    assignment_id: str,
    student_user_id: str,
    code: str,
    language: str
):
    """
    Trigger plagiarism detection after a student submits code
    Compares with all existing submissions for the same assignment
    
    Background task - does not block submission response
    """
    try:
        # Get all other submissions for this assignment
        cursor = db.submissions.find({
            "assignment_id": assignment_id,
            "submission_id": {"$ne": submission_id},  # Exclude current submission
            "approved": {"$ne": False}  # Exclude rejected submissions
        })
        
        other_submissions = await cursor.to_list(length=None)
        
        if not other_submissions:
            # No other submissions to compare against
            await db.submissions.update_one(
                {"submission_id": submission_id},
                {"$set": {"plagiarism_flag": "green"}}  # Default to green
            )
            return
        
        detector = PlagiarismDetector()
        
        # Compare with each existing submission
        max_similarity = 0.0
        highest_similarity_pair = None
        
        for other_sub in other_submissions:
            # Skip if different language
            if other_sub["language"] != language:
                continue
            
            # Skip submissions from same student (self-plagiarism handled separately)
            if other_sub["student_user_id"] == student_user_id:
                continue
            
            # Run plagiarism detection
            report = await detector.compare_submissions(
                code1=code,
                code2=other_sub["code"],
                language=language,
                submission1_id=submission_id,
                submission2_id=other_sub["submission_id"]
            )
            
            # Track highest similarity
            if report.overall_similarity > max_similarity:
                max_similarity = report.overall_similarity
                highest_similarity_pair = {
                    "other_submission_id": other_sub["submission_id"],
                    "other_student_id": other_sub["student_user_id"],
                    "similarity_score": report.overall_similarity,
                    "flag_color": report.flag_color.value,
                    "is_ai_generated": report.is_likely_ai_generated,
                    "ai_probability": report.ai_probability,
                    "detected_at": datetime.utcnow()
                }
            
            # Store plagiarism result for teacher review
            if report.overall_similarity >= 0.30:  # Only store significant matches
                await store_plagiarism_result(report, assignment_id)
        
        # Update submission with plagiarism flag
        flag = determine_flag_from_similarity(max_similarity)
        
        update_data = {
            "plagiarism_flag": flag,
            "plagiarism_checked_at": datetime.utcnow()
        }
        
        if highest_similarity_pair:
            update_data["highest_similarity_match"] = highest_similarity_pair
        
        await db.submissions.update_one(
            {"submission_id": submission_id},
            {"$set": update_data}
        )
        
    except Exception as e:
        print(f"Error in plagiarism check: {str(e)}")
        # Don't fail submission if plagiarism check fails
        await db.submissions.update_one(
            {"submission_id": submission_id},
            {"$set": {
                "plagiarism_flag": "pending",
                "plagiarism_error": str(e)
            }}
        )

async def store_plagiarism_result(report, assignment_id: str):
    """
    Store plagiarism detection result for teacher review
    """
    from app.plagiarism.plagiarism_main import FlagColor
    
    pair_id = f"PLAG_{report.submission1_id}_{report.submission2_id}"
    
    # Get student details
    sub1 = await db.submissions.find_one({"submission_id": report.submission1_id})
    sub2 = await db.submissions.find_one({"submission_id": report.submission2_id})
    
    if not sub1 or not sub2:
        return
    
    plagiarism_doc = {
        "pair_id": pair_id,
        "assignment_id": assignment_id,
        "submission_1_id": report.submission1_id,
        "submission_2_id": report.submission2_id,
        "student_1_user_id": sub1["student_user_id"],
        "student_2_user_id": sub2["student_user_id"],
        "similarity_score": report.overall_similarity,
        "flag": report.flag_color.value,
        "details": {
            "layer_results": [
                {
                    "layer": layer.layer_name,
                    "score": layer.similarity_score,
                    "confidence": layer.confidence
                }
                for layer in report.layer_results
            ],
            "is_ai_generated": report.is_likely_ai_generated,
            "ai_probability": report.ai_probability,
            "recommendations": report.recommendations
        },
        "reviewed_by_teacher": False,
        "teacher_notes": None,
        "detected_at": datetime.utcnow(),
        "reviewed_at": None
    }
    
    # Upsert to avoid duplicates
    await db.plagiarism_results.update_one(
        {"pair_id": pair_id},
        {"$set": plagiarism_doc},
        upsert=True
    )

def determine_flag_from_similarity(similarity: float) -> str:
    """
    Convert similarity score to flag color
    """
    if similarity < 0.30:
        return "green"
    elif similarity < 0.60:
        return "yellow"
    else:
        return "red"

async def batch_check_assignment_submissions(
    assignment_id: str,
    teacher_user_id: str
) -> dict:
    """
    Run plagiarism detection on all submissions for an assignment
    Used when teacher manually triggers batch analysis
    
    Returns:
        dict: Summary of results
    """
    try:
        # Get all submissions for assignment
        cursor = db.submissions.find({
            "assignment_id": assignment_id,
            "approved": {"$ne": False}  # Exclude rejected
        })
        
        submissions = await cursor.to_list(length=None)
        
        if len(submissions) < 2:
            return {
                "status": "success",
                "message": "Not enough submissions to compare",
                "total_submissions": len(submissions)
            }
        
        # Group by language
        by_language = {}
        for sub in submissions:
            lang = sub["language"]
            if lang not in by_language:
                by_language[lang] = []
            by_language[lang].append(sub)
        
        detector = PlagiarismDetector()
        total_comparisons = 0
        flagged_pairs = {"green": 0, "yellow": 0, "red": 0}
        
        # Compare submissions within each language
        for lang, lang_submissions in by_language.items():
            for i in range(len(lang_submissions)):
                for j in range(i + 1, len(lang_submissions)):
                    sub1 = lang_submissions[i]
                    sub2 = lang_submissions[j]
                    
                    # Run detection
                    report = await detector.compare_submissions(
                        code1=sub1["code"],
                        code2=sub2["code"],
                        language=lang,
                        submission1_id=sub1["submission_id"],
                        submission2_id=sub2["submission_id"]
                    )
                    
                    total_comparisons += 1
                    flagged_pairs[report.flag_color.value] += 1
                    
                    # Store result if significant
                    if report.overall_similarity >= 0.30:
                        await store_plagiarism_result(report, assignment_id)
        
        # Update assignment metadata
        await db.assignments.update_one(
            {"assignment_id": assignment_id},
            {"$set": {
                "plagiarism_last_checked": datetime.utcnow(),
                "plagiarism_stats": {
                    "total_comparisons": total_comparisons,
                    "flagged_pairs": flagged_pairs
                }
            }}
        )
        
        return {
            "status": "success",
            "total_submissions": len(submissions),
            "total_comparisons": total_comparisons,
            "flagged_pairs": flagged_pairs,
            "message": f"Analyzed {total_comparisons} submission pairs"
        }
        
    except Exception as e:
        return {
            "status": "error",
            "message": str(e)
        }

async def get_submission_plagiarism_status(submission_id: str) -> dict:
    """
    Get plagiarism status for a specific submission
    Used by teachers to view details
    
    Returns FULL plagiarism data (teachers only)
    """
    submission = await db.submissions.find_one({"submission_id": submission_id})
    
    if not submission:
        return None
    
    # Get all plagiarism results involving this submission
    cursor = db.plagiarism_results.find({
        "$or": [
            {"submission_1_id": submission_id},
            {"submission_2_id": submission_id}
        ]
    }).sort("similarity_score", -1)
    
    matches = await cursor.to_list(length=None)
    
    # Remove MongoDB _id
    for match in matches:
        match.pop("_id", None)
    
    return {
        "submission_id": submission_id,
        "plagiarism_flag": submission.get("plagiarism_flag", "pending"),
        "total_matches": len(matches),
        "matches": matches,
        "ai_detection": {
            "is_likely_ai": submission.get("highest_similarity_match", {}).get("is_ai_generated", False),
            "ai_probability": submission.get("highest_similarity_match", {}).get("ai_probability", 0.0)
        }
    }

async def get_assignment_plagiarism_summary(assignment_id: str) -> dict:
    """
    Get plagiarism summary for entire assignment
    Teacher-only endpoint
    """
    # Get all plagiarism results
    cursor = db.plagiarism_results.find({
        "assignment_id": assignment_id
    })
    
    results = await cursor.to_list(length=None)
    
    summary = {
        "assignment_id": assignment_id,
        "total_pairs": len(results),
        "green_flags": sum(1 for r in results if r["flag"] == "green"),
        "yellow_flags": sum(1 for r in results if r["flag"] == "yellow"),
        "red_flags": sum(1 for r in results if r["flag"] == "red"),
        "unreviewed_count": sum(1 for r in results if not r["reviewed_by_teacher"]),
        "last_checked": None
    }
    
    # Get last check time from assignment
    assignment = await db.assignments.find_one(
        {"assignment_id": assignment_id},
        {"plagiarism_last_checked": 1}
    )
    
    if assignment:
        summary["last_checked"] = assignment.get("plagiarism_last_checked")
    
    return summary
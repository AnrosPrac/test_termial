from motor.motor_asyncio import AsyncIOMotorClient
import os

MONGO_URL = os.getenv("MONGO_URL")
client = AsyncIOMotorClient(MONGO_URL)
db = client.lumetrics_db

async def create_teacher_indexes():
    """
    Create database indexes for optimal query performance
    Called during application startup
    """
    
    # Teachers metadata
    await db.teachers_meta.create_index("user_id", unique=True)
    await db.teachers_meta.create_index("sidhi_id")
    
    # Classrooms
    await db.classrooms.create_index("classroom_id", unique=True)
    await db.classrooms.create_index("teacher_user_id")
    await db.classrooms.create_index([("university_id", 1), ("teacher_user_id", 1)])
    await db.classrooms.create_index("visibility")
    
    # Classroom memberships
    await db.classroom_memberships.create_index([("classroom_id", 1), ("student_user_id", 1)], unique=True)
    await db.classroom_memberships.create_index("student_user_id")
    await db.classroom_memberships.create_index([("classroom_id", 1), ("is_active", 1)])
    
    # Assignments
    await db.assignments.create_index("assignment_id", unique=True)
    await db.assignments.create_index("classroom_id")
    await db.assignments.create_index("teacher_user_id")
    await db.assignments.create_index([("classroom_id", 1), ("status", 1)])
    
    # Test cases
    # Test cases
    await db.testcases.create_index("testcase_id", unique=True)
    await db.testcases.create_index("assignment_id")
    await db.testcases.create_index("question_id")  # ✅ NEW: Query by question
    await db.testcases.create_index([("assignment_id", 1), ("question_id", 1)])  # ✅ NEW: Compound index
    await db.testcases.create_index([("assignment_id", 1), ("locked", 1)])
    await db.testcases.create_index([("question_id", 1), ("is_hidden", 1)])  # ✅ NEW: For filtering hidden tests
    
    # Submissions
    await db.submissions.create_index("submission_id", unique=True)
    await db.submissions.create_index("assignment_id")
    await db.submissions.create_index("student_user_id")
    await db.submissions.create_index([("assignment_id", 1), ("student_user_id", 1)])
    await db.submissions.create_index([("assignment_id", 1), ("approved", 1)])
    await db.submissions.create_index("submitted_at")
    
    # Plagiarism results
    await db.plagiarism_results.create_index("pair_id", unique=True)
    await db.plagiarism_results.create_index("assignment_id")
    await db.plagiarism_results.create_index([("assignment_id", 1), ("flag", 1)])
    await db.plagiarism_results.create_index([("assignment_id", 1), ("reviewed_by_teacher", 1)])
    
    # Audit logs
    await db.audit_logs.create_index("actor_user_id")
    await db.audit_logs.create_index([("target_type", 1), ("target_id", 1)])
    await db.audit_logs.create_index("timestamp")
    await db.audit_logs.create_index([("actor_user_id", 1), ("timestamp", -1)])
    
    print("✅ Teacher management indexes created successfully")
# Add to app/database.py

async def create_student_indexes():
    """
    Create database indexes for student operations
    Called during application startup
    """
    
    # Classroom memberships (student joins)
    await db.classroom_memberships.create_index(
        [("classroom_id", 1), ("student_user_id", 1)], 
        unique=True
    )
    await db.classroom_memberships.create_index("student_user_id")
    await db.classroom_memberships.create_index([("student_user_id", 1), ("is_active", 1)])
    await db.classroom_memberships.create_index("classroom_id")
    
    # Submissions (student code)
    await db.submissions.create_index("submission_id", unique=True)
    await db.submissions.create_index([("assignment_id", 1), ("student_user_id", 1)])
    await db.submissions.create_index("student_user_id")
    await db.submissions.create_index([("assignment_id", 1), ("approved", 1)])
    await db.submissions.create_index([("student_user_id", 1), ("submitted_at", -1)])
    await db.submissions.create_index("is_locked")
    
    # Submission audits (security logging)
    await db.submission_audits.create_index("student_user_id")
    await db.submission_audits.create_index([("assignment_id", 1), ("action", 1)])
    await db.submission_audits.create_index("timestamp")
    await db.submission_audits.create_index([("student_user_id", 1), ("timestamp", -1)])
    
    print("✅ Student management indexes created successfully")


"""
LUMETRIX COURSE SYSTEM - COMPLETE API REFERENCE
50+ endpoints across 6 router modules
"""

# ==================== MODULE 1: COURSE MANAGEMENT (course_router.py) ====================

# Create course (admin/instructor)
POST   /api/courses/create
Body: {title, description, course_type, domain, instructor_id?, thumbnail_url?, tags[], external_resources[]}

# Get course details
GET    /api/courses/{course_id}

# Update course (DRAFT only)
PUT    /api/courses/{course_id}
Body: {title?, description?, thumbnail_url?, tags?, external_resources?}

# Publish course (locks editing)
POST   /api/courses/{course_id}/publish

# List courses with filters
GET    /api/courses/
Params: course_type?, domain?, skip, limit

# Create question for course
POST   /api/courses/questions/create
Body: {course_id, title, description, difficulty, language, test_cases[], time_limit, memory_limit, points}

# Get question details
GET    /api/courses/questions/{question_id}

# ==================== MODULE 2: ENROLLMENT (enrollment_router.py) ====================

# Enroll in course
POST   /api/enrollments/enroll
Body: {course_id}
Returns: {enrollment_id, certificate_id}

# Get my enrolled courses
GET    /api/enrollments/my-courses

# Get progress in specific course
GET    /api/enrollments/course/{course_id}/progress

# Get available questions (unsolved only)
GET    /api/enrollments/course/{course_id}/questions

# ==================== MODULE 3: SUBMISSIONS (submission_router.py) ====================

# Submit solution for grading
POST   /api/submissions/submit
Body: {course_id, question_id, code, language}
Returns: {submission_id, status: "queued"}

# Get submission status
GET    /api/submissions/{submission_id}/status

# Get submission history for course
GET    /api/submissions/course/{course_id}/history
Params: skip, limit

# ==================== MODULE 4: LEADERBOARDS (leaderboard_router.py) ====================

# Course-specific leaderboard
GET    /api/leaderboards/course/{course_id}
Params: skip, limit

# Global leaderboard (OFFICIAL courses only)
GET    /api/leaderboards/global
Params: skip, limit

# College leaderboard
GET    /api/leaderboards/college/{college_name}
Params: skip, limit

# State leaderboard
GET    /api/leaderboards/state/{state_name}
Params: skip, limit

# Department leaderboard
GET    /api/leaderboards/department/{college_name}/{department}
Params: skip, limit

# Alumni hall of fame (frozen rankings)
GET    /api/leaderboards/alumni
Params: skip, limit

# Get my rank in course
GET    /api/leaderboards/my-rank/{course_id}

# ==================== MODULE 5: CERTIFICATES (certificate_router.py) ====================

# View dynamic certificate portfolio (HTML)
GET    /api/certificates/{certificate_id}
Returns: HTML page

# Get certificate data (JSON)
GET    /api/certificates/{certificate_id}/data

# Verify certificate authenticity
GET    /api/certificates/verify/{certificate_id}

# Claim certificate PDF
POST   /api/certificates/claim

# ==================== MODULE 6: PRACTICE SAMPLES (practice_router.py) ====================

# Get practice samples (5000 questions)
GET    /api/practice/samples
Params: chapter?, difficulty?, skip, limit, show_unread_first

# Mark sample as read
POST   /api/practice/samples/{sample_id}/mark-read

# Get specific sample
GET    /api/practice/samples/{sample_id}

# Get practice statistics
GET    /api/practice/stats

# ==================== RESPONSE FORMATS ====================

# Course Object
{
    "course_id": "COURSE_ABC123",
    "title": "C Programming Fundamentals",
    "description": "...",
    "course_type": "OFFICIAL" | "CREATOR",
    "domain": "SOFTWARE" | "HARDWARE",
    "status": "DRAFT" | "PUBLISHED" | "ACTIVE" | "ARCHIVED",
    "creator_id": "user123",
    "tags": ["beginner", "programming"],
    "external_resources": [{"title": "...", "url": "..."}],
    "stats": {
        "enrollments": 150,
        "completions": 45,
        "avg_rating": 4.5
    }
}

# Enrollment Object
{
    "enrollment_id": "ENR_XYZ789",
    "course_id": "COURSE_ABC123",
    "user_id": "user123",
    "sidhi_id": "user@sidhilynx.id",
    "certificate_id": "CERT_XYZ789",
    "progress": 45.5,
    "current_league": "GOLD",
    "league_points": 7500,
    "enrolled_at": "2026-01-30T10:00:00Z"
}

# Submission Result
{
    "submission_id": "SUB_DEF456",
    "status": "completed",
    "verdict": "Accepted",
    "result": {
        "passed": 10,
        "total": 10,
        "test_results": [...],
        "avg_execution_time_ms": 145.23,
        "max_execution_time_ms": 234.56,
        "avg_memory_mb": 12.4,
        "max_memory_mb": 15.2
    },
    "score": 95.5
}

# Leaderboard Entry
{
    "rank": 1,
    "user_id": "user123",
    "sidhi_id": "user@sidhilynx.id",
    "username": "John Doe",
    "college": "MIT",
    "league": "LEGEND",
    "points": 58000,
    "problems_solved": 548,
    "avg_efficiency": 1.15
}

# Certificate Data
{
    "certificate_id": "CERT_XYZ789",
    "user_id": "user123",
    "username": "John Doe",
    "course_title": "C Programming Fundamentals",
    "grade_points": 7500,
    "current_league": "GOLD",
    "problems_solved": 45,
    "completion_percentage": 68.5,
    "skills": ["C Programming", "Problem Solving"],
    "badges": ["speedster", "perfectionist"]
}

# ==================== GRADING FORMULA ====================

"""
final_score = base_points × correctness × efficiency × (1 + time_bonus) × attempt_penalty

Where:
- base_points: Question difficulty (Easy=100, Medium=300, Hard=600)
- correctness: passed/total test cases (0.0 - 1.0)
- efficiency: runtime_multiplier × memory_multiplier (0.6 - 1.32)
- time_bonus: 0.15 if in first 10%, 0.10 if in first 25%, 0.05 if in first 50%
- attempt_penalty: max(0.8, 1 - 0.05 × (attempts - 1))

League points = sum of all final_scores
"""

# ==================== LEAGUE THRESHOLDS ====================

BRONZE:   0 - 2,499
SILVER:   2,500 - 4,999
GOLD:     5,000 - 9,999
PLATINUM: 10,000 - 19,999
DIAMOND:  20,000 - 34,999
MYTHIC:   35,000 - 54,999
LEGEND:   55,000+

# ==================== KEY BUSINESS RULES ====================

# 1. Once a question is solved (all test cases passed), it is REMOVED from the user's available questions
# 2. OFFICIAL courses affect global/state/college/alumni leaderboards
# 3. CREATOR courses have isolated leaderboards (course-specific only)
# 4. Alumni board is FROZEN at graduation (immutable historical record)
# 5. Sample questions (5000) are read-only, no grading, just read tracking
# 6. Certificates are DYNAMIC portfolios that update in real-time
# 7. Course editing is LOCKED after publishing (immutability for fairness)

# ==================== DATABASE COLLECTIONS ====================

courses               # Course definitions
course_questions      # Graded problems (653 problems)
course_enrollments    # Student progress & league data
course_submissions    # Submission history & results
alumni_board          # Frozen alumni rankings
user_achievements     # Badges & achievements
user_sample_progress  # Read tracking for 5000 samples
training_samples      # 5000 practice questions (existing)
user_profiles         # User data (existing)

# ==================== TOTAL ENDPOINT COUNT ====================

Course Management:     7 endpoints
Enrollment:            4 endpoints
Submissions:           3 endpoints
Leaderboards:          7 endpoints
Certificates:          4 endpoints
Practice Samples:      4 endpoints
----------------------------------
TOTAL:                29 endpoints

from fastapi import APIRouter, HTTPException, Depends, Response
from fastapi.responses import HTMLResponse, FileResponse
from motor.motor_asyncio import AsyncIOMotorDatabase
from typing import Optional
from app.courses.models import CertificateData
from app.courses.dependencies import get_db,get_current_user_id
from datetime import datetime
import os

router = APIRouter( tags=["Certificates"])

# ==================== CERTIFICATE HELPERS ====================

async def get_certificate_data(db: AsyncIOMotorDatabase, certificate_id: str) -> Optional[dict]:
    """Get complete certificate data with fallbacks for missing data"""
    
    # Step 1: Find enrollment by certificate_id
    enrollment = await db.course_enrollments.find_one({"certificate_id": certificate_id})
    if not enrollment:
        print(f"❌ Enrollment not found for certificate_id: {certificate_id}")
        return None
    
    print(f"✅ Found enrollment: {enrollment.get('enrollment_id')}")
    
    # Step 2: Get user profile (OPTIONAL - use enrollment data as fallback)
    user = await db.users_profile.find_one({"user_id": enrollment["user_id"]})
    if not user:
        print(f"⚠️ User profile not found for user_id: {enrollment['user_id']}, using enrollment data")
        # Use enrollment data as fallback
        username = enrollment.get("user_id", "Student")
        college = None
        department = None
    else:
        print(f"✅ Found user profile for: {user.get('username')}")
        username = user.get("username", "Student")
        college = user.get("college")
        department = user.get("department")
    
    # Step 3: Get course details
    course = await db.courses.find_one({"course_id": enrollment["course_id"]})
    if not course:
        print(f"❌ Course not found for course_id: {enrollment['course_id']}")
        return None
    
    print(f"✅ Found course: {course.get('title')}")
    
    # Step 4: Get submission stats
    submissions = await db.course_submissions.find({
        "course_id": enrollment["course_id"],
        "user_id": enrollment["user_id"],
        "verdict": "Accepted"
    }).to_list(length=None)
    
    print(f"✅ Found {len(submissions)} accepted submissions")
    
    # Step 5: Calculate stats
    total_questions = await db.course_questions.count_documents({
        "course_id": enrollment["course_id"],
        "is_active": True
    })
    
    print(f"✅ Total questions in course: {total_questions}")
    
    solved_count = len(enrollment.get("solved_questions", []))
    
    # Step 6: Get badges/achievements (OPTIONAL)
    achievements = await db.user_achievements.find({
        "user_id": enrollment["user_id"],
        "course_id": enrollment["course_id"]
    }).to_list(length=None)
    
    print(f"✅ Found {len(achievements)} achievements")
    
    # Build certificate data
    certificate_data = {
        "certificate_id": certificate_id,
        "user_id": enrollment["user_id"],
        "sidhi_id": enrollment.get("sidhi_id", "N/A"),  # Fallback if null
        "username": username,
        "college": college,
        "department": department,
        "course_id": course["course_id"],
        "course_title": course["title"],
        "course_domain": course["domain"],
        "grade_points": enrollment.get("league_points", 0),
        "current_league": enrollment.get("current_league", "BRONZE"),
        "problems_solved": solved_count,
        "total_problems": total_questions,
        "completion_percentage": round((solved_count / total_questions * 100) if total_questions > 0 else 0, 2),
        "enrolled_at": enrollment["enrolled_at"],
        "last_updated": datetime.utcnow(),
        "badges": [a.get("badge_id") for a in achievements],
        "skills": get_skills_from_course(course["domain"])
    }
    
    print(f"✅ Successfully built certificate data")
    return certificate_data

def get_skills_from_course(domain: str) -> list:
    """Extract skills based on course domain"""
    if domain == "SOFTWARE":
        return ["C Programming", "C++", "Python", "Problem Solving", "Algorithms"]
    elif domain == "HARDWARE":
        return ["VHDL", "Verilog", "Digital Design", "HDL", "Circuit Design"]
    return []

def generate_certificate_html(data: dict) -> str:
    """Generate dynamic certificate portfolio HTML"""
    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>{data['username']} - Lumetrix Certificate</title>
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            body {{
                font-family: 'Inter', -apple-system, system-ui, sans-serif;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                min-height: 100vh;
                padding: 2rem;
            }}
            .container {{
                max-width: 1000px;
                margin: 0 auto;
                background: white;
                border-radius: 20px;
                box-shadow: 0 20px 60px rgba(0,0,0,0.3);
                overflow: hidden;
            }}
            .header {{
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                padding: 3rem 2rem;
                text-align: center;
                color: white;
            }}
            .header h1 {{ font-size: 2.5rem; margin-bottom: 0.5rem; }}
            .header p {{ opacity: 0.9; font-size: 1.1rem; }}
            .content {{
                padding: 2rem;
            }}
            .profile {{
                display: flex;
                align-items: center;
                gap: 2rem;
                margin-bottom: 2rem;
                padding-bottom: 2rem;
                border-bottom: 2px solid #f0f0f0;
            }}
            .profile-icon {{
                width: 120px;
                height: 120px;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                border-radius: 50%;
                display: flex;
                align-items: center;
                justify-content: center;
                font-size: 3rem;
                color: white;
                font-weight: bold;
            }}
            .profile-info h2 {{ font-size: 2rem; margin-bottom: 0.5rem; }}
            .profile-info p {{ color: #666; margin-bottom: 0.25rem; }}
            .stats {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                gap: 1.5rem;
                margin-bottom: 2rem;
            }}
            .stat-card {{
                background: #f8f9fa;
                padding: 1.5rem;
                border-radius: 12px;
                text-align: center;
            }}
            .stat-card h3 {{ font-size: 2rem; color: #667eea; margin-bottom: 0.5rem; }}
            .stat-card p {{ color: #666; font-size: 0.9rem; }}
            .league-badge {{
                display: inline-block;
                padding: 0.5rem 1.5rem;
                background: linear-gradient(135deg, #ffd700 0%, #ffed4e 100%);
                border-radius: 25px;
                font-weight: bold;
                color: #333;
                margin: 1rem 0;
            }}
            .skills {{
                display: flex;
                flex-wrap: wrap;
                gap: 0.75rem;
                margin-top: 1rem;
            }}
            .skill-tag {{
                background: #667eea;
                color: white;
                padding: 0.5rem 1rem;
                border-radius: 20px;
                font-size: 0.9rem;
            }}
            .footer {{
                text-align: center;
                padding: 2rem;
                background: #f8f9fa;
                color: #666;
                font-size: 0.9rem;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>Lumetrix Certificate</h1>
                <p>Official Course Completion Portfolio</p>
            </div>
            
            <div class="content">
                <div class="profile">
                    <div class="profile-icon">{data['username'][0].upper()}</div>
                    <div class="profile-info">
                        <h2>{data['username']}</h2>
                        <p><strong>Sidhi ID:</strong> {data['sidhi_id']}</p>
                        {f"<p><strong>College:</strong> {data['college']}</p>" if data.get('college') else ""}
                        {f"<p><strong>Department:</strong> {data['department']}</p>" if data.get('department') else ""}
                    </div>
                </div>
                
                <h3 style="margin-bottom: 1rem; color: #333;">Course Achievements</h3>
                <div class="stat-card" style="margin-bottom: 1.5rem;">
                    <h2 style="color: #667eea; margin-bottom: 0.5rem;">{data['course_title']}</h2>
                    <p style="color: #666;">Domain: {data['course_domain']}</p>
                    <div class="league-badge">{data['current_league']} League</div>
                </div>
                
                <div class="stats">
                    <div class="stat-card">
                        <h3>{data['grade_points']}</h3>
                        <p>Grade Points</p>
                    </div>
                    <div class="stat-card">
                        <h3>{data['problems_solved']}</h3>
                        <p>Problems Solved</p>
                    </div>
                    <div class="stat-card">
                        <h3>{data['completion_percentage']}%</h3>
                        <p>Course Completion</p>
                    </div>
                </div>
                
                <h3 style="margin-bottom: 1rem; color: #333;">Skills Acquired</h3>
                <div class="skills">
                    {''.join([f'<span class="skill-tag">{skill}</span>' for skill in data.get('skills', [])])}
                </div>
            </div>
            
            <div class="footer">
                <p><strong>Certificate ID:</strong> {data['certificate_id']}</p>
                <p>Last Updated: {data['last_updated'].strftime('%B %d, %Y')}</p>
                <p style="margin-top: 1rem;">This is a dynamic certificate that updates with your progress.</p>
                <p>Verify at: https://lumetrix.com/verify/{data['certificate_id']}</p>
            </div>
        </div>
    </body>
    </html>
    """
    return html

# ==================== ENDPOINTS ====================

@router.get("/{certificate_id}", response_class=HTMLResponse)
async def view_certificate(
    certificate_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db)
):
    """View dynamic certificate portfolio"""
    print(f"\n🔍 Attempting to view certificate: {certificate_id}")
    
    data = await get_certificate_data(db, certificate_id)
    if not data:
        print(f"❌ Certificate data could not be retrieved")
        raise HTTPException(status_code=404, detail="Certificate not found or incomplete data")
    
    print(f"✅ Certificate data retrieved successfully")
    html = generate_certificate_html(data)
    return HTMLResponse(content=html)

@router.get("/{certificate_id}/data")
async def get_certificate_json(
    certificate_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db)
):
    """Get certificate data as JSON"""
    print(f"\n🔍 Attempting to get certificate JSON: {certificate_id}")
    
    data = await get_certificate_data(db, certificate_id)
    if not data:
        print(f"❌ Certificate data could not be retrieved")
        raise HTTPException(status_code=404, detail="Certificate not found or incomplete data")
    
    print(f"✅ Certificate data retrieved successfully")
    return data

@router.get("/verify/{certificate_id}")
async def verify_certificate(
    certificate_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db)
):
    """Verify certificate authenticity"""
    enrollment = await db.course_enrollments.find_one({"certificate_id": certificate_id})
    
    if not enrollment:
        return {
            "valid": False,
            "message": "Certificate not found"
        }
    
    return {
        "valid": True,
        "certificate_id": certificate_id,
        "issued_to": enrollment.get("sidhi_id"),  # Added .get() for safety
        "course_id": enrollment["course_id"],
        "issued_at": enrollment["enrolled_at"],
        "message": "Certificate is valid"
    }

@router.post("/claim")
async def claim_certificate_pdf(
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    """Claim PDF snapshot of certificate (future implementation)"""
    # This would generate a static PDF from current state
    # For now, return URL to dynamic certificate
    
    enrollments = await db.course_enrollments.find({
        "user_id": user_id,
        "is_active": True
    }).to_list(length=None)
    
    certificates = []
    for enr in enrollments:
        # Check if minimum score met
        if enr.get("league_points", 0) >= 1000:  # Minimum threshold
            certificates.append({
                "certificate_id": enr["certificate_id"],
                "course_id": enr["course_id"],
                "url": f"https://lumetrix.com/certificates/{enr['certificate_id']}",
                "claimable": True
            })
    
    return {
        "certificates": certificates,
        "count": len(certificates)
    }
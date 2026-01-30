"""
Integration Guide: Add Course System to Existing Lumetrix Backend

Add this to your main.py file
"""

# ==================== STEP 1: IMPORTS (Add to top of main.py) ====================

from courses.app import setup_course_routes, startup_course_system
from courses.practice_router import router as practice_router

# ==================== STEP 2: REGISTER ROUTERS (After existing routers) ====================

# Existing routers...
# app.include_router(training_router)
# app.include_router(coding_router)
# etc...

# NEW: Course system routers
setup_course_routes(app)  # This registers all 5 course routers
app.include_router(practice_router)  # Practice samples router

# ==================== STEP 3: STARTUP EVENT (Add to startup) ====================

@app.on_event("startup")
async def startup_event():
    # Existing startup code...
    # await create_teacher_indexes()
    # await create_student_indexes()
    
    # NEW: Initialize course system
    await startup_course_system()
    
    print("✅ Lumetrix backend fully initialized")

# ==================== STEP 4: DEPENDENCIES (Replace lambda dependencies) ====================

"""
Current code has placeholder dependencies like:
    db: AsyncIOMotorDatabase = Depends(lambda: None)
    user_id: str = Depends(lambda: None)

Replace with your actual dependencies:
"""

from app.admin.hardened_firebase_auth import get_current_user

# In each router file, replace:
# Depends(lambda: None) 
# with:
# Depends(get_db)  # for database
# Depends(get_current_user)  # for auth

# Example dependency functions to add:

async def get_db():
    """Database dependency"""
    return db  # Your existing MongoDB client

async def get_current_user_id(user = Depends(get_current_user)):
    """Extract user_id from authenticated user"""
    return user.get("user_id")

async def get_sidhi_id(user = Depends(get_current_user)):
    """Extract sidhi_id from authenticated user"""
    return user.get("sidhi_id")

# ==================== STEP 5: ENVIRONMENT VARIABLES ====================

"""
Add these to your .env file:

JUDGE_API_URL=http://your-judge-service:8000
JUDGE_API_KEY=your_api_key_here
"""

# ==================== COMPLETE MAIN.PY STRUCTURE ====================

"""
from datetime import datetime
from fastapi import FastAPI, BackgroundTasks, Request, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os

# Existing imports...
from app.ai.router import router as ai_router
from app.chat.router import router as chat_router
# ... all your existing imports ...

# NEW: Course system imports
from courses.app import setup_course_routes, startup_course_system
from courses.practice_router import router as practice_router

app = FastAPI(title="Lumetrics AI Engine")

# MongoDB
MONGO_URL = os.getenv("MONGO_URL")
client = AsyncIOMotorClient(MONGO_URL)
db = client.lumetrics_db

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Existing routers
app.include_router(ai_router)
app.include_router(chat_router)
app.include_router(training_router)
app.include_router(coding_router)
app.include_router(teacher_router)
app.include_router(student_router)

# NEW: Course system routers
setup_course_routes(app)
app.include_router(practice_router)

@app.on_event("startup")
async def startup_event():
    # Existing startup
    await create_teacher_indexes()
    await create_student_indexes()
    
    # NEW: Course system startup
    await startup_course_system()
    
    print("✅ Lumetrix backend ready")

@app.get("/")
async def root():
    return {
        "service": "Lumetrix AI Engine",
        "version": os.getenv("VERSION"),
        "status": "online",
        "features": [
            "AI Chat",
            "Training",
            "Coding Practice",
            "Course System",  # NEW
            "Teacher Portal",
            "Student Portal"
        ]
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
"""

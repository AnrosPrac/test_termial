from fastapi import APIRouter, HTTPException, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase
from typing import Optional
from pydantic import BaseModel
from datetime import datetime
import uuid
import httpx
import os

from app.courses.dependencies import get_db, get_current_user_id

router = APIRouter(tags=["AI Doubt Solver"])

# ==================== MODELS ====================

class DoubtQuery(BaseModel):
    question_id: Optional[str] = None  # If related to a specific question
    doubt_text: str
    code_snippet: Optional[str] = None  # If they want code help
    language: Optional[str] = None

class AIHintRequest(BaseModel):
    question_id: str
    current_approach: Optional[str] = None

# ==================== AI INTEGRATION ====================

# You would replace this with your actual AI service
AI_SERVICE_URL = os.getenv("AI_SERVICE_URL", "http://localhost:8001")
AI_API_KEY = os.getenv("AI_API_KEY", "")


async def call_ai_service(prompt: str, context: dict = None) -> str:
    """
    Call your existing AI generation service
    Replace this with your actual implementation
    """
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{AI_SERVICE_URL}/generate",
                json={
                    "prompt": prompt,
                    "context": context,
                    "max_tokens": 500,
                    "temperature": 0.7
                },
                headers={"Authorization": f"Bearer {AI_API_KEY}"},
                timeout=30.0
            )
            
            if response.status_code == 200:
                return response.json().get("response", "Sorry, I couldn't generate a response.")
            else:
                return "AI service temporarily unavailable. Please try again later."
    
    except Exception as e:
        return f"Error communicating with AI service: {str(e)}"


def serialize_mongo(doc: dict) -> dict:
    if "_id" in doc:
        doc["_id"] = str(doc["_id"])
    return doc


# ==================== DOUBT SOLVER ENDPOINTS ====================

@router.post("/ask-doubt")
async def ask_doubt(
    doubt: DoubtQuery,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    """
    Ask AI a doubt about a problem or concept
    AI will provide hints and guidance without giving away the solution
    """
    
    doubt_id = f"DOUBT_{uuid.uuid4().hex[:12].upper()}"
    
    # Build context for AI
    context = {
        "user_id": user_id,
        "doubt_text": doubt.doubt_text
    }
    
    # If related to a specific question, fetch question details
    question_context = None
    if doubt.question_id:
        question = await db.course_questions.find_one({"question_id": doubt.question_id})
        if question:
            question_context = {
                "title": question.get("title"),
                "description": question.get("description"),
                "difficulty": question.get("difficulty"),
                "language": question.get("language")
            }
            context["question"] = question_context
    
    # Add code snippet if provided
    if doubt.code_snippet:
        context["code"] = doubt.code_snippet
        context["language"] = doubt.language or "unknown"
    
    # Build AI prompt
    if doubt.question_id and question_context:
        prompt = f"""
You are a helpful programming tutor. A student is working on this problem:

Title: {question_context['title']}
Difficulty: {question_context['difficulty']}
Language: {question_context['language']}

Description: {question_context['description']}

Student's Doubt: {doubt.doubt_text}

{'Student Code Snippet:' + doubt.code_snippet if doubt.code_snippet else ''}

Provide helpful hints and guidance WITHOUT giving away the complete solution. 
- Help them understand the concept
- Point out potential issues in their approach
- Suggest what to think about next
- Do NOT provide the complete working code
"""
    else:
        prompt = f"""
You are a helpful programming tutor. A student has the following doubt:

{doubt.doubt_text}

{'They provided this code snippet:' + doubt.code_snippet if doubt.code_snippet else ''}

Provide clear, educational guidance to help them understand the concept and solve the problem themselves.
"""
    
    # Get AI response
    ai_response = await call_ai_service(prompt, context)
    
    # Store doubt and response in database
    doubt_record = {
        "doubt_id": doubt_id,
        "user_id": user_id,
        "question_id": doubt.question_id,
        "doubt_text": doubt.doubt_text,
        "code_snippet": doubt.code_snippet,
        "language": doubt.language,
        "ai_response": ai_response,
        "created_at": datetime.utcnow(),
        "helpful_votes": 0,
        "not_helpful_votes": 0
    }
    
    await db.ai_doubts.insert_one(doubt_record)
    
    return {
        "doubt_id": doubt_id,
        "response": ai_response,
        "question_id": doubt.question_id,
        "timestamp": datetime.utcnow()
    }


@router.post("/get-hint")
async def get_hint(
    hint_request: AIHintRequest,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    """
    Get a progressive hint for a specific question
    Hints get progressively more detailed
    """
    
    # Check how many hints user has already requested
    hint_count = await db.ai_doubts.count_documents({
        "user_id": user_id,
        "question_id": hint_request.question_id,
        "doubt_text": {"$regex": "^HINT REQUEST"}
    })
    
    # Get question
    question = await db.course_questions.find_one({"question_id": hint_request.question_id})
    if not question:
        raise HTTPException(status_code=404, detail="Question not found")
    
    # Build progressive hint prompt
    hint_level = min(hint_count + 1, 3)  # Max 3 levels
    
    prompt = f"""
You are a helpful programming tutor. Provide a HINT LEVEL {hint_level} for this problem:

Title: {question['title']}
Description: {question['description']}
Difficulty: {question['difficulty']}

{'Student mentioned: ' + hint_request.current_approach if hint_request.current_approach else ''}

Hint Level Guidelines:
- Level 1: Point to the general algorithm/approach category (e.g., "Think about using two pointers")
- Level 2: Provide more specific direction (e.g., "Initialize two pointers at start and end, move them based on...")
- Level 3: Detailed step-by-step approach WITHOUT complete code

Provide Hint Level {hint_level} only.
"""
    
    ai_response = await call_ai_service(prompt, {
        "question_id": hint_request.question_id,
        "hint_level": hint_level
    })
    
    # Store hint request
    doubt_id = f"HINT_{uuid.uuid4().hex[:10].upper()}"
    await db.ai_doubts.insert_one({
        "doubt_id": doubt_id,
        "user_id": user_id,
        "question_id": hint_request.question_id,
        "doubt_text": f"HINT REQUEST LEVEL {hint_level}",
        "ai_response": ai_response,
        "created_at": datetime.utcnow(),
        "hint_level": hint_level
    })
    
    return {
        "hint_level": hint_level,
        "max_hints": 3,
        "remaining_hints": 3 - hint_level,
        "hint": ai_response,
        "message": "This is a hint, not the complete solution. Try implementing it yourself!"
    }


@router.post("/explain-code")
async def explain_code(
    question_id: str,
    code: str,
    language: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    """
    Ask AI to explain a piece of code
    Useful for understanding solutions after solving or learning from others
    """
    
    question = await db.course_questions.find_one({"question_id": question_id})
    question_title = question.get("title") if question else "Code Explanation"
    
    prompt = f"""
Explain this {language} code in simple terms. Break down what each part does.

Problem: {question_title}

Code:
```{language}
{code}
```

Provide:
1. Overall approach explanation
2. Step-by-step breakdown
3. Time and space complexity analysis
4. Any optimization suggestions
"""
    
    explanation = await call_ai_service(prompt, {
        "language": language,
        "question_id": question_id
    })
    
    return {
        "explanation": explanation,
        "language": language,
        "question_id": question_id
    }


@router.get("/my-doubts")
async def get_my_doubts(
    skip: int = 0,
    limit: int = 20,
    question_id: Optional[str] = None,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    """
    Get user's doubt history
    """
    
    query = {"user_id": user_id}
    if question_id:
        query["question_id"] = question_id
    
    cursor = db.ai_doubts.find(query).sort("created_at", -1).skip(skip).limit(limit)
    doubts = await cursor.to_list(length=limit)
    
    # Enrich with question titles
    for doubt in doubts:
        if doubt.get("question_id"):
            question = await db.course_questions.find_one({"question_id": doubt["question_id"]})
            doubt["question_title"] = question.get("title") if question else "Unknown"
    
    return {
        "doubts": [serialize_mongo(d) for d in doubts],
        "count": len(doubts),
        "skip": skip,
        "limit": limit
    }


@router.post("/doubt/{doubt_id}/feedback")
async def rate_doubt_response(
    doubt_id: str,
    helpful: bool,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    """
    Rate if AI response was helpful
    """
    
    doubt = await db.ai_doubts.find_one({"doubt_id": doubt_id, "user_id": user_id})
    if not doubt:
        raise HTTPException(status_code=404, detail="Doubt not found")
    
    if helpful:
        await db.ai_doubts.update_one(
            {"doubt_id": doubt_id},
            {"$inc": {"helpful_votes": 1}}
        )
    else:
        await db.ai_doubts.update_one(
            {"doubt_id": doubt_id},
            {"$inc": {"not_helpful_votes": 1}}
        )
    
    return {
        "success": True,
        "message": "Feedback recorded. This helps us improve!"
    }


@router.get("/question/{question_id}/common-doubts")
async def get_common_doubts(
    question_id: str,
    limit: int = 5,
    db: AsyncIOMotorDatabase = Depends(get_db)
):
    """
    Get most helpful AI responses for a question
    Helps other students learn from common doubts
    """
    
    cursor = db.ai_doubts.find({
        "question_id": question_id,
        "helpful_votes": {"$gt": 0}
    }).sort("helpful_votes", -1).limit(limit)
    
    doubts = await cursor.to_list(length=limit)
    
    return {
        "question_id": question_id,
        "common_doubts": [
            {
                "doubt_text": d["doubt_text"],
                "ai_response": d["ai_response"],
                "helpful_votes": d.get("helpful_votes", 0)
            }
            for d in doubts
        ]
    }
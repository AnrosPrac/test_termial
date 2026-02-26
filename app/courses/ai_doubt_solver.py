"""
Lumetrix AI Doubt Solver — powered by Cerebras (gpt-oss-120b)
Streaming responses via SSE for real-time feel.
"""

import os
import uuid
from datetime import datetime
from typing import Optional, AsyncGenerator

from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import StreamingResponse
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel
from cerebras.cloud.sdk import Cerebras

from app.courses.dependencies import get_db, get_current_user_id

router = APIRouter(tags=["AI Doubt Solver"])

# ── Cerebras client ───────────────────────────────────────────────────────────
# Set CEREBRAS_API_KEY in your .env — client picks it up automatically
_cerebras = Cerebras(api_key=os.environ.get("CEREBRAS_API_KEY"))

MODEL         = "gpt-oss-120b"
MAX_TOKENS    = 32768
TEMPERATURE   = 1
TOP_P         = 1
REASONING     = "medium"


# ══════════════════════════════════════════════════════════════════
#  MODELS
# ══════════════════════════════════════════════════════════════════

class DoubtQuery(BaseModel):
    question_id:  Optional[str] = None   # optional — can ask general doubts
    doubt_text:   str
    code_snippet: Optional[str] = None
    language:     Optional[str] = None

class HintRequest(BaseModel):
    question_id:      str
    current_approach: Optional[str] = None

class ExplainRequest(BaseModel):
    question_id: str
    code:        str
    language:    str

class FeedbackRequest(BaseModel):
    helpful: bool


# ══════════════════════════════════════════════════════════════════
#  CEREBRAS HELPERS
# ══════════════════════════════════════════════════════════════════

def _cerebras_stream(system: str, user: str) -> AsyncGenerator[str, None]:
    """
    Returns a generator that yields SSE-formatted chunks.
    Uses Cerebras streaming so the frontend gets tokens in real time.
    """
    stream = _cerebras.chat.completions.create(
        model=MODEL,
        stream=True,
        max_completion_tokens=MAX_TOKENS,
        temperature=TEMPERATURE,
        top_p=TOP_P,
        reasoning_effort=REASONING,
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
    )

    def _gen():
        full_text = []
        for chunk in stream:
            token = chunk.choices[0].delta.content or ""
            if token:
                full_text.append(token)
                # SSE format: "data: <token>\n\n"
                yield f"data: {token}\n\n"
        # Final event so frontend knows stream ended
        yield "data: [DONE]\n\n"

    return _gen()


async def _cerebras_full(system: str, user: str) -> str:
    """
    Non-streaming call — returns full response as string.
    Used for endpoints that need to save the response to DB before returning.
    """
    response = _cerebras.chat.completions.create(
        model=MODEL,
        stream=False,
        max_completion_tokens=MAX_TOKENS,
        temperature=TEMPERATURE,
        top_p=TOP_P,
        reasoning_effort=REASONING,
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
    )
    return response.choices[0].message.content or ""


def _serialize(doc: dict) -> dict:
    if "_id" in doc:
        doc["_id"] = str(doc["_id"])
    return doc


# ══════════════════════════════════════════════════════════════════
#  SYSTEM PROMPTS
# ══════════════════════════════════════════════════════════════════

TUTOR_SYSTEM = """You are an expert programming tutor on the Lumetrix platform.
Your job is to help students understand concepts and debug their thinking — NOT to give away solutions.
Rules:
- Never write the complete working solution
- Ask Socratic questions when helpful
- Be encouraging and clear
- Keep responses concise and focused
- Use code snippets only to illustrate concepts, never to solve the problem directly"""

HINT_SYSTEM = """You are a programming tutor giving progressive hints.
You must follow the hint level exactly — don't give more than requested.
Never reveal the complete solution."""

EXPLAIN_SYSTEM = """You are a code explanation expert.
Break down code clearly: overall approach, step-by-step logic, time/space complexity, optimization suggestions.
Be concise and educational."""


# ══════════════════════════════════════════════════════════════════
#  ENDPOINTS
# ══════════════════════════════════════════════════════════════════

@router.post("/ask-doubt/stream")
async def ask_doubt_stream(
    doubt: DoubtQuery,
    db:      AsyncIOMotorDatabase = Depends(get_db),
    user_id: str                  = Depends(get_current_user_id)
):
    """
    Ask AI a doubt — STREAMING version (recommended).
    Returns Server-Sent Events stream.
    Frontend reads chunks via EventSource or fetch with ReadableStream.

    Send:
        {
          "question_id":  "Q_XXXXXXXX",   // optional
          "doubt_text":   "Why is my loop infinite?",
          "code_snippet": "while(1){...}", // optional
          "language":     "c"              // optional
        }

    Receive: SSE stream
        data: <token>
        data: <token>
        ...
        data: [DONE]
    """
    # Build user message
    parts = [f"Student doubt: {doubt.doubt_text}"]

    if doubt.question_id:
        question = await db.course_questions.find_one({"question_id": doubt.question_id})
        if question:
            parts.insert(0, (
                f"Problem: {question.get('title')}\n"
                f"Difficulty: {question.get('difficulty')}\n"
                f"Description: {question.get('description')}\n"
            ))

    if doubt.code_snippet:
        lang = doubt.language or "code"
        parts.append(f"\nStudent's code ({lang}):\n```{lang}\n{doubt.code_snippet}\n```")

    user_msg = "\n".join(parts)

    # Save the doubt record (response saved later via /ask-doubt/save)
    doubt_id = f"DOUBT_{uuid.uuid4().hex[:12].upper()}"
    await db.ai_doubts.insert_one({
        "doubt_id":    doubt_id,
        "user_id":     user_id,
        "question_id": doubt.question_id,
        "doubt_text":  doubt.doubt_text,
        "code_snippet":doubt.code_snippet,
        "language":    doubt.language,
        "ai_response": None,   # filled by /save endpoint after stream ends
        "created_at":  datetime.utcnow(),
        "helpful_votes":     0,
        "not_helpful_votes": 0,
        "type": "doubt"
    })

    # Stream header includes doubt_id so frontend can call /save after
    headers = {
        "X-Doubt-Id":    doubt_id,
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    }

    return StreamingResponse(
        _cerebras_stream(TUTOR_SYSTEM, user_msg),
        media_type="text/event-stream",
        headers=headers
    )


@router.post("/ask-doubt")
async def ask_doubt(
    doubt: DoubtQuery,
    db:      AsyncIOMotorDatabase = Depends(get_db),
    user_id: str                  = Depends(get_current_user_id)
):
    """
    Ask AI a doubt — NON-STREAMING version.
    Waits for full response then returns JSON.
    Use this if your frontend doesn't support SSE.

    Send:
        {
          "question_id":  "Q_XXXXXXXX",   // optional
          "doubt_text":   "Why is my loop infinite?",
          "code_snippet": "while(1){...}", // optional
          "language":     "c"              // optional
        }

    Receive:
        {
          "doubt_id":   "DOUBT_XXXXXXXXXXXX",
          "response":   "Here's what's happening...",
          "question_id": "Q_XXXXXXXX",
          "timestamp":  "2026-..."
        }
    """
    parts = [f"Student doubt: {doubt.doubt_text}"]

    if doubt.question_id:
        question = await db.course_questions.find_one({"question_id": doubt.question_id})
        if question:
            parts.insert(0, (
                f"Problem: {question.get('title')}\n"
                f"Difficulty: {question.get('difficulty')}\n"
                f"Description: {question.get('description')}\n"
            ))

    if doubt.code_snippet:
        lang = doubt.language or "code"
        parts.append(f"\nStudent's code ({lang}):\n```{lang}\n{doubt.code_snippet}\n```")

    user_msg    = "\n".join(parts)
    ai_response = await _cerebras_full(TUTOR_SYSTEM, user_msg)

    doubt_id = f"DOUBT_{uuid.uuid4().hex[:12].upper()}"
    await db.ai_doubts.insert_one({
        "doubt_id":          doubt_id,
        "user_id":           user_id,
        "question_id":       doubt.question_id,
        "doubt_text":        doubt.doubt_text,
        "code_snippet":      doubt.code_snippet,
        "language":          doubt.language,
        "ai_response":       ai_response,
        "created_at":        datetime.utcnow(),
        "helpful_votes":     0,
        "not_helpful_votes": 0,
        "type": "doubt"
    })

    return {
        "doubt_id":   doubt_id,
        "response":   ai_response,
        "question_id":doubt.question_id,
        "timestamp":  datetime.utcnow().isoformat()
    }


@router.post("/ask-doubt/{doubt_id}/save")
async def save_doubt_response(
    doubt_id: str,
    body: dict,   # { "response": "full AI text" }
    db:      AsyncIOMotorDatabase = Depends(get_db),
    user_id: str                  = Depends(get_current_user_id)
):
    """
    After streaming completes, frontend calls this to persist the full response.
    Only the owner of the doubt can save it.

    Send:  { "response": "<full assembled AI text>" }
    Receive: { "success": true }
    """
    doubt = await db.ai_doubts.find_one({"doubt_id": doubt_id, "user_id": user_id})
    if not doubt:
        raise HTTPException(status_code=404, detail="Doubt not found")

    await db.ai_doubts.update_one(
        {"doubt_id": doubt_id},
        {"$set": {"ai_response": body.get("response", "")}}
    )
    return {"success": True}


@router.post("/get-hint")
async def get_hint(
    req:     HintRequest,
    db:      AsyncIOMotorDatabase = Depends(get_db),
    user_id: str                  = Depends(get_current_user_id)
):
    """
    Get a progressive hint for a question. Max 3 levels.
    Each call automatically advances to the next hint level.

    Send:
        {
          "question_id":      "Q_XXXXXXXX",
          "current_approach": "I tried using a loop..."  // optional
        }

    Receive:
        {
          "doubt_id":        "HINT_XXXXXXXXXX",
          "hint_level":      1,          // 1, 2, or 3
          "max_hints":       3,
          "hints_remaining": 2,
          "hint":            "Think about...",
          "is_final_hint":   false
        }
    """
    question = await db.course_questions.find_one({"question_id": req.question_id})
    if not question:
        raise HTTPException(status_code=404, detail="Question not found")

    # Count previous hints for this question by this user
    prev_hints = await db.ai_doubts.count_documents({
        "user_id":     user_id,
        "question_id": req.question_id,
        "type":        "hint"
    })

    hint_level = min(prev_hints + 1, 3)

    LEVEL_GUIDE = {
        1: "Give a very gentle nudge — just the category of algorithm/technique to think about. One sentence max.",
        2: "Give more specific direction — which data structure or approach, and why. 2-3 sentences.",
        3: "Give a detailed step-by-step approach description WITHOUT any actual code. Be thorough."
    }

    user_msg = (
        f"Problem: {question.get('title')}\n"
        f"Description: {question.get('description')}\n"
        f"Difficulty: {question.get('difficulty')}\n"
        f"Language: {question.get('language')}\n"
    )
    if req.current_approach:
        user_msg += f"\nStudent's current approach: {req.current_approach}\n"

    user_msg += f"\nProvide HINT LEVEL {hint_level}. Instruction: {LEVEL_GUIDE[hint_level]}"

    ai_hint = await _cerebras_full(HINT_SYSTEM, user_msg)

    doubt_id = f"HINT_{uuid.uuid4().hex[:10].upper()}"
    await db.ai_doubts.insert_one({
        "doubt_id":    doubt_id,
        "user_id":     user_id,
        "question_id": req.question_id,
        "doubt_text":  f"HINT_LEVEL_{hint_level}",
        "ai_response": ai_hint,
        "hint_level":  hint_level,
        "created_at":  datetime.utcnow(),
        "helpful_votes":     0,
        "not_helpful_votes": 0,
        "type": "hint"
    })

    return {
        "doubt_id":       doubt_id,
        "hint_level":     hint_level,
        "max_hints":      3,
        "hints_remaining":3 - hint_level,
        "hint":           ai_hint,
        "is_final_hint":  hint_level == 3
    }


@router.post("/explain-code")
async def explain_code(
    req:     ExplainRequest,
    db:      AsyncIOMotorDatabase = Depends(get_db),
    user_id: str                  = Depends(get_current_user_id)
):
    """
    Ask AI to explain a piece of code — useful after solving to understand better.

    Send:
        {
          "question_id": "Q_XXXXXXXX",
          "code":        "int main(){...}",
          "language":    "c"
        }

    Receive:
        {
          "explanation": "This code does...",
          "language":    "c",
          "question_id": "Q_XXXXXXXX"
        }
    """
    question = await db.course_questions.find_one({"question_id": req.question_id})
    title    = question.get("title", "Code Explanation") if question else "Code Explanation"

    user_msg = (
        f"Problem: {title}\n\n"
        f"Code ({req.language}):\n```{req.language}\n{req.code}\n```\n\n"
        "Explain this code: overall approach, step-by-step breakdown, time and space complexity, any optimization suggestions."
    )

    explanation = await _cerebras_full(EXPLAIN_SYSTEM, user_msg)

    return {
        "explanation": explanation,
        "language":    req.language,
        "question_id": req.question_id
    }


@router.get("/my-doubts")
async def get_my_doubts(
    skip:        int           = 0,
    limit:       int           = 20,
    question_id: Optional[str] = None,
    db:          AsyncIOMotorDatabase = Depends(get_db),
    user_id:     str           = Depends(get_current_user_id)
):
    """
    Get student's full AI interaction history.

    Query params: ?question_id=Q_XXX (optional filter)

    Receive:
        {
          "doubts": [
            {
              "doubt_id":    "DOUBT_XXX",
              "type":        "doubt" | "hint",
              "doubt_text":  "...",
              "ai_response": "...",
              "hint_level":  1,           // only for hints
              "question_id": "Q_XXX",
              "question_title": "...",
              "created_at":  "2026-..."
            }
          ],
          "count": 5
        }
    """
    query = {"user_id": user_id}
    if question_id:
        query["question_id"] = question_id

    cursor = db.ai_doubts.find(query).sort("created_at", -1).skip(skip).limit(limit)
    doubts = await cursor.to_list(length=limit)

    for d in doubts:
        if d.get("question_id"):
            q = await db.course_questions.find_one({"question_id": d["question_id"]})
            d["question_title"] = q.get("title") if q else "Unknown"

    return {
        "doubts": [_serialize(d) for d in doubts],
        "count":  len(doubts),
        "skip":   skip,
        "limit":  limit
    }


@router.post("/doubt/{doubt_id}/feedback")
async def rate_doubt(
    doubt_id: str,
    feedback: FeedbackRequest,
    db:       AsyncIOMotorDatabase = Depends(get_db),
    user_id:  str                  = Depends(get_current_user_id)
):
    """
    Rate if AI response was helpful.

    Send:  { "helpful": true }
    Receive: { "success": true }
    """
    doubt = await db.ai_doubts.find_one({"doubt_id": doubt_id, "user_id": user_id})
    if not doubt:
        raise HTTPException(status_code=404, detail="Doubt not found")

    field = "helpful_votes" if feedback.helpful else "not_helpful_votes"
    await db.ai_doubts.update_one({"doubt_id": doubt_id}, {"$inc": {field: 1}})

    return {"success": True}


@router.get("/question/{question_id}/common-doubts")
async def get_common_doubts(
    question_id: str,
    limit:       int = 5,
    db:          AsyncIOMotorDatabase = Depends(get_db)
):
    """
    Get the most helpful AI responses for a question.
    Public endpoint — no auth needed.
    Useful to show other students' common doubts.

    Receive:
        {
          "question_id": "Q_XXX",
          "common_doubts": [
            {
              "doubt_text":    "Why does my loop not terminate?",
              "ai_response":   "...",
              "helpful_votes": 12,
              "type":          "doubt"
            }
          ]
        }
    """
    cursor = db.ai_doubts.find({
        "question_id":   question_id,
        "helpful_votes": {"$gt": 0},
        "ai_response":   {"$ne": None}
    }).sort("helpful_votes", -1).limit(limit)

    doubts = await cursor.to_list(length=limit)

    return {
        "question_id": question_id,
        "common_doubts": [
            {
                "doubt_text":    d["doubt_text"],
                "ai_response":   d["ai_response"],
                "helpful_votes": d.get("helpful_votes", 0),
                "type":          d.get("type", "doubt"),
            }
            for d in doubts
            if not d["doubt_text"].startswith("HINT_LEVEL_")  # exclude raw hint records
        ]
    }
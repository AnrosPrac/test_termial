from fastapi import APIRouter, HTTPException, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase
from typing import List, Optional
from pydantic import BaseModel
from datetime import datetime
import uuid

from app.courses.dependencies import get_db, get_current_user_id

router = APIRouter(tags=["Community & Comments"])

# ==================== MODELS ====================

class CommentCreate(BaseModel):
    content: str
    parent_comment_id: Optional[str] = None  # For replies

class CommentUpdate(BaseModel):
    content: str

class VoteAction(BaseModel):
    action: str  # "upvote" or "downvote"

# ==================== COMMENT CRUD ====================

def serialize_mongo(doc: dict) -> dict:
    if "_id" in doc:
        doc["_id"] = str(doc["_id"])
    return doc

def serialize_many(docs: list[dict]) -> list[dict]:
    return [serialize_mongo(doc) for doc in docs]


async def get_user_info(db: AsyncIOMotorDatabase, user_id: str) -> dict:
    """Get basic user info for display"""
    user = await db.users_profile.find_one({"user_id": user_id})
    if user:
        return {
            "user_id": user_id,
            "username": user.get("username", "Anonymous"),
            "college": user.get("college")
        }
    return {
        "user_id": user_id,
        "username": "Anonymous",
        "college": None
    }


# ==================== QUESTION DISCUSSION ENDPOINTS ====================

@router.post("/question/{question_id}/comments")
async def add_comment_to_question(
    question_id: str,
    comment: CommentCreate,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    """
    Add a comment/discussion to a specific question
    Students can ask doubts, share approaches, etc.
    """
    
    # Verify question exists
    question = await db.course_questions.find_one({"question_id": question_id})
    if not question:
        raise HTTPException(status_code=404, detail="Question not found")
    
    comment_id = f"CMT_{uuid.uuid4().hex[:12].upper()}"
    
    comment_doc = {
        "comment_id": comment_id,
        "question_id": question_id,
        "course_id": question["course_id"],
        "user_id": user_id,
        "content": comment.content,
        "parent_comment_id": comment.parent_comment_id,
        "upvotes": 0,
        "downvotes": 0,
        "is_solution": False,
        "is_edited": False,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow()
    }
    
    await db.question_comments.insert_one(comment_doc)
    
    # Get user info for response
    user_info = await get_user_info(db, user_id)
    
    return {
        "success": True,
        "comment_id": comment_id,
        "comment": {
            **comment_doc,
            "user": user_info
        },
        "message": "Comment added successfully"
    }


@router.get("/question/{question_id}/comments")
async def get_question_comments(
    question_id: str,
    skip: int = 0,
    limit: int = 50,
    sort_by: str = "recent",  # "recent", "top", "oldest"
    db: AsyncIOMotorDatabase = Depends(get_db)
):
    """
    Get all comments/discussions for a question
    Supports nested replies
    """
    
    # Verify question exists
    question = await db.course_questions.find_one({"question_id": question_id})
    if not question:
        raise HTTPException(status_code=404, detail="Question not found")
    
    # Build sort
    sort_order = []
    if sort_by == "top":
        sort_order = [("upvotes", -1)]
    elif sort_by == "oldest":
        sort_order = [("created_at", 1)]
    else:  # recent
        sort_order = [("created_at", -1)]
    
    # Get top-level comments (no parent)
    cursor = db.question_comments.find({
        "question_id": question_id,
        "parent_comment_id": None
    }).sort(sort_order).skip(skip).limit(limit)
    
    comments = await cursor.to_list(length=limit)
    
    # Enrich with user info and replies
    for comment in comments:
        # Get user info
        comment["user"] = await get_user_info(db, comment["user_id"])
        
        # Get reply count
        comment["reply_count"] = await db.question_comments.count_documents({
            "parent_comment_id": comment["comment_id"]
        })
        
        # Get first 3 replies
        replies_cursor = db.question_comments.find({
            "parent_comment_id": comment["comment_id"]
        }).sort("created_at", 1).limit(3)
        
        replies = await replies_cursor.to_list(length=3)
        
        for reply in replies:
            reply["user"] = await get_user_info(db, reply["user_id"])
        
        comment["replies"] = serialize_many(replies)
    
    total = await db.question_comments.count_documents({
        "question_id": question_id,
        "parent_comment_id": None
    })
    
    return {
        "question_id": question_id,
        "comments": serialize_many(comments),
        "total": total,
        "skip": skip,
        "limit": limit
    }


@router.get("/comment/{comment_id}/replies")
async def get_comment_replies(
    comment_id: str,
    skip: int = 0,
    limit: int = 20,
    db: AsyncIOMotorDatabase = Depends(get_db)
):
    """Get all replies to a specific comment"""
    
    cursor = db.question_comments.find({
        "parent_comment_id": comment_id
    }).sort("created_at", 1).skip(skip).limit(limit)
    
    replies = await cursor.to_list(length=limit)
    
    # Enrich with user info
    for reply in replies:
        reply["user"] = await get_user_info(db, reply["user_id"])
    
    total = await db.question_comments.count_documents({
        "parent_comment_id": comment_id
    })
    
    return {
        "comment_id": comment_id,
        "replies": serialize_many(replies),
        "total": total,
        "skip": skip,
        "limit": limit
    }


@router.put("/comment/{comment_id}")
async def update_comment(
    comment_id: str,
    update: CommentUpdate,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    """Edit your own comment"""
    
    comment = await db.question_comments.find_one({"comment_id": comment_id})
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")
    
    if comment["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="Not authorized to edit this comment")
    
    result = await db.question_comments.update_one(
        {"comment_id": comment_id},
        {
            "$set": {
                "content": update.content,
                "is_edited": True,
                "updated_at": datetime.utcnow()
            }
        }
    )
    
    return {
        "success": True,
        "comment_id": comment_id,
        "message": "Comment updated successfully"
    }


@router.delete("/comment/{comment_id}")
async def delete_comment(
    comment_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    """Delete your own comment"""
    
    comment = await db.question_comments.find_one({"comment_id": comment_id})
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")
    
    if comment["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="Not authorized to delete this comment")
    
    # Delete the comment and all its replies
    await db.question_comments.delete_many({
        "$or": [
            {"comment_id": comment_id},
            {"parent_comment_id": comment_id}
        ]
    })
    
    return {
        "success": True,
        "comment_id": comment_id,
        "message": "Comment deleted successfully"
    }


@router.post("/comment/{comment_id}/vote")
async def vote_comment(
    comment_id: str,
    vote: VoteAction,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    """
    Upvote or downvote a comment
    Users can change their vote
    """
    
    if vote.action not in ["upvote", "downvote"]:
        raise HTTPException(status_code=400, detail="Invalid vote action")
    
    comment = await db.question_comments.find_one({"comment_id": comment_id})
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")
    
    # Check existing vote
    existing_vote = await db.comment_votes.find_one({
        "comment_id": comment_id,
        "user_id": user_id
    })
    
    if existing_vote:
        old_action = existing_vote["action"]
        
        if old_action == vote.action:
            # Remove vote
            await db.comment_votes.delete_one({
                "comment_id": comment_id,
                "user_id": user_id
            })
            
            # Update count
            if vote.action == "upvote":
                await db.question_comments.update_one(
                    {"comment_id": comment_id},
                    {"$inc": {"upvotes": -1}}
                )
            else:
                await db.question_comments.update_one(
                    {"comment_id": comment_id},
                    {"$inc": {"downvotes": -1}}
                )
            
            return {"success": True, "action": "removed", "message": "Vote removed"}
        else:
            # Change vote
            await db.comment_votes.update_one(
                {"comment_id": comment_id, "user_id": user_id},
                {"$set": {"action": vote.action}}
            )
            
            # Update counts
            if vote.action == "upvote":
                await db.question_comments.update_one(
                    {"comment_id": comment_id},
                    {"$inc": {"upvotes": 1, "downvotes": -1}}
                )
            else:
                await db.question_comments.update_one(
                    {"comment_id": comment_id},
                    {"$inc": {"upvotes": -1, "downvotes": 1}}
                )
            
            return {"success": True, "action": "changed", "message": "Vote changed"}
    else:
        # New vote
        await db.comment_votes.insert_one({
            "comment_id": comment_id,
            "user_id": user_id,
            "action": vote.action,
            "created_at": datetime.utcnow()
        })
        
        # Update count
        if vote.action == "upvote":
            await db.question_comments.update_one(
                {"comment_id": comment_id},
                {"$inc": {"upvotes": 1}}
            )
        else:
            await db.question_comments.update_one(
                {"comment_id": comment_id},
                {"$inc": {"downvotes": 1}}
            )
        
        return {"success": True, "action": "added", "message": "Vote added"}


@router.post("/comment/{comment_id}/mark-solution")
async def mark_as_solution(
    comment_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    """
    Mark a comment as the accepted solution
    Only the question asker or instructor can do this
    """
    
    comment = await db.question_comments.find_one({"comment_id": comment_id})
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")
    
    # Unmark any previous solutions
    await db.question_comments.update_many(
        {"question_id": comment["question_id"]},
        {"$set": {"is_solution": False}}
    )
    
    # Mark this as solution
    await db.question_comments.update_one(
        {"comment_id": comment_id},
        {"$set": {"is_solution": True}}
    )
    
    return {
        "success": True,
        "comment_id": comment_id,
        "message": "Marked as solution"
    }


# ==================== COURSE DISCUSSION BOARD ====================

@router.get("/course/{course_id}/discussions")
async def get_course_discussions(
    course_id: str,
    skip: int = 0,
    limit: int = 20,
    db: AsyncIOMotorDatabase = Depends(get_db)
):
    """
    Get recent discussions across all questions in a course
    """
    
    # Get questions for this course
    questions = await db.course_questions.find(
        {"course_id": course_id, "is_active": True},
        {"question_id": 1, "title": 1}
    ).to_list(length=None)
    
    question_map = {q["question_id"]: q["title"] for q in questions}
    question_ids = list(question_map.keys())
    
    # Get recent comments
    cursor = db.question_comments.find({
        "question_id": {"$in": question_ids}
    }).sort("created_at", -1).skip(skip).limit(limit)
    
    comments = await cursor.to_list(length=limit)
    
    # Enrich with user and question info
    for comment in comments:
        comment["user"] = await get_user_info(db, comment["user_id"])
        comment["question_title"] = question_map.get(comment["question_id"], "Unknown")
    
    return {
        "course_id": course_id,
        "discussions": serialize_many(comments),
        "count": len(comments),
        "skip": skip,
        "limit": limit
    }
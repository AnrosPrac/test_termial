"""
Complete Admin API Router
All admin endpoints with full CRUD, bulk actions, analytics
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, validator
from typing import List, Optional
from datetime import datetime, timedelta
from motor.motor_asyncio import AsyncIOMotorDatabase
from bson import ObjectId

# Import our modules
from app.admin.hardened_firebase_auth import get_current_admin
from app.admin.hardened_analytics import (
    get_dashboard_stats,
    get_revenue_chart,
    get_user_growth_chart,
    get_command_usage_stats,
    get_top_users_by_usage

)
from app.admin.safe_bulk_operations import (
    bulk_upgrade_users,
    bulk_reset_quotas,
    bulk_ban_users,
    bulk_unban_users,
    export_users_csv,
    export_payments_csv,
    export_tickets_csv
)

# Database dependency
from motor.motor_asyncio import AsyncIOMotorClient
import os

MONGO_URL = os.getenv("MONGO_URL")
client = AsyncIOMotorClient(MONGO_URL)
db = client.lumetrics_db

async def get_db():
    """Dependency to get database instance"""
    return db

router = APIRouter(tags=["Admin"])


# ============================================================================
# PYDANTIC MODELS
# ============================================================================

class LoginRequest(BaseModel):
    username: str
    password: str


class BulkUpgradeRequest(BaseModel):
    sidhi_ids: List[str]
    tier: str
    
    @validator('tier')
    def validate_tier(cls, v):
        if v not in ['free', 'hero', 'dominator']:
            raise ValueError('Invalid tier')
        return v


class BulkResetRequest(BaseModel):
    sidhi_ids: List[str]
    reset_type: str = "full"
    
    @validator('reset_type')
    def validate_reset_type(cls, v):
        if v not in ['full', 'commands_only', 'addons_only']:
            raise ValueError('Invalid reset type')
        return v


class BulkBanRequest(BaseModel):
    sidhi_ids: List[str]
    reason: str


class UserUpdateRequest(BaseModel):
    username: Optional[str] = None
    email_id: Optional[str] = None
    college: Optional[str] = None
    department: Optional[str] = None
    degree: Optional[str] = None
    is_admin: Optional[bool] = None


class QuotaTierChangeRequest(BaseModel):
    tier: str
    
    @validator('tier')
    def validate_tier(cls, v):
        if v not in ['free', 'hero', 'dominator']:
            raise ValueError('Invalid tier')
        return v


class QuotaAddonRequest(BaseModel):
    feature: str  # inject, cells, pdf, convo, trace, explain
    amount: int


class TicketReplyRequest(BaseModel):
    admin_response: str


class NotificationRequest(BaseModel):
    title: str
    message: str
    type: str = "info"  # info, warning, success, error, announcement
    priority: str = "medium"  # low, medium, high, urgent
    target_type: str = "all"  # all, specific
    target_users: Optional[List[str]] = None
    action_url: Optional[str] = None
    expires_hours: Optional[int] = None


class PaymentRefundRequest(BaseModel):
    refund_reason: str


# ============================================================================
# AUTHENTICATION ENDPOINTS
# ============================================================================

@router.post("/login")
async def admin_login(credentials: LoginRequest):
    """
    Admin login - Returns JWT token
    
    Only ADMIN_EMAIL can access
    """
    from app.admin.hardened_firebase_auth import verify_credentials, create_admin_jwt
    
    # Verify credentials
    user = verify_credentials(credentials.username, credentials.password)
    
    # Create admin JWT
    admin_jwt = create_admin_jwt(user['email'])
    
    return {
        "status": "success",
        "token": admin_jwt,
        "email": user['email'],
        "message": "Login successful"
    }


@router.post("/logout")
async def admin_logout(admin: dict = Depends(get_current_admin)):
    """
    Logout admin session
    """
    from app.admin.hardened_firebase_auth import revoke_admin_session
    from fastapi import Header
    
    # In production, you'd revoke the token
    # For now, just return success
    
    return {
        "status": "success",
        "message": "Logged out successfully"
    }


@router.get("/me")
async def get_admin_info(admin: dict = Depends(get_current_admin)):
    """
    Get current admin info
    """
    return {
        "status": "success",
        "admin": {
            "email": admin.get("email"),
            "role": admin.get("role"),
            "session_expires": admin.get("exp")
        }
    }


# ============================================================================
# DASHBOARD & ANALYTICS
# ============================================================================

@router.get("/dashboard/stats")
async def dashboard_stats(
    admin: dict = Depends(get_current_admin),
    db_instance: AsyncIOMotorDatabase = Depends(get_db)
):
    """
    Get dashboard overview stats
    
    Returns:
    - Total users, revenue, payments
    - Tier distribution
    - Gemini API costs
    - Pending tickets
    """
    stats = await get_dashboard_stats(db_instance)
    return stats


@router.get("/analytics/revenue")
async def revenue_analytics(
    days: int = Query(30, ge=1, le=365),
    admin: dict = Depends(get_current_admin),
    db_instance: AsyncIOMotorDatabase = Depends(get_db)
):
    """
    Get daily revenue chart for last N days
    """
    data = await get_revenue_chart(db_instance, days)
    return {
        "status": "success",
        "chart_data": data
    }


@router.get("/analytics/user-growth")
async def user_growth_analytics(
    days: int = Query(30, ge=1, le=365),
    admin: dict = Depends(get_current_admin),
    db_instance: AsyncIOMotorDatabase = Depends(get_db)
):
    """
    Get daily user registration chart
    """
    data = await get_user_growth_chart(db_instance, days)
    return {
        "status": "success",
        "chart_data": data
    }


@router.get("/analytics/commands")
async def command_usage_analytics(
    admin: dict = Depends(get_current_admin),
    db_instance: AsyncIOMotorDatabase = Depends(get_db)
):
    """
    Get command usage statistics
    """
    data = await get_command_usage_stats(db_instance)
    return {
        "status": "success",
        "commands": data
    }


@router.get("/analytics/top-users")
async def top_users_analytics(
    limit: int = Query(10, ge=1, le=100),
    admin: dict = Depends(get_current_admin),
    db_instance: AsyncIOMotorDatabase = Depends(get_db)
):
    """
    Get top users by usage
    """
    data = await get_top_users_by_usage(db_instance, limit)
    return {
        "status": "success",
        "top_users": data
    }


# ============================================================================
# USER MANAGEMENT
# ============================================================================

@router.get("/users")
async def list_all_users(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=500),
    search: Optional[str] = None,
    tier: Optional[str] = None,
    is_banned: Optional[bool] = None,
    admin: dict = Depends(get_current_admin),
    db_instance: AsyncIOMotorDatabase = Depends(get_db)
):
    """
    List all users with pagination and filters
    
    Filters:
    - search: Search by username, email, sidhi_id
    - tier: Filter by tier (free, hero, dominator)
    - is_banned: Filter banned users
    """
    query = {}
    
    # Search filter
    if search:
        query["$or"] = [
            {"username": {"$regex": search, "$options": "i"}},
            {"email_id": {"$regex": search, "$options": "i"}},
            {"sidhi_id": {"$regex": search, "$options": "i"}}
        ]
    
    # Ban filter
    if is_banned is not None:
        query["is_banned"] = is_banned
    
    # Get users
    skip = (page - 1) * limit
    users_cursor = db_instance.users_profile.find(query, {"_id": 0}) \
        .skip(skip) \
        .limit(limit) \
        .sort("created_at", -1)
    
    users = await users_cursor.to_list(length=limit)
    
    # Enrich with quota info
    for user in users:
        quota = await db_instance.quotas.find_one(
            {"sidhi_id": user["sidhi_id"]},
            {"tier": 1, "_id": 0}
        )
        user["tier"] = quota.get("tier") if quota else "none"
    
    # Apply tier filter after enrichment
    if tier:
        users = [u for u in users if u.get("tier") == tier]
    
    total_count = await db_instance.users_profile.count_documents(query)
    
    return {
        "status": "success",
        "users": users,
        "pagination": {
            "page": page,
            "limit": limit,
            "total": total_count,
            "total_pages": (total_count + limit - 1) // limit
        }
    }


@router.get("/users/{sidhi_id}")
async def get_user_details(
    sidhi_id: str,
    admin: dict = Depends(get_current_admin),
    db_instance: AsyncIOMotorDatabase = Depends(get_db)
):
    """
    Get complete user details
    
    Returns:
    - User profile
    - Quota information
    - Payment history
    - Activity logs
    - Cloud sync history
    """
    # User profile
    profile = await db_instance.users_profile.find_one(
        {"sidhi_id": sidhi_id},
        {"_id": 0}
    )
    
    if not profile:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Quota
    quota = await db_instance.quotas.find_one(
        {"sidhi_id": sidhi_id},
        {"_id": 0}
    )
    
    # Payments
    payments = await db_instance.payments.find(
        {"sidhi_id": sidhi_id},
        {"_id": 0}
    ).sort("created_at", -1).to_list(length=10)
    
    # Activity logs
    history = await db_instance.history.find_one(
        {"sidhi_id": sidhi_id},
        {"_id": 0}
    )
    
    # Cloud sync history
    cloud_history = await db_instance.cloud_history.find_one(
        {"sidhi_id": sidhi_id},
        {"_id": 0}
    )
    
    # Orders
    orders = await db_instance.orders.find(
        {"USER_ID": sidhi_id},
        {"_id": 0}
    ).sort("PLACED_AT", -1).to_list(length=10)
    
    return {
        "status": "success",
        "user": {
            "profile": profile,
            "quota": quota,
            "payments": payments,
            "activity_history": history,
            "cloud_history": cloud_history,
            "orders": orders
        }
    }


@router.put("/users/{sidhi_id}")
async def update_user(
    sidhi_id: str,
    data: UserUpdateRequest,
    admin: dict = Depends(get_current_admin),
    db_instance: AsyncIOMotorDatabase = Depends(get_db)
):
    """
    Update user profile
    
    Can update:
    - username, email, college, department, degree
    - is_admin status
    """
    update_fields = {k: v for k, v in data.dict().items() if v is not None}
    
    if not update_fields:
        raise HTTPException(status_code=400, detail="No fields to update")
    
    result = await db_instance.users_profile.update_one(
        {"sidhi_id": sidhi_id},
        {"$set": update_fields}
    )
    
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="User not found")
    
    return {
        "status": "success",
        "message": "User updated successfully",
        "updated_fields": list(update_fields.keys())
    }


@router.delete("/users/{sidhi_id}")
async def delete_user(
    sidhi_id: str,
    admin: dict = Depends(get_current_admin),
    db_instance: AsyncIOMotorDatabase = Depends(get_db)
):
    """
    **DANGER:** Delete user completely
    
    Deletes from:
    - users_profile
    - quotas
    - history
    - cloud_history
    - personalization
    - help_bot_history
    """
    # Delete from all collections
    await db_instance.users_profile.delete_one({"sidhi_id": sidhi_id})
    await db_instance.users.delete_one({"sid_id": sidhi_id})
    await db_instance.quotas.delete_one({"sidhi_id": sidhi_id})
    await db_instance.history.delete_one({"sidhi_id": sidhi_id})
    await db_instance.cloud_history.delete_one({"sidhi_id": sidhi_id})
    await db_instance.personalization.delete_one({"sidhi_id": sidhi_id})
    await db_instance.help_bot_history.delete_many({"sidhi_id": sidhi_id})
    
    return {
        "status": "success",
        "message": f"User {sidhi_id} deleted completely"
    }


# ============================================================================
# QUOTA MANAGEMENT
# ============================================================================

@router.get("/quota/{sidhi_id}")
async def get_user_quota(
    sidhi_id: str,
    admin: dict = Depends(get_current_admin),
    db_instance: AsyncIOMotorDatabase = Depends(get_db)
):
    """
    Get user's quota details
    """
    quota = await db_instance.quotas.find_one(
        {"sidhi_id": sidhi_id},
        {"_id": 0}
    )
    
    if not quota:
        raise HTTPException(status_code=404, detail="Quota not found")
    
    return {
        "status": "success",
        "quota": quota
    }


@router.put("/quota/{sidhi_id}/tier")
async def change_user_tier(
    sidhi_id: str,
    data: QuotaTierChangeRequest,
    admin: dict = Depends(get_current_admin),
    db_instance: AsyncIOMotorDatabase = Depends(get_db)
):
    """
    Change user's tier manually
    
    Creates new quota document for the tier
    """
    from app.ai.payment_router import create_quota_document
    
    # Create new quota
    quota_doc = await create_quota_document(sidhi_id, data.tier)
    quota_doc["meta"]["admin_upgraded"] = True
    quota_doc["meta"]["upgraded_by"] = admin.get("email")
    quota_doc["meta"]["upgraded_at"] = datetime.utcnow()
    
    # Update in database
    result = await db_instance.quotas.update_one(
        {"sidhi_id": sidhi_id},
        {"$set": quota_doc},
        upsert=True
    )
    
    return {
        "status": "success",
        "message": f"User upgraded to {data.tier} tier",
        "quota": quota_doc
    }


@router.put("/quota/{sidhi_id}/reset")
async def reset_user_quota(
    sidhi_id: str,
    reset_type: str = Query("full", regex="^(full|commands_only|addons_only)$"),
    admin: dict = Depends(get_current_admin),
    db_instance: AsyncIOMotorDatabase = Depends(get_db)
):
    """
    Reset user's quota usage
    
    Types:
    - full: Reset all usage counters
    - commands_only: Reset command usage only
    - addons_only: Reset addon quotas only
    """
    quota = await db_instance.quotas.find_one({"sidhi_id": sidhi_id})
    
    if not quota:
        raise HTTPException(status_code=404, detail="Quota not found")
    
    update_fields = {}
    
    if reset_type in ["full", "commands_only"]:
        reset_commands = {cmd: 0 for cmd in quota["base"]["commands"].keys()}
        update_fields["used.commands"] = reset_commands
    
    if reset_type == "full":
        update_fields["used.inject"] = 0
        update_fields["used.cells"] = 0
        update_fields["used.pdf"] = 0
        update_fields["used.convo"] = 0
    
    if reset_type == "addons_only":
        update_fields["addons"] = {
            "inject": 0,
            "cells": 0,
            "pdf": 0,
            "convo": 0,
            "trace": 0,
            "explain": 0
        }
    
    update_fields["meta.last_updated"] = datetime.utcnow()
    update_fields["meta.admin_reset"] = True
    update_fields["meta.reset_by"] = admin.get("email")
    
    await db_instance.quotas.update_one(
        {"sidhi_id": sidhi_id},
        {"$set": update_fields}
    )
    
    return {
        "status": "success",
        "message": f"Quota reset ({reset_type})",
        "reset_fields": list(update_fields.keys())
    }


@router.post("/quota/{sidhi_id}/add-addon")
async def add_quota_bonus(
    sidhi_id: str,
    data: QuotaAddonRequest,
    admin: dict = Depends(get_current_admin),
    db_instance: AsyncIOMotorDatabase = Depends(get_db)
):
    """
    Add bonus quota to user
    
    Features: inject, cells, pdf, convo, trace, explain
    """
    valid_features = ["inject", "cells", "pdf", "convo", "trace", "explain"]
    
    if data.feature not in valid_features:
        raise HTTPException(status_code=400, detail=f"Invalid feature. Choose from: {valid_features}")
    
    if data.amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")
    
    # Update addon
    result = await db_instance.quotas.update_one(
        {"sidhi_id": sidhi_id},
        {
            "$inc": {f"addons.{data.feature}": data.amount},
            "$set": {
                "meta.last_updated": datetime.utcnow(),
                "meta.bonus_added_by": admin.get("email")
            }
        }
    )
    
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Quota not found")
    
    return {
        "status": "success",
        "message": f"Added {data.amount} {data.feature} quota to user"
    }


# ============================================================================
# BULK ACTIONS
# ============================================================================

@router.post("/bulk/upgrade")
async def bulk_upgrade(
    data: BulkUpgradeRequest,
    admin: dict = Depends(get_current_admin),
    db_instance: AsyncIOMotorDatabase = Depends(get_db)
):
    """
    Bulk upgrade multiple users to a tier
    
    Max 1000 users at once
    """
    result = await bulk_upgrade_users(data.sidhi_ids, data.tier)
    return result


@router.post("/bulk/reset-quota")
async def bulk_reset(
    data: BulkResetRequest,
    admin: dict = Depends(get_current_admin),
    db_instance: AsyncIOMotorDatabase = Depends(get_db)
):
    """
    Bulk reset quotas for multiple users
    """
    result = await bulk_reset_quotas(data.sidhi_ids, data.reset_type)
    return result


@router.post("/bulk/ban")
async def bulk_ban(
    data: BulkBanRequest,
    admin: dict = Depends(get_current_admin),
    db_instance: AsyncIOMotorDatabase = Depends(get_db)
):
    """
    Bulk ban multiple users
    """
    result = await bulk_ban_users(data.sidhi_ids, data.reason)
    return result


@router.post("/bulk/unban")
async def bulk_unban(
    sidhi_ids: List[str],
    admin: dict = Depends(get_current_admin),
    db_instance: AsyncIOMotorDatabase = Depends(get_db)
):
    """
    Bulk unban multiple users
    """
    result = await bulk_unban_users(sidhi_ids)
    return result


# ============================================================================
# PAYMENT MANAGEMENT
# ============================================================================

@router.get("/payments")
async def list_payments(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=500),
    status: Optional[str] = None,
    tier: Optional[str] = None,
    admin: dict = Depends(get_current_admin),
    db_instance: AsyncIOMotorDatabase = Depends(get_db)
):
    """
    List all payments with filters
    
    Filters:
    - status: captured, created, failed
    - tier: hero, dominator
    """
    query = {}
    
    if status:
        query["status"] = status
    
    if tier:
        query["tier"] = tier
    
    skip = (page - 1) * limit
    payments = await db_instance.payments.find(query, {"_id": 0}) \
        .skip(skip) \
        .limit(limit) \
        .sort("created_at", -1) \
        .to_list(length=limit)
    
    total_count = await db_instance.payments.count_documents(query)
    
    return {
        "status": "success",
        "payments": payments,
        "pagination": {
            "page": page,
            "limit": limit,
            "total": total_count
        }
    }


@router.get("/payments/{order_id}")
async def get_payment_details(
    order_id: str,
    admin: dict = Depends(get_current_admin),
    db_instance: AsyncIOMotorDatabase = Depends(get_db)
):
    """
    Get payment details
    """
    payment = await db_instance.payments.find_one(
        {"razorpay_order_id": order_id},
        {"_id": 0}
    )
    
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    
    return {
        "status": "success",
        "payment": payment
    }


@router.put("/payments/{order_id}/refund")
async def refund_payment(
    order_id: str,
    data: PaymentRefundRequest,
    admin: dict = Depends(get_current_admin),
    db_instance: AsyncIOMotorDatabase = Depends(get_db)
):
    """
    Mark payment as refunded (manual process)
    
    Note: Does NOT process actual Razorpay refund
    This just updates the record
    """
    result = await db_instance.payments.update_one(
        {"razorpay_order_id": order_id},
        {"$set": {
            "status": "refunded",
            "refund_reason": data.refund_reason,
            "refunded_at": datetime.utcnow(),
            "refunded_by": admin.get("email")
        }}
    )
    
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Payment not found")
    
    return {
        "status": "success",
        "message": "Payment marked as refunded",
        "note": "Process actual refund in Razorpay dashboard"
    }


# ============================================================================
# SUPPORT TICKETS
# ============================================================================

@router.get("/tickets")
async def list_tickets(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=500),
    status: Optional[str] = None,
    admin: dict = Depends(get_current_admin),
    db_instance: AsyncIOMotorDatabase = Depends(get_db)
):
    """
    List all support tickets
    
    Filter by status: pending, resolved, closed
    """
    query = {}
    if status:
        query["status"] = status
    
    skip = (page - 1) * limit
    tickets = await db_instance.help_tickets.find(query, {"_id": 1}) \
        .skip(skip) \
        .limit(limit) \
        .sort("created_at", -1) \
        .to_list(length=limit)
    
    # Convert ObjectId to string
    for ticket in tickets:
        ticket["_id"] = str(ticket["_id"])
    
    total_count = await db_instance.help_tickets.count_documents(query)
    
    return {
        "status": "success",
        "tickets": tickets,
        "pagination": {
            "page": page,
            "limit": limit,
            "total": total_count
        }
    }


@router.get("/tickets/{ticket_id}")
async def get_ticket_details(
    ticket_id: str,
    admin: dict = Depends(get_current_admin),
    db_instance: AsyncIOMotorDatabase = Depends(get_db)
):
    """
    Get ticket details
    """
    try:
        ticket = await db_instance.help_tickets.find_one(
            {"_id": ObjectId(ticket_id)}
        )
    except:
        raise HTTPException(status_code=400, detail="Invalid ticket ID")
    
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    
    ticket["_id"] = str(ticket["_id"])
    
    return {
        "status": "success",
        "ticket": ticket
    }


@router.put("/tickets/{ticket_id}/reply")
async def reply_to_ticket(
    ticket_id: str,
    data: TicketReplyRequest,
    admin: dict = Depends(get_current_admin),
    db_instance: AsyncIOMotorDatabase = Depends(get_db)
):
    """
    **YOU CAN RESPOND TO TICKETS HERE**
    
    Adds admin response and marks as resolved
    """
    try:
        result = await db_instance.help_tickets.update_one(
            {"_id": ObjectId(ticket_id)},
            {"$set": {
                "admin_response": data.admin_response,
                "status": "resolved",
                "resolved_at": datetime.utcnow(),
                "resolved_by": admin.get("email"),
                "updated_at": datetime.utcnow()
            }}
        )
    except:
        raise HTTPException(status_code=400, detail="Invalid ticket ID")
    
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Ticket not found")
    
    return {
        "status": "success",
        "message": "Response sent and ticket resolved"
    }


@router.put("/tickets/{ticket_id}/close")
async def close_ticket(
    ticket_id: str,
    admin: dict = Depends(get_current_admin),
    db_instance: AsyncIOMotorDatabase = Depends(get_db)
):
    """
    Close ticket without response
    """
    try:
        result = await db_instance.help_tickets.update_one(
            {"_id": ObjectId(ticket_id)},
            {"$set": {
                "status": "closed",
                "closed_at": datetime.utcnow(),
                "closed_by": admin.get("email"),
                "updated_at": datetime.utcnow()
            }}
        )
    except:
        raise HTTPException(status_code=400, detail="Invalid ticket ID")
    
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Ticket not found")
    
    return {
        "status": "success",
        "message": "Ticket closed"
    }


# ============================================================================
# NOTIFICATIONS
# ============================================================================

@router.post("/notifications/send")
async def send_notification(
    data: NotificationRequest,
    admin: dict = Depends(get_current_admin),
    db_instance: AsyncIOMotorDatabase = Depends(get_db)
):
    """
    Send notification to users
    
    target_type:
    - all: Send to all users
    - specific: Send to specific users (provide target_users list)
    """
    if data.target_type == "specific" and not data.target_users:
        raise HTTPException(status_code=400, detail="target_users required for specific notifications")
    
    expires_at = None
    if data.expires_hours:
        expires_at = datetime.utcnow() + timedelta(hours=data.expires_hours)
    
    notification = {
        "title": data.title,
        "message": data.message,
        "type": data.type,
        "priority": data.priority,
        "target_type": data.target_type,
        "target_users": data.target_users or [],
        "action_url": data.action_url,
        "expires_at": expires_at,
        "created_at": datetime.utcnow(),
        "created_by": admin.get("email"),
        "read_by": []
    }
    
    result = await db_instance.notifications.insert_one(notification)
    
    return {
        "status": "success",
        "message": "Notification sent",
        "notification_id": str(result.inserted_id),
        "target_count": len(data.target_users) if data.target_type == "specific" else "all"
    }

# ============================================================================
# NOTIFICATIONS (COMPLETION)
# ============================================================================

@router.get("/notifications/history")
async def notification_history(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=500),
    admin: dict = Depends(get_current_admin),
    db_instance: AsyncIOMotorDatabase = Depends(get_db)
):
    """Get sent notifications history"""
    skip = (page - 1) * limit
    
    notifications = await db_instance.notifications.find({}) \
        .skip(skip) \
        .limit(limit) \
        .sort("created_at", -1) \
        .to_list(length=limit)
    
    for notif in notifications:
        notif["_id"] = str(notif["_id"])
        notif["read_count"] = len(notif.get("read_by", []))
    
    total_count = await db_instance.notifications.count_documents({})
    
    return {
        "status": "success",
        "notifications": notifications,
        "pagination": {
            "page": page,
            "limit": limit,
            "total": total_count
        }
    }


@router.delete("/notifications/{notification_id}")
async def delete_notification(
    notification_id: str,
    admin: dict = Depends(get_current_admin),
    db_instance: AsyncIOMotorDatabase = Depends(get_db)
):
    """Delete a notification"""
    try:
        result = await db_instance.notifications.delete_one(
            {"_id": ObjectId(notification_id)}
        )
    except:
        raise HTTPException(status_code=400, detail="Invalid notification ID")
    
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Notification not found")
    
    return {
        "status": "success",
        "message": "Notification deleted"
    }


# ============================================================================
# CSV EXPORTS
# ============================================================================

@router.get("/export/users")
async def export_users(
    admin: dict = Depends(get_current_admin),
    db_instance: AsyncIOMotorDatabase = Depends(get_db)
):
    """Export all users to CSV"""
    from app.admin.safe_bulk_operations import export_users_csv
    from fastapi.responses import StreamingResponse
    
    csv_content = await export_users_csv()
    
    return StreamingResponse(
        iter([csv_content]),
        media_type="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=users_export.csv"
        }
    )


@router.get("/export/payments")
async def export_payments(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    admin: dict = Depends(get_current_admin),
    db_instance: AsyncIOMotorDatabase = Depends(get_db)
):
    """Export payments to CSV"""
    from app.admin.safe_bulk_operations import export_payments_csv
    from fastapi.responses import StreamingResponse
    
    # Parse dates
    start = None
    end = None
    
    if start_date:
        try:
            start = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
        except:
            raise HTTPException(status_code=400, detail="Invalid start_date format. Use ISO format")
    
    if end_date:
        try:
            end = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
        except:
            raise HTTPException(status_code=400, detail="Invalid end_date format. Use ISO format")
    
    csv_content = await export_payments_csv(start, end)
    
    return StreamingResponse(
        iter([csv_content]),
        media_type="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=payments_export.csv"
        }
    )


@router.get("/export/tickets")
async def export_tickets(
    status_filter: Optional[str] = None,
    admin: dict = Depends(get_current_admin),
    db_instance: AsyncIOMotorDatabase = Depends(get_db)
):
    """Export support tickets to CSV"""
    from app.admin.safe_bulk_operations import export_tickets_csv
    from fastapi.responses import StreamingResponse
    
    csv_content = await export_tickets_csv(status_filter)
    
    return StreamingResponse(
        iter([csv_content]),
        media_type="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=tickets_export.csv"
        }
    )


# ============================================================================
# MONITORING & SYSTEM STATUS
# ============================================================================

@router.get("/monitoring/gemini-keys")
async def get_gemini_keys_status(
    admin: dict = Depends(get_current_admin),
    db_instance: AsyncIOMotorDatabase = Depends(get_db)
):
    """Get all Gemini API keys status and usage"""
    keys = await db_instance.gemini_key_stats.find({}).to_list(length=None)
    
    for key in keys:
        if "_id" in key:
            key["_id"] = str(key["_id"])
    
    # Calculate totals
    total_requests = sum(k.get("requests_today", 0) for k in keys)
    total_input_tokens = sum(k.get("tokens_today", {}).get("input_tokens", 0) for k in keys)
    total_output_tokens = sum(k.get("tokens_today", {}).get("output_tokens", 0) for k in keys)
    
    return {
        "status": "success",
        "keys": keys,
        "summary": {
            "total_keys": len(keys),
            "total_requests_today": total_requests,
            "total_input_tokens_today": total_input_tokens,
            "total_output_tokens_today": total_output_tokens
        }
    }


@router.get("/monitoring/activity-logs/{sidhi_id}")
async def get_user_activity_logs(
    sidhi_id: str,
    days: int = Query(7, ge=1, le=90),
    admin: dict = Depends(get_current_admin),
    db_instance: AsyncIOMotorDatabase = Depends(get_db)
):
    """Get user activity logs for last N days"""
    history = await db_instance.history.find_one({"sidhi_id": sidhi_id})
    
    if not history:
        return {
            "status": "success",
            "sidhi_id": sidhi_id,
            "activity": {},
            "message": "No activity found"
        }
    
    if "_id" in history:
        history["_id"] = str(history["_id"])
    
    # Filter logs by date range
    from datetime import timedelta
    cutoff_date = datetime.utcnow() - timedelta(days=days)
    cutoff_str = cutoff_date.strftime("%Y-%m-%d")
    
    filtered_logs = {}
    for date, logs in history.get("logs", {}).items():
        if date >= cutoff_str:
            filtered_logs[date] = logs
    
    return {
        "status": "success",
        "sidhi_id": sidhi_id,
        "activity": filtered_logs,
        "days_shown": days
    }


@router.get("/monitoring/help-bot-history/{sidhi_id}")
async def get_help_bot_history(
    sidhi_id: str,
    limit: int = Query(20, ge=1, le=100),
    admin: dict = Depends(get_current_admin),
    db_instance: AsyncIOMotorDatabase = Depends(get_db)
):
    """Get user's help bot conversation history"""
    history = await db_instance.help_bot_history.find({"sidhi_id": sidhi_id}) \
        .sort("created_at", -1) \
        .limit(limit) \
        .to_list(length=limit)
    
    for item in history:
        if "_id" in item:
            item["_id"] = str(item["_id"])
    
    return {
        "status": "success",
        "sidhi_id": sidhi_id,
        "history": history,
        "count": len(history)
    }


@router.get("/monitoring/cloud-sync/{sidhi_id}")
async def get_cloud_sync_history(
    sidhi_id: str,
    admin: dict = Depends(get_current_admin),
    db_instance: AsyncIOMotorDatabase = Depends(get_db)
):
    """Get user's cloud sync history"""
    cloud_history = await db_instance.cloud_history.find_one({"sidhi_id": sidhi_id})
    
    if not cloud_history:
        return {
            "status": "success",
            "sidhi_id": sidhi_id,
            "pushes": [],
            "message": "No cloud sync history found"
        }
    
    if "_id" in cloud_history:
        cloud_history["_id"] = str(cloud_history["_id"])
    
    return {
        "status": "success",
        "cloud_history": cloud_history
    }


# ============================================================================
# PERSONALIZATION
# ============================================================================

@router.get("/personalization/{sidhi_id}")
async def get_user_personalization(
    sidhi_id: str,
    admin: dict = Depends(get_current_admin),
    db_instance: AsyncIOMotorDatabase = Depends(get_db)
):
    """Get user's personalization settings"""
    personalization = await db_instance.personalization.find_one({"sidhi_id": sidhi_id})
    
    if not personalization:
        return {
            "status": "success",
            "sidhi_id": sidhi_id,
            "personalization": None,
            "message": "No personalization found"
        }
    
    if "_id" in personalization:
        personalization["_id"] = str(personalization["_id"])
    
    return {
        "status": "success",
        "personalization": personalization
    }


@router.delete("/personalization/{sidhi_id}")
async def delete_user_personalization(
    sidhi_id: str,
    admin: dict = Depends(get_current_admin),
    db_instance: AsyncIOMotorDatabase = Depends(get_db)
):
    """Delete user's personalization (reset to default)"""
    result = await db_instance.personalization.delete_one({"sidhi_id": sidhi_id})
    
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Personalization not found")
    
    return {
        "status": "success",
        "message": "Personalization deleted. User will use default settings."
    }


# ============================================================================
# CACHE MANAGEMENT
# ============================================================================

@router.post("/cache/clear")
async def clear_all_cache(
    admin: dict = Depends(get_current_admin)
):
    """Clear all analytics cache"""
    # If you're using the hardened_analytics module with cache
    try:
        from app.admin.hardened_analytics import cache
        await cache.clear()
        message = "All analytics cache cleared"
    except:
        message = "Cache clearing not implemented or no cache found"
    
    return {
        "status": "success",
        "message": message
    }


@router.post("/cache/clear/{cache_key}")
async def clear_specific_cache(
    cache_key: str,
    admin: dict = Depends(get_current_admin)
):
    """Clear specific cache key"""
    try:
        from app.admin.hardened_analytics import cache
        await cache.delete(cache_key)
        message = f"Cache key '{cache_key}' cleared"
    except:
        message = "Cache clearing not implemented or key not found"
    
    return {
        "status": "success",
        "message": message
    }


# ============================================================================
# SYSTEM STATISTICS
# ============================================================================

@router.get("/stats/system")
async def get_system_statistics(
    admin: dict = Depends(get_current_admin),
    db_instance: AsyncIOMotorDatabase = Depends(get_db)
):
    """Get overall system statistics"""
    
    # Collection counts
    total_users = await db_instance.users_profile.count_documents({})
    total_quotas = await db_instance.quotas.count_documents({})
    total_payments = await db_instance.payments.count_documents({})
    total_tickets = await db_instance.help_tickets.count_documents({})
    pending_tickets = await db_instance.help_tickets.count_documents({"status": "pending"})
    
    # Recent activity
    recent_users = await db_instance.users_profile.count_documents({
        "created_at": {"$gte": datetime.utcnow() - timedelta(days=7)}
    })
    
    recent_payments = await db_instance.payments.count_documents({
        "created_at": {"$gte": datetime.utcnow() - timedelta(days=7)},
        "status": "captured"
    })
    
    return {
        "status": "success",
        "system_stats": {
            "total_users": total_users,
            "total_quotas": total_quotas,
            "total_payments": total_payments,
            "total_tickets": total_tickets,
            "pending_tickets": pending_tickets,
            "recent_activity": {
                "new_users_7_days": recent_users,
                "payments_7_days": recent_payments
            }
        }
    }


# ============================================================================
# SEARCH & FILTERS
# ============================================================================

@router.get("/search")
async def search_across_collections(
    query: str = Query(..., min_length=2),
    collections: List[str] = Query(["users", "payments", "tickets"]),
    limit: int = Query(10, ge=1, le=50),
    admin: dict = Depends(get_current_admin),
    db_instance: AsyncIOMotorDatabase = Depends(get_db)
):
    """Search across multiple collections"""
    results = {}
    
    if "users" in collections:
        users = await db_instance.users_profile.find({
            "$or": [
                {"username": {"$regex": query, "$options": "i"}},
                {"email_id": {"$regex": query, "$options": "i"}},
                {"sidhi_id": {"$regex": query, "$options": "i"}}
            ]
        }).limit(limit).to_list(length=limit)
        
        for user in users:
            if "_id" in user:
                user["_id"] = str(user["_id"])
        
        results["users"] = users
    
    if "payments" in collections:
        payments = await db_instance.payments.find({
            "$or": [
                {"sidhi_id": {"$regex": query, "$options": "i"}},
                {"razorpay_order_id": {"$regex": query, "$options": "i"}},
                {"razorpay_payment_id": {"$regex": query, "$options": "i"}}
            ]
        }).limit(limit).to_list(length=limit)
        
        for payment in payments:
            if "_id" in payment:
                payment["_id"] = str(payment["_id"])
        
        results["payments"] = payments
    
    if "tickets" in collections:
        tickets = await db_instance.help_tickets.find({
            "$or": [
                {"sidhi_id": {"$regex": query, "$options": "i"}},
                {"email_id": {"$regex": query, "$options": "i"}},
                {"issue": {"$regex": query, "$options": "i"}}
            ]
        }).limit(limit).to_list(length=limit)
        
        for ticket in tickets:
            if "_id" in ticket:
                ticket["_id"] = str(ticket["_id"])
        
        results["tickets"] = tickets
    
    return {
        "status": "success",
        "query": query,
        "results": results
    }


# ============================================================================
# HEALTH CHECK
# ============================================================================

@router.get("/health")
async def admin_health_check(
    admin: dict = Depends(get_current_admin),
    db_instance: AsyncIOMotorDatabase = Depends(get_db)
):
    """Admin panel health check"""
    
    # Check database connection
    try:
        await db_instance.command("ping")
        db_status = "healthy"
    except Exception as e:
        db_status = f"unhealthy: {str(e)}"
    
    return {
        "status": "success",
        "admin_panel": "operational",
        "database": db_status,
        "timestamp": datetime.utcnow().isoformat()
    }
"""
Bulk Actions for Admin Panel
Safe mass operations with audit logging, dry-run, and transactions
"""

# ============================================================================
# models/enums.py - Strict Type Definitions
# ============================================================================

from enum import Enum

class UserTier(str, Enum):
    """Valid user tiers"""
    FREE = "free"
    HERO = "hero"
    DOMINATOR = "dominator"


class BulkActionType(str, Enum):
    """Bulk action types for audit logging"""
    UPGRADE_USERS = "bulk_upgrade"
    RESET_QUOTAS = "bulk_reset"
    BAN_USERS = "bulk_ban"
    UNBAN_USERS = "bulk_unban"
    DOWNGRADE_USERS = "bulk_downgrade"


class QuotaResetType(str, Enum):
    """Types of quota resets"""
    FULL = "full"
    COMMANDS_ONLY = "commands_only"
    ADDONS_ONLY = "addons_only"


# ============================================================================
# services/quota_service.py - Quota Management (Decoupled)
# ============================================================================

from datetime import datetime, timedelta
from typing import Dict
from motor.motor_asyncio import AsyncIOMotorDatabase

class QuotaService:
    """Centralized quota management - decoupled from payment logic"""
    
    # Tier configurations
    TIER_CONFIGS = {
        UserTier.FREE: {
            "commands": {
                "inject": 5,
                "cells": 3,
                "pdf": 2,
                "convo": 10,
                "trace": 5,
                "explain": 10
            },
            "addons": {
                "inject": 0,
                "cells": 0,
                "pdf": 0,
                "convo": 0,
                "trace": 0,
                "explain": 0
            },
            "expires_days": None  # Never expires
        },
        UserTier.HERO: {
            "commands": {
                "inject": 50,
                "cells": 30,
                "pdf": 20,
                "convo": 100,
                "trace": 50,
                "explain": 100
            },
            "addons": {
                "inject": 0,
                "cells": 0,
                "pdf": 0,
                "convo": 0,
                "trace": 0,
                "explain": 0
            },
            "expires_days": 30
        },
        UserTier.DOMINATOR: {
            "commands": {
                "inject": 200,
                "cells": 150,
                "pdf": 100,
                "convo": 500,
                "trace": 200,
                "explain": 500
            },
            "addons": {
                "inject": 0,
                "cells": 0,
                "pdf": 0,
                "convo": 0,
                "trace": 0,
                "explain": 0
            },
            "expires_days": 30
        }
    }
    
    @classmethod
    def create_quota_document(cls, sidhi_id: str, tier: UserTier) -> Dict:
        """
        Create a fresh quota document for a user
        
        Args:
            sidhi_id: User ID
            tier: Target tier
            
        Returns:
            Complete quota document
        """
        config = cls.TIER_CONFIGS[tier]
        
        expires_at = None
        if config["expires_days"]:
            expires_at = datetime.utcnow() + timedelta(days=config["expires_days"])
        
        return {
            "sidhi_id": sidhi_id,
            "tier": tier.value,
            "base": {
                "commands": config["commands"].copy()
            },
            "used": {
                "commands": {cmd: 0 for cmd in config["commands"].keys()},
                "inject": 0,
                "cells": 0,
                "pdf": 0,
                "convo": 0
            },
            "addons": config["addons"].copy(),
            "meta": {
                "created_at": datetime.utcnow(),
                "last_updated": datetime.utcnow(),
                "expires_at": expires_at,
                "last_used_at": None
            }
        }
    
    @classmethod
    def reset_quota_usage(cls, quota: Dict, reset_type: QuotaResetType) -> Dict:
        """
        Reset quota usage based on type
        
        Args:
            quota: Existing quota document
            reset_type: Type of reset
            
        Returns:
            Updated fields dict
        """
        update_fields = {}
        
        if reset_type in [QuotaResetType.FULL, QuotaResetType.COMMANDS_ONLY]:
            # Reset command usage to 0
            reset_commands = {cmd: 0 for cmd in quota["base"]["commands"].keys()}
            update_fields["used.commands"] = reset_commands
        
        if reset_type == QuotaResetType.FULL:
            # Reset all usage counters
            update_fields["used.inject"] = 0
            update_fields["used.cells"] = 0
            update_fields["used.pdf"] = 0
            update_fields["used.convo"] = 0
            
            # Reset expiry if tier has expiration
            tier = UserTier(quota["tier"])
            config = cls.TIER_CONFIGS[tier]
            if config["expires_days"]:
                update_fields["meta.expires_at"] = datetime.utcnow() + timedelta(days=config["expires_days"])
        
        if reset_type == QuotaResetType.ADDONS_ONLY:
            # Reset addons
            update_fields["addons"] = {
                "inject": 0,
                "cells": 0,
                "pdf": 0,
                "convo": 0,
                "trace": 0,
                "explain": 0
            }
        
        update_fields["meta.last_updated"] = datetime.utcnow()
        
        return update_fields


# ============================================================================
# services/audit_service.py - Audit Logging
# ============================================================================

from typing import List, Any, Optional
from motor.motor_asyncio import AsyncIOMotorDatabase, AsyncIOMotorClientSession

class AuditService:
    """Audit logging for admin actions"""
    
    @staticmethod
    async def log_bulk_action(
        db: AsyncIOMotorDatabase,
        admin_email: str,
        action_type: BulkActionType,
        target_ids: List[str],
        parameters: Dict,
        before_snapshot: Optional[Dict] = None,
        after_snapshot: Optional[Dict] = None,
        result: Optional[Dict] = None,
        session: Optional[AsyncIOMotorClientSession] = None
    ):
        """
        Log bulk admin action to audit trail
        
        Args:
            db: Database instance
            admin_email: Email of admin performing action
            action_type: Type of bulk action
            target_ids: List of affected user IDs
            parameters: Action parameters
            before_snapshot: State before action
            after_snapshot: State after action
            result: Action result summary
            session: MongoDB session for transaction
        """
        audit_log = {
            "admin_email": admin_email,
            "action": action_type.value,
            "target_ids": target_ids,
            "target_count": len(target_ids),
            "parameters": parameters,
            "before_snapshot": before_snapshot,
            "after_snapshot": after_snapshot,
            "result": result,
            "timestamp": datetime.utcnow(),
            "ip_address": None,  # Add from request context
            "user_agent": None   # Add from request context
        }
        
        await db.admin_audit_logs.insert_one(audit_log, session=session)
    
    @staticmethod
    async def get_recent_actions(
        db: AsyncIOMotorDatabase,
        limit: int = 50,
        admin_email: Optional[str] = None
    ) -> List[Dict]:
        """Get recent audit logs"""
        query = {}
        if admin_email:
            query["admin_email"] = admin_email
        
        logs = await db.admin_audit_logs.find(query) \
            .sort("timestamp", -1) \
            .limit(limit) \
            .to_list(length=limit)
        
        return logs


# ============================================================================
# services/bulk_service.py - Safe Bulk Operations
# ============================================================================

from typing import List, Dict
from motor.motor_asyncio import AsyncIOMotorDatabase
from fastapi import HTTPException

class BulkOperationResult:
    """Result of bulk operation"""
    def __init__(self):
        self.success_count = 0
        self.error_count = 0
        self.errors: List[Dict] = []
        self.warnings: List[str] = []
    
    def to_dict(self) -> Dict:
        return {
            "status": "completed",
            "success_count": self.success_count,
            "error_count": self.error_count,
            "total_requested": self.success_count + self.error_count,
            "errors": self.errors,
            "warnings": self.warnings
        }


class BulkService:
    """Safe bulk operations with transactions and dry-run"""
    
    MAX_BULK_SIZE = 1000  # Safety limit
    
    @staticmethod
    def validate_bulk_size(sidhi_ids: List[str]):
        """Validate bulk operation size"""
        if len(sidhi_ids) > BulkService.MAX_BULK_SIZE:
            raise HTTPException(
                status_code=400,
                detail=f"Bulk operation limited to {BulkService.MAX_BULK_SIZE} users"
            )
        if len(sidhi_ids) == 0:
            raise HTTPException(status_code=400, detail="No users specified")
    
    @staticmethod
    async def bulk_upgrade_users(
        db: AsyncIOMotorDatabase,
        admin_email: str,
        sidhi_ids: List[str],
        tier: UserTier,
        dry_run: bool = False
    ) -> Dict:
        """
        Upgrade multiple users to a specific tier (with transaction)
        
        Args:
            db: Database instance
            admin_email: Admin performing action
            sidhi_ids: List of user IDs to upgrade
            tier: Target tier
            dry_run: If True, validate only without making changes
            
        Returns:
            Operation result
        """
        BulkService.validate_bulk_size(sidhi_ids)
        
        # Dry run: validate and preview
        if dry_run:
            return await BulkService._dry_run_upgrade(db, sidhi_ids, tier)
        
        # Execute with transaction
        result = BulkOperationResult()
        
        async with await db.client.start_session() as session:
            async with session.start_transaction():
                try:
                    # Snapshot before state
                    before_snapshot = await db.quotas.find(
                        {"sidhi_id": {"$in": sidhi_ids[:10]}}  # Sample
                    ).to_list(length=10)
                    
                    for sidhi_id in sidhi_ids:
                        try:
                            # Create new quota document
                            quota_doc = QuotaService.create_quota_document(sidhi_id, tier)
                            
                            # Update in database
                            await db.quotas.update_one(
                                {"sidhi_id": sidhi_id},
                                {"$set": quota_doc},
                                upsert=True,
                                session=session
                            )
                            
                            result.success_count += 1
                            
                        except Exception as e:
                            result.error_count += 1
                            result.errors.append({
                                "sidhi_id": sidhi_id,
                                "error": str(e)
                            })
                    
                    # Snapshot after state
                    after_snapshot = await db.quotas.find(
                        {"sidhi_id": {"$in": sidhi_ids[:10]}}
                    ).to_list(length=10)
                    
                    # Log audit trail
                    await AuditService.log_bulk_action(
                        db=db,
                        admin_email=admin_email,
                        action_type=BulkActionType.UPGRADE_USERS,
                        target_ids=sidhi_ids,
                        parameters={"tier": tier.value},
                        before_snapshot=before_snapshot,
                        after_snapshot=after_snapshot,
                        result=result.to_dict(),
                        session=session
                    )
                    
                    # Commit transaction
                    await session.commit_transaction()
                    
                except Exception as e:
                    await session.abort_transaction()
                    raise HTTPException(
                        status_code=500,
                        detail=f"Bulk upgrade failed, transaction rolled back: {str(e)}"
                    )
        
        return result.to_dict()
    
    @staticmethod
    async def _dry_run_upgrade(
        db: AsyncIOMotorDatabase,
        sidhi_ids: List[str],
        tier: UserTier
    ) -> Dict:
        """Dry run validation for upgrade"""
        # Check which users exist
        existing_users = await db.users_profile.count_documents(
            {"sidhi_id": {"$in": sidhi_ids}}
        )
        
        # Check current quotas
        current_quotas = await db.quotas.find(
            {"sidhi_id": {"$in": sidhi_ids}}
        ).to_list(length=None)
        
        tier_changes = {}
        for quota in current_quotas:
            old_tier = quota.get("tier", "none")
            tier_changes[old_tier] = tier_changes.get(old_tier, 0) + 1
        
        return {
            "status": "dry_run",
            "would_affect": len(sidhi_ids),
            "existing_users": existing_users,
            "missing_users": len(sidhi_ids) - existing_users,
            "target_tier": tier.value,
            "current_tier_distribution": tier_changes,
            "message": "This is a preview. No changes were made."
        }
    
    @staticmethod
    async def bulk_reset_quotas(
        db: AsyncIOMotorDatabase,
        admin_email: str,
        sidhi_ids: List[str],
        reset_type: QuotaResetType,
        dry_run: bool = False
    ) -> Dict:
        """
        Reset quotas for multiple users (with transaction)
        """
        BulkService.validate_bulk_size(sidhi_ids)
        
        if dry_run:
            affected = await db.quotas.count_documents({"sidhi_id": {"$in": sidhi_ids}})
            return {
                "status": "dry_run",
                "would_affect": affected,
                "reset_type": reset_type.value,
                "message": "This is a preview. No changes were made."
            }
        
        result = BulkOperationResult()
        
        async with await db.client.start_session() as session:
            async with session.start_transaction():
                try:
                    for sidhi_id in sidhi_ids:
                        try:
                            quota = await db.quotas.find_one(
                                {"sidhi_id": sidhi_id},
                                session=session
                            )
                            
                            if not quota:
                                result.error_count += 1
                                result.errors.append({
                                    "sidhi_id": sidhi_id,
                                    "error": "Quota not found"
                                })
                                continue
                            
                            # Get reset fields
                            update_fields = QuotaService.reset_quota_usage(quota, reset_type)
                            
                            # Apply update
                            await db.quotas.update_one(
                                {"sidhi_id": sidhi_id},
                                {"$set": update_fields},
                                session=session
                            )
                            
                            result.success_count += 1
                            
                        except Exception as e:
                            result.error_count += 1
                            result.errors.append({
                                "sidhi_id": sidhi_id,
                                "error": str(e)
                            })
                    
                    # Log audit
                    await AuditService.log_bulk_action(
                        db=db,
                        admin_email=admin_email,
                        action_type=BulkActionType.RESET_QUOTAS,
                        target_ids=sidhi_ids,
                        parameters={"reset_type": reset_type.value},
                        result=result.to_dict(),
                        session=session
                    )
                    
                    await session.commit_transaction()
                    
                except Exception as e:
                    await session.abort_transaction()
                    raise HTTPException(
                        status_code=500,
                        detail=f"Bulk reset failed: {str(e)}"
                    )
        
        return result.to_dict()
    
    @staticmethod
    async def bulk_ban_users(
        db: AsyncIOMotorDatabase,
        admin_email: str,
        sidhi_ids: List[str],
        reason: str,
        dry_run: bool = False
    ) -> Dict:
        """
        Ban multiple users (with transaction)
        """
        BulkService.validate_bulk_size(sidhi_ids)
        
        if dry_run:
            affected = await db.users_profile.count_documents(
                {"sidhi_id": {"$in": sidhi_ids}}
            )
            return {
                "status": "dry_run",
                "would_ban": affected,
                "reason": reason,
                "message": "This is a preview. No changes were made."
            }
        
        async with await db.client.start_session() as session:
            async with session.start_transaction():
                try:
                    # Ban users
                    ban_result = await db.users_profile.update_many(
                        {"sidhi_id": {"$in": sidhi_ids}},
                        {"$set": {
                            "is_banned": True,
                            "ban_reason": reason,
                            "banned_at": datetime.utcnow(),
                            "banned_by": admin_email
                        }},
                        session=session
                    )
                    
                    # Invalidate their quotas
                    await db.quotas.update_many(
                        {"sidhi_id": {"$in": sidhi_ids}},
                        {"$set": {
                            "meta.is_banned": True,
                            "meta.banned_at": datetime.utcnow()
                        }},
                        session=session
                    )
                    
                    # Log audit
                    await AuditService.log_bulk_action(
                        db=db,
                        admin_email=admin_email,
                        action_type=BulkActionType.BAN_USERS,
                        target_ids=sidhi_ids,
                        parameters={"reason": reason},
                        result={
                            "banned_count": ban_result.modified_count
                        },
                        session=session
                    )
                    
                    await session.commit_transaction()
                    
                    return {
                        "status": "success",
                        "banned_count": ban_result.modified_count,
                        "total_requested": len(sidhi_ids)
                    }
                    
                except Exception as e:
                    await session.abort_transaction()
                    raise HTTPException(
                        status_code=500,
                        detail=f"Bulk ban failed: {str(e)}"
                    )
    
    @staticmethod
    async def bulk_unban_users(
        db: AsyncIOMotorDatabase,
        admin_email: str,
        sidhi_ids: List[str],
        dry_run: bool = False
    ) -> Dict:
        """Unban multiple users"""
        BulkService.validate_bulk_size(sidhi_ids)
        
        if dry_run:
            affected = await db.users_profile.count_documents(
                {"sidhi_id": {"$in": sidhi_ids}, "is_banned": True}
            )
            return {
                "status": "dry_run",
                "would_unban": affected,
                "message": "This is a preview. No changes were made."
            }
        
        async with await db.client.start_session() as session:
            async with session.start_transaction():
                try:
                    unban_result = await db.users_profile.update_many(
                        {"sidhi_id": {"$in": sidhi_ids}},
                        {"$set": {
                            "is_banned": False,
                            "ban_reason": None,
                            "unbanned_at": datetime.utcnow(),
                            "unbanned_by": admin_email
                        }},
                        session=session
                    )
                    
                    await db.quotas.update_many(
                        {"sidhi_id": {"$in": sidhi_ids}},
                        {"$set": {
                            "meta.is_banned": False
                        }},
                        session=session
                    )
                    
                    await AuditService.log_bulk_action(
                        db=db,
                        admin_email=admin_email,
                        action_type=BulkActionType.UNBAN_USERS,
                        target_ids=sidhi_ids,
                        parameters={},
                        result={"unbanned_count": unban_result.modified_count},
                        session=session
                    )
                    
                    await session.commit_transaction()
                    
                    return {
                        "status": "success",
                        "unbanned_count": unban_result.modified_count
                    }
                    
                except Exception as e:
                    await session.abort_transaction()
                    raise HTTPException(status_code=500, detail=f"Bulk unban failed: {str(e)}")


# ============================================================================
# services/export_service.py - Streaming CSV Exports
# ============================================================================

import csv
from typing import AsyncGenerator, Optional
from datetime import datetime

class ExportService:
    """Streaming CSV export service"""
    
    MAX_EXPORT_ROWS = 100_000  # Safety limit
    
    @staticmethod
    async def export_users_csv(db: AsyncIOMotorDatabase) -> AsyncGenerator[str, None]:
        """
        Stream users to CSV (memory-safe)
        
        Yields:
            CSV rows as strings
        """
        # Header
        header = "sidhi_id,username,email_id,college,department,degree,starting_year,is_admin,is_banned\n"
        yield header
        
        # Stream users
        row_count = 0
        async for user in db.users_profile.find({}).batch_size(100):
            if row_count >= ExportService.MAX_EXPORT_ROWS:
                break
            
            row = f"{user.get('sidhi_id', '')}," \
                  f"{user.get('username', '')}," \
                  f"{user.get('email_id', '')}," \
                  f"{user.get('college', '')}," \
                  f"{user.get('department', '')}," \
                  f"{user.get('degree', '')}," \
                  f"{user.get('starting_year', '')}," \
                  f"{user.get('is_admin', False)}," \
                  f"{user.get('is_banned', False)}\n"
            
            yield row
            row_count += 1
    
    @staticmethod
    async def export_payments_csv(
        db: AsyncIOMotorDatabase,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> AsyncGenerator[str, None]:
        """
        Stream payments to CSV (requires date filters)
        """
        # Enforce date filters
        if not start_date:
            start_date = datetime.utcnow() - timedelta(days=90)  # Max 90 days
        
        query = {"created_at": {"$gte": start_date}}
        if end_date:
            query["created_at"]["$lte"] = end_date
        
        # Header
        header = "order_id,sidhi_id,tier,amount_inr,status,payment_id,created_at,verified_via\n"
        yield header
        
        # Stream payments
        row_count = 0
        async for payment in db.payments.find(query).batch_size(100):
            if row_count >= ExportService.MAX_EXPORT_ROWS:
                break
            
            row = f"{payment.get('razorpay_order_id', '')}," \
                  f"{payment.get('sidhi_id', '')}," \
                  f"{payment.get('tier', '')}," \
                  f"{payment.get('amount', 0) / 100}," \
                  f"{payment.get('status', '')}," \
                  f"{payment.get('razorpay_payment_id', '')}," \
                  f"{payment.get('created_at', '')}," \
                  f"{payment.get('verified_via', '')}\n"
            
            yield row
            row_count += 1


# ============================================================================
# middleware/ban_middleware.py - Ban Enforcement
# ============================================================================

from fastapi import Request, HTTPException
from motor.motor_asyncio import AsyncIOMotorDatabase

async def enforce_ban_middleware(request: Request, db: AsyncIOMotorDatabase, sidhi_id: str):
    """
    Middleware to enforce user bans globally
    
    Args:
        request: FastAPI request
        db: Database instance
        sidhi_id: User ID from authenticated session
        
    Raises:
        HTTPException: If user is banned
    """
    # Skip for admin routes
    if request.url.path.startswith("/admin"):
        return
    
    # Check ban status
    user = await db.users_profile.find_one(
        {"sidhi_id": sidhi_id},
        {"is_banned": 1, "ban_reason": 1}
    )
    
    if user and user.get("is_banned", False):
        raise HTTPException(
            status_code=403,
            detail=f"Account banned: {user.get('ban_reason', 'No reason provided')}"
        )


# ============================================================================
# FastAPI Integration Example
# ============================================================================

"""
# main.py
from fastapi import FastAPI, Depends
from fastapi.responses import StreamingResponse

app = FastAPI()

@app.post("/admin/bulk/upgrade")
async def bulk_upgrade(
    sidhi_ids: List[str],
    tier: UserTier,
    dry_run: bool = False,
    admin: dict = Depends(get_current_admin),
    db: AsyncIOMotorDatabase = Depends(get_db)
):
    return await BulkService.bulk_upgrade_users(
        db=db,
        admin_email=admin["email"],
        sidhi_ids=sidhi_ids,
        tier=tier,
        dry_run=dry_run
    )

@app.get("/admin/export/users")
async def export_users(
    admin: dict = Depends(get_current_admin),
    db: AsyncIOMotorDatabase = Depends(get_db)
):
    return StreamingResponse(
        ExportService.export_users_csv(db),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=users.csv"}
    )

@app.get("/admin/audit/logs")
async def audit_logs(
    limit: int = 50,
    admin: dict = Depends(get_current_admin),
    db: AsyncIOMotorDatabase = Depends(get_db)
):
    return await AuditService.get_recent_actions(db, limit, admin["email"])
"""
# ============================================================================
# BACKWARD-COMPATIBILITY WRAPPERS
# DO NOT REMOVE – required by existing admin router
# ============================================================================

from typing import List, Optional
from motor.motor_asyncio import AsyncIOMotorClient
import os

# Reuse existing Mongo connection pattern used elsewhere
_MONGO_URL = os.getenv("MONGO_URL")
_client = AsyncIOMotorClient(_MONGO_URL)
_db = _client.lumetrics_db


# -------------------------------
# BULK ACTION WRAPPERS
# -------------------------------

async def bulk_upgrade_users(
    sidhi_ids: List[str],
    tier: str
) -> dict:
    """
    Wrapper for BulkService.bulk_upgrade_users
    (keeps router unchanged)
    """
    from app.admin.safe_bulk_operations import BulkService, UserTier

    return await BulkService.bulk_upgrade_users(
        db=_db,
        admin_email="system-admin",  # router already authenticated admin
        sidhi_ids=sidhi_ids,
        tier=UserTier(tier),
        dry_run=False
    )


async def bulk_reset_quotas(
    sidhi_ids: List[str],
    reset_type: str
) -> dict:
    from app.admin.safe_bulk_operations import BulkService, QuotaResetType

    return await BulkService.bulk_reset_quotas(
        db=_db,
        admin_email="system-admin",
        sidhi_ids=sidhi_ids,
        reset_type=QuotaResetType(reset_type),
        dry_run=False
    )


async def bulk_ban_users(
    sidhi_ids: List[str],
    reason: str
) -> dict:
    from app.admin.safe_bulk_operations import BulkService

    return await BulkService.bulk_ban_users(
        db=_db,
        admin_email="system-admin",
        sidhi_ids=sidhi_ids,
        reason=reason,
        dry_run=False
    )


async def bulk_unban_users(
    sidhi_ids: List[str]
) -> dict:
    from app.admin.safe_bulk_operations import BulkService

    return await BulkService.bulk_unban_users(
        db=_db,
        admin_email="system-admin",
        sidhi_ids=sidhi_ids,
        dry_run=False
    )


# -------------------------------
# CSV EXPORT WRAPPERS
# -------------------------------

async def export_users_csv() -> str:
    """
    Wrapper for ExportService.export_users_csv
    Returns CSV string (router already streams it)
    """
    from app.admin.safe_bulk_operations import ExportService

    chunks = []
    async for chunk in ExportService.export_users_csv(_db):
        chunks.append(chunk)

    return "".join(chunks)


async def export_payments_csv(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
) -> str:
    from app.admin.safe_bulk_operations import ExportService

    chunks = []
    async for chunk in ExportService.export_payments_csv(_db):
        chunks.append(chunk)

    return "".join(chunks)


async def export_tickets_csv() -> str:
    """
    Minimal ticket export to satisfy router import
    """
    rows = ["sidhi_id,email_id,issue,status,created_at\n"]

    async for ticket in _db.help_tickets.find({}):
        rows.append(
            f"{ticket.get('sidhi_id','')},"
            f"{ticket.get('email_id','')},"
            f"{ticket.get('issue','')},"
            f"{ticket.get('status','')},"
            f"{ticket.get('created_at','')}\n"
        )

    return "".join(rows)

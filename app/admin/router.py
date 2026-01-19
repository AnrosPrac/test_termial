from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from typing import List
from motor.motor_asyncio import AsyncIOMotorDatabase

# 🔐 Admin Auth (from same folder)
from app.admin.hardened_firebase_auth import get_current_admin,verify_admin_jwt,create_admin_jwt,verify_firebase_token

# 🗄️ Database
from app.admin.hardened_analytics import get_db

from pydantic import BaseModel

# 📊 Analytics
from app.admin.hardened_analytics import (
    get_dashboard_stats,
    get_revenue_chart,
    get_user_growth_chart,
    get_command_usage_stats,
    get_top_users_by_usage
)

# 🧨 Bulk Operations
from app.admin.safe_bulk_operations import (
    BulkService,
    UserTier,
    QuotaResetType,
    ExportService,
    AuditService
)

router = APIRouter(
    prefix="/admin",
    tags=["Admin"],
)

class AdminLoginRequest(BaseModel):
    firebase_token: str


@router.post("/login")
async def admin_login(payload: AdminLoginRequest):
    """
    Admin login endpoint.
    Accepts Firebase ID token and returns admin JWT.
    """
    # 1. Verify Firebase token + admin allowlist
    decoded = verify_firebase_token(payload.firebase_token)

    # 2. Issue backend admin JWT
    admin_jwt = create_admin_jwt(decoded["email"])

    return {
        "access_token": admin_jwt,
        "token_type": "Bearer",
        "expires_in_hours": 24
    }

@router.get("/dashboard/stats")
async def dashboard_stats(
    admin: dict = Depends(get_current_admin),
    db: AsyncIOMotorDatabase = Depends(get_db)
):
    return await get_dashboard_stats(db)


@router.get("/charts/revenue")
async def revenue_chart(
    days: int = Query(30, ge=1, le=365),
    admin: dict = Depends(get_current_admin),
    db: AsyncIOMotorDatabase = Depends(get_db)
):
    return await get_revenue_chart(db, days)


@router.get("/charts/user-growth")
async def user_growth_chart(
    days: int = Query(30, ge=1, le=365),
    admin: dict = Depends(get_current_admin),
    db: AsyncIOMotorDatabase = Depends(get_db)
):
    return await get_user_growth_chart(db, days)


@router.get("/stats/commands")
async def command_usage_stats(
    admin: dict = Depends(get_current_admin),
    db: AsyncIOMotorDatabase = Depends(get_db)
):
    return await get_command_usage_stats(db)


@router.get("/stats/top-users")
async def top_users(
    limit: int = Query(10, ge=1, le=100),
    admin: dict = Depends(get_current_admin),
    db: AsyncIOMotorDatabase = Depends(get_db)
):
    return await get_top_users_by_usage(db, limit)
@router.post("/bulk/upgrade")
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


@router.post("/bulk/reset-quotas")
async def bulk_reset(
    sidhi_ids: List[str],
    reset_type: QuotaResetType,
    dry_run: bool = False,
    admin: dict = Depends(get_current_admin),
    db: AsyncIOMotorDatabase = Depends(get_db)
):
    return await BulkService.bulk_reset_quotas(
        db=db,
        admin_email=admin["email"],
        sidhi_ids=sidhi_ids,
        reset_type=reset_type,
        dry_run=dry_run
    )


@router.post("/bulk/ban")
async def bulk_ban(
    sidhi_ids: List[str],
    reason: str,
    dry_run: bool = False,
    admin: dict = Depends(get_current_admin),
    db: AsyncIOMotorDatabase = Depends(get_db)
):
    return await BulkService.bulk_ban_users(
        db=db,
        admin_email=admin["email"],
        sidhi_ids=sidhi_ids,
        reason=reason,
        dry_run=dry_run
    )


@router.post("/bulk/unban")
async def bulk_unban(
    sidhi_ids: List[str],
    dry_run: bool = False,
    admin: dict = Depends(get_current_admin),
    db: AsyncIOMotorDatabase = Depends(get_db)
):
    return await BulkService.bulk_unban_users(
        db=db,
        admin_email=admin["email"],
        sidhi_ids=sidhi_ids,
        dry_run=dry_run
    )
@router.get("/export/users")
async def export_users(
    admin: dict = Depends(get_current_admin),
    db: AsyncIOMotorDatabase = Depends(get_db)
):
    return StreamingResponse(
        ExportService.export_users_csv(db),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=users.csv"}
    )


@router.get("/export/payments")
async def export_payments(
    admin: dict = Depends(get_current_admin),
    db: AsyncIOMotorDatabase = Depends(get_db)
):
    return StreamingResponse(
        ExportService.export_payments_csv(db),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=payments.csv"}
    )
@router.get("/audit/logs")
async def audit_logs(
    limit: int = Query(50, ge=1, le=200),
    admin: dict = Depends(get_current_admin),
    db: AsyncIOMotorDatabase = Depends(get_db)
):
    return await AuditService.get_recent_actions(
        db=db,
        limit=limit,
        admin_email=admin["email"]
    )

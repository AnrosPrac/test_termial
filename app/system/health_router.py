from fastapi import APIRouter, Depends, HTTPException
from motor.motor_asyncio import AsyncIOMotorDatabase
from datetime import datetime
import time
import os

from app.courses.dependencies import get_db

router = APIRouter(tags=["Health"])

START_TIME = time.time()

INTERNAL_TOKEN = os.getenv("INTERNAL_HEALTH_TOKEN", "secret-token")


# -----------------------
# 1️⃣ Liveness (crash check)
# -----------------------
@router.get("/live")
async def live():
    return {"alive": True}


# -----------------------
# 2️⃣ Public health (SAFE)
# -----------------------
@router.get("/health")
async def health(db: AsyncIOMotorDatabase = Depends(get_db)):
    try:
        await db.command("ping")
        return {"status": "healthy"}
    except:
        raise HTTPException(503, "unhealthy")


# -----------------------
# 3️⃣ Internal health (PRIVATE)
# -----------------------
@router.get("/health/internal")
async def health_internal(
    token: str,
    db: AsyncIOMotorDatabase = Depends(get_db)
):
    if token != INTERNAL_TOKEN:
        raise HTTPException(403)

    uptime = int(time.time() - START_TIME)

    db_ok = True
    try:
        await db.command("ping")
    except:
        db_ok = False

    return {
        "status": "healthy" if db_ok else "unhealthy",
        "uptime_seconds": uptime,
        "timestamp": datetime.utcnow()
    }

import os
from motor.motor_asyncio import AsyncIOMotorClient
from fastapi import HTTPException

MONGO_URL = os.getenv("MONGO_URL", "mongodb://localhost:27017")
client = AsyncIOMotorClient(MONGO_URL)
db = client.lumetrics_db 
from fastapi import HTTPException
from datetime import datetime

from fastapi import HTTPException
from datetime import datetime

async def check_and_use_quota(sidhi_id: str, command: str) -> bool:
    user = await db.users_quotas.find_one({"sidhi_id": sidhi_id})

    if not user:
        raise HTTPException(status_code=404, detail="User quota profile not found")

    base = user.get("base", {})
    used = user.get("used", {})
    addons = user.get("addons", {})

    # ---------- detect command vs feature ----------
    if command in base.get("commands", {}):
        base_limit = base["commands"].get(command, 0)
        used_count = used["commands"].get(command, 0)
        addon_limit = addons.get(command, 0)
        used_path = f"used.commands.{command}"
    elif command in base:
        base_limit = base.get(command, 0)
        used_count = used.get(command, 0)
        addon_limit = addons.get(command, 0)
        used_path = f"used.{command}"
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid command/feature: {command}"
        )

    remaining = (base_limit + addon_limit) - used_count

    if remaining <= 0:
        raise HTTPException(
            status_code=429,
            detail=f"Quota exhausted for '{command}'"
        )

    # ---------- atomic update ----------
    result = await db.users_quotas.update_one(
        {"sidhi_id": sidhi_id},
        {
            "$inc": {used_path: 1},
            "$set": {"meta.last_updated": datetime.utcnow()}
        }
    )

    if result.modified_count == 0:
        raise HTTPException(
            status_code=500,
            detail="Quota update failed"
        )

    return True




from datetime import datetime

async def log_activity(sidhi_id: str, command: str, success: bool):
    today = datetime.utcnow().strftime("%Y-%m-%d")
    log_entry = {
        "command": command,
        "timestamp": datetime.utcnow(),
        "success": success
    }
    
    await db.history.update_one(
        {"sidhi_id": sidhi_id},
        {"$push": {f"logs.{today}": log_entry}},
        upsert=True
    )

async def get_user_quotas(sidhi_id: str):
    return await db.users_quotas.find_one({"sidhi_id": sidhi_id}, {"_id": 0})

async def get_user_history(sidhi_id: str):
    return await db.history.find_one({"sidhi_id": sidhi_id}, {"_id": 0})

from datetime import datetime

async def log_cloud_push(sidhi_id: str):
    # Perfect formatting: "Jan 15, 2026 | 10:30 PM"
    now = datetime.utcnow()
    timestamp_str = now.strftime("%b %d, %Y | %I:%M %p")
    
    await db.cloud_history.update_one(
        {"sidhi_id": sidhi_id},
        {"$push": {
            "pushes": {
                "time": timestamp_str,
                "timestamp": now # Storing raw date helps if you want to sort later
            }
        }},
        upsert=True
    )

async def get_cloud_history(sidhi_id: str):
    return await db.cloud_history.find_one({"sidhi_id": sidhi_id}, {"_id": 0})
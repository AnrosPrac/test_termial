import os
from motor.motor_asyncio import AsyncIOMotorClient
from fastapi import HTTPException

MONGO_URL = os.getenv("MONGO_URL", "mongodb://localhost:27017")
client = AsyncIOMotorClient(MONGO_URL)
db = client.lumetrics_db 
async def check_and_use_quota(sidhi_id: str, feature: str):
    user = await db.users_quotas.find_one({"sidhi_id": sidhi_id})

    if not user:
        raise HTTPException(status_code=404, detail="User quota profile not found")

    remaining = user.get("quotas", {}).get(feature)

    if remaining is None:
        raise HTTPException(status_code=400, detail=f"Invalid feature: {feature}")

    if remaining <= 0:
        raise HTTPException(
            status_code=429,
            detail=f"Quota exhausted for {feature}"
        )

    await db.users_quotas.update_one(
        {"sidhi_id": sidhi_id},
        {"$inc": {f"quotas.{feature}": -1}}
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
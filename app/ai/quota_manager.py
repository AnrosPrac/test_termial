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

    remaining = user.get("quotas", {}).get(feature, 0)

    if remaining <= 0:
        raise HTTPException(
            status_code=429, 
            detail=f"Quota exhausted for {feature}. Remaining: 0"
        )

    await db.users_quotas.update_one(
        {"sidhi_id": sidhi_id},
        {"$inc": {f"quotas.{feature}": -1}}
    )
    
    return True
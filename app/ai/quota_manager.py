import os
from motor.motor_asyncio import AsyncIOMotorClient
from fastapi import HTTPException

class MongoManager:
    def __init__(self):
        self.client = None
        self.db = None

    async def connect(self):
        mongo_url = os.getenv("MONGO_URL", "mongodb://localhost:27017")
        db_name = os.getenv("MONGO_DB_NAME", "lumetrics_db")
        self.client = AsyncIOMotorClient(mongo_url)
        self.db = self.client[db_name]

    async def disconnect(self):
        if self.client:
            self.client.close()

    async def check_and_use_quota(self, sidhi_id: str, feature: str):
        user = await self.db.users_quotas.find_one({"sidhi_id": sidhi_id})
        
        if not user:
            raise HTTPException(status_code=404, detail="User profile not found")

        quotas = user.get("quotas", {})
        remaining = quotas.get(feature, 0)

        if remaining <= 0:
            raise HTTPException(
                status_code=429, 
                detail=f"Quota exhausted for {feature}. Remaining: 0"
            )

        await self.db.users_quotas.update_one(
            {"sidhi_id": sidhi_id},
            {"$inc": {f"quotas.{feature}": -1}}
        )
        
        return True

manager = MongoManager()
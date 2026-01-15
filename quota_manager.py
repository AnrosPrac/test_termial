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

    async def check_and_update_quota(self, sidhi_id: str, feature_path: str):
        user = await self.db.users_quotas.find_one({"sidhi_id": sidhi_id})
        
        if not user:
            raise HTTPException(status_code=404, detail="User profile not found")

        path_parts = feature_path.split(".")
        
        limit = user["base"]
        used = user["used"]
        addons = user["addons"]

        for part in path_parts:
            limit = limit.get(part, 0) if isinstance(limit, dict) else limit
            used = used.get(part, 0) if isinstance(used, dict) else used
            addons = addons.get(part, 0) if isinstance(addons, dict) else addons

        total_allowed = limit + addons

        if used >= total_allowed:
            raise HTTPException(
                status_code=429, 
                detail=f"Quota exhausted for {feature_path}. Limit: {total_allowed}"
            )

        await self.db.users_quotas.update_one(
            {"sidhi_id": sidhi_id},
            {"$inc": {f"used.{feature_path}": 1}}
        )
        
        return True

manager = MongoManager()
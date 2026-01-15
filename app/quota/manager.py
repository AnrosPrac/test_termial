# from fastapi import HTTPException
# from datetime import datetime
# import os
# from motor.motor_asyncio import AsyncIOMotorClient
# from cycle import resolve_cycle



# MONGO_URL = os.getenv("MONGO_URL")

# client = AsyncIOMotorClient(MONGO_URL)
# db = client.lumetrics_db    
# async def consume_quota(sidhi_id: str, command: str):
#     """
#     Universal quota gate.
#     Input: sidhi_id, command
#     """

#     cycle = resolve_cycle()  # internal logic

#     doc = await db.user_quota.find_one({
#         "sidhi_id": sidhi_id,
#         "cycle.id": cycle["id"]
#     })

#     if not doc or doc["meta"]["status"] != "active":
#         raise HTTPException(403, "Quota profile not found or inactive")

#     limit = doc["limits"].get(command)
#     used = doc["used"].get(command, 0)
#     addon = doc["addons"].get(command, 0)

#     # Unlimited
#     if limit == -1:
#         return

#     # Base quota
#     if used < limit:
#         await db.user_quota.update_one(
#             {"_id": doc["_id"]},
#             {"$inc": {f"used.{command}": 1}}
#         )
#         return

#     # Add-on quota
#     if addon > 0:
#         await db.user_quota.update_one(
#             {"_id": doc["_id"]},
#             {"$inc": {f"addons.{command}": -1}}
#         )
#         return

#     raise HTTPException(
#         status_code=429,
#         detail=f"Quota exhausted for command '{command}'"
#     )

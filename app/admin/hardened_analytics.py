"""
Analytics & Dashboard Stats for Admin Panel
Scalable, cached, timezone-aware metrics
"""

# ============================================================================
# db/session.py - Database Session Management
# ============================================================================

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from typing import Optional
import os

class DatabaseManager:
    """Manages MongoDB connection lifecycle"""
    
    def __init__(self):
        self.client: Optional[AsyncIOMotorClient] = None
        self.db: Optional[AsyncIOMotorDatabase] = None
    
    def connect(self):
        """Initialize MongoDB connection"""
        mongo_url = os.getenv("MONGO_URL")
        if not mongo_url:
            raise RuntimeError("❌ FATAL: MONGO_URL environment variable required")
        
        self.client = AsyncIOMotorClient(mongo_url)
        self.db = self.client.lumetrics_db
        print("✅ MongoDB connected")
    
    def disconnect(self):
        """Close MongoDB connection"""
        if self.client:
            self.client.close()
            print("✅ MongoDB disconnected")
    
    def get_database(self) -> AsyncIOMotorDatabase:
        """Get database instance for dependency injection"""
        if self.db is None:
            raise RuntimeError("Database not initialized. Call connect() first.")
        return self.db


# Global database manager
db_manager = DatabaseManager()


def get_db() -> AsyncIOMotorDatabase:
    """FastAPI dependency for database access"""
    return db_manager.get_database()


# ============================================================================
# config/pricing.py - Centralized Pricing Configuration
# ============================================================================

from typing import Dict
from enum import Enum

class GeminiModel(str, Enum):
    """Gemini model identifiers"""
    FLASH = "gemini-2.0-flash-exp"
    PRO = "gemini-1.5-pro"
    # Add other models as needed


class PricingConfig:
    """Centralized pricing configuration"""
    
    # Gemini API pricing (USD per 1M tokens)
    GEMINI_PRICING_USD = {
        GeminiModel.FLASH: {
            "input": 0.10,   # $0.10 per 1M input tokens
            "output": 0.40,  # $0.40 per 1M output tokens
        },
        GeminiModel.PRO: {
            "input": 1.25,
            "output": 5.00,
        }
    }
    
    # Currency conversion rates
    USD_TO_INR = 90.0  # Update regularly or fetch from API
    
    # Tier names
    VALID_TIERS = {"free", "starter", "pro", "enterprise"}
    DEFAULT_TIER = "unknown"
    
    @classmethod
    def calculate_gemini_cost_inr(
        cls,
        input_tokens: int,
        output_tokens: int,
        model: GeminiModel = GeminiModel.FLASH
    ) -> float:
        """
        Calculate Gemini API cost in INR
        
        Args:
            input_tokens: Number of input tokens
            output_tokens: Number of output tokens
            model: Gemini model used
            
        Returns:
            Cost in INR
        """
        pricing = cls.GEMINI_PRICING_USD.get(model, cls.GEMINI_PRICING_USD[GeminiModel.FLASH])
        
        input_cost_usd = (input_tokens / 1_000_000) * pricing["input"]
        output_cost_usd = (output_tokens / 1_000_000) * pricing["output"]
        
        total_usd = input_cost_usd + output_cost_usd
        total_inr = total_usd * cls.USD_TO_INR
        
        return total_inr


# ============================================================================
# utils/cache.py - Simple In-Memory Cache
# ============================================================================

from datetime import datetime, timedelta
from typing import Any, Optional
import asyncio

class CacheManager:
    """Simple in-memory cache with TTL (use Redis in production)"""
    
    def __init__(self):
        self._cache: Dict[str, tuple[Any, datetime]] = {}
        self._lock = asyncio.Lock()
    
    async def get(self, key: str) -> Optional[Any]:
        """Get cached value if not expired"""
        async with self._lock:
            if key in self._cache:
                value, expiry = self._cache[key]
                if datetime.utcnow() < expiry:
                    return value
                else:
                    del self._cache[key]
        return None
    
    async def set(self, key: str, value: Any, ttl_seconds: int):
        """Set cached value with TTL"""
        async with self._lock:
            expiry = datetime.utcnow() + timedelta(seconds=ttl_seconds)
            self._cache[key] = (value, expiry)
    
    async def delete(self, key: str):
        """Delete cached value"""
        async with self._lock:
            self._cache.pop(key, None)
    
    async def clear(self):
        """Clear all cache"""
        async with self._lock:
            self._cache.clear()


# Global cache instance
cache = CacheManager()


# ============================================================================
# services/analytics.py - Analytics Service with Caching
# ============================================================================

from datetime import datetime, timedelta, timezone
from motor.motor_asyncio import AsyncIOMotorDatabase
from typing import Dict, List

# Cache TTLs
DASHBOARD_STATS_TTL = 300  # 5 minutes
CHART_DATA_TTL = 180       # 3 minutes
COMMAND_STATS_TTL = 600    # 10 minutes

# Timezone
IST = timezone(timedelta(hours=5, minutes=30))


async def get_dashboard_stats(db: AsyncIOMotorDatabase) -> Dict:
    """
    Get overview stats for admin dashboard (cached)
    """
    cache_key = "dashboard:stats"
    cached = await cache.get(cache_key)
    if cached:
        return cached
    
    # Total users
    total_users = await db.users_profile.count_documents({})
    
    # Active users (used service in last 7 days)
    seven_days_ago = datetime.utcnow() - timedelta(days=7)
    active_users = await db.quotas.count_documents({
        "last_used_at": {"$gte": seven_days_ago}
    })
    
    # Tier distribution (normalized)
    tier_counts = await db.quotas.aggregate([
        {
            "$group": {
                "_id": {
                    "$ifNull": [
                        {
                            "$cond": [
                                {"$in": ["$tier", list(PricingConfig.VALID_TIERS)]},
                                "$tier",
                                PricingConfig.DEFAULT_TIER
                            ]
                        },
                        PricingConfig.DEFAULT_TIER
                    ]
                },
                "count": {"$sum": 1}
            }
        }
    ]).to_list(length=None)
    
    tier_distribution = {item["_id"]: item["count"] for item in tier_counts}
    
    # Total payments
    total_payments = await db.payments.count_documents({"status": "captured"})
    
    # Total revenue (in rupees)
    revenue_pipeline = await db.payments.aggregate([
        {"$match": {"status": "captured"}},
        {
            "$group": {
                "_id": None,
                "total": {"$sum": {"$ifNull": ["$amount", 0]}}
            }
        }
    ]).to_list(length=1)
    
    total_revenue = revenue_pipeline[0]["total"] / 100 if revenue_pipeline else 0
    
    # Pending tickets
    pending_tickets = await db.help_tickets.count_documents({"status": "pending"})
    
    # Today's revenue (IST timezone)
    now_ist = datetime.now(IST)
    today_start_ist = now_ist.replace(hour=0, minute=0, second=0, microsecond=0)
    today_start_utc = today_start_ist.astimezone(timezone.utc).replace(tzinfo=None)
    
    today_revenue_pipeline = await db.payments.aggregate([
        {
            "$match": {
                "status": "captured",
                "created_at": {"$gte": today_start_utc}
            }
        },
        {
            "$group": {
                "_id": None,
                "total": {"$sum": {"$ifNull": ["$amount", 0]}}
            }
        }
    ]).to_list(length=1)
    
    today_revenue = today_revenue_pipeline[0]["total"] / 100 if today_revenue_pipeline else 0
    
    # Gemini API costs (today)
    gemini_stats = await db.gemini_key_stats.aggregate([
        {
            "$group": {
                "_id": None,
                "total_requests": {"$sum": {"$ifNull": ["$requests_today", 0]}},
                "total_input_tokens": {"$sum": {"$ifNull": ["$tokens_today.input_tokens", 0]}},
                "total_output_tokens": {"$sum": {"$ifNull": ["$tokens_today.output_tokens", 0]}}
            }
        }
    ]).to_list(length=1)
    
    if gemini_stats:
        stats = gemini_stats[0]
        gemini_cost_today = PricingConfig.calculate_gemini_cost_inr(
            input_tokens=stats["total_input_tokens"],
            output_tokens=stats["total_output_tokens"],
            model=GeminiModel.FLASH
        )
    else:
        gemini_cost_today = 0
        stats = {"total_requests": 0, "total_input_tokens": 0, "total_output_tokens": 0}
    
    result = {
        "overview": {
            "total_users": total_users,
            "active_users": active_users,
            "total_payments": total_payments,
            "total_revenue_inr": round(total_revenue, 2),
            "today_revenue_inr": round(today_revenue, 2),
            "pending_tickets": pending_tickets
        },
        "tier_distribution": tier_distribution,
        "gemini_api": {
            "requests_today": stats["total_requests"],
            "input_tokens_today": stats["total_input_tokens"],
            "output_tokens_today": stats["total_output_tokens"],
            "cost_today_inr": round(gemini_cost_today, 2)
        }
    }
    
    await cache.set(cache_key, result, DASHBOARD_STATS_TTL)
    return result


async def get_revenue_chart(db: AsyncIOMotorDatabase, days: int = 30) -> List[Dict]:
    """
    Get daily revenue for last N days (cached, IST timezone)
    """
    cache_key = f"chart:revenue:{days}"
    cached = await cache.get(cache_key)
    if cached:
        return cached
    
    # Calculate start date in IST
    now_ist = datetime.now(IST)
    start_date_ist = (now_ist - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)
    start_date_utc = start_date_ist.astimezone(timezone.utc).replace(tzinfo=None)
    
    pipeline = await db.payments.aggregate([
        {
            "$match": {
                "status": "captured",
                "created_at": {"$gte": start_date_utc}
            }
        },
        {
            "$addFields": {
                # Convert UTC to IST for grouping
                "ist_date": {
                    "$dateAdd": {
                        "startDate": "$created_at",
                        "unit": "minute",
                        "amount": 330  # +5:30 hours
                    }
                }
            }
        },
        {
            "$group": {
                "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$ist_date"}},
                "revenue": {"$sum": {"$ifNull": ["$amount", 0]}},
                "count": {"$sum": 1}
            }
        },
        {"$sort": {"_id": 1}}
    ]).to_list(length=None)
    
    result = [
        {
            "date": item["_id"],
            "revenue_inr": round(item["revenue"] / 100, 2),
            "payment_count": item["count"]
        }
        for item in pipeline
    ]
    
    await cache.set(cache_key, result, CHART_DATA_TTL)
    return result


async def get_user_growth_chart(db: AsyncIOMotorDatabase, days: int = 30) -> List[Dict]:
    """
    Get daily user registrations for last N days (cached, fixed date filter)
    """
    cache_key = f"chart:user_growth:{days}"
    cached = await cache.get(cache_key)
    if cached:
        return cached
    
    # Calculate start date
    now_ist = datetime.now(IST)
    start_date_ist = (now_ist - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)
    start_date_utc = start_date_ist.astimezone(timezone.utc).replace(tzinfo=None)
    
    pipeline = await db.users_profile.aggregate([
        {
            "$match": {
                "created_at": {
                    "$exists": True,
                    "$gte": start_date_utc  # ✅ FIXED: Apply date filter
                }
            }
        },
        {
            "$addFields": {
                "ist_date": {
                    "$dateAdd": {
                        "startDate": "$created_at",
                        "unit": "minute",
                        "amount": 330
                    }
                }
            }
        },
        {
            "$group": {
                "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$ist_date"}},
                "count": {"$sum": 1}
            }
        },
        {"$sort": {"_id": 1}}
    ]).to_list(length=None)
    
    result = [
        {
            "date": item["_id"],
            "new_users": item["count"]
        }
        for item in pipeline
    ]
    
    await cache.set(cache_key, result, CHART_DATA_TTL)
    return result


async def get_command_usage_stats(db: AsyncIOMotorDatabase) -> List[Dict]:
    """
    Get most used commands across all users (cached)
    """
    cache_key = "stats:command_usage"
    cached = await cache.get(cache_key)
    if cached:
        return cached
    
    pipeline = await db.quotas.aggregate([
        {
            "$project": {
                "commands": {
                    "$ifNull": [
                        {"$objectToArray": "$used.commands"},
                        []
                    ]
                }
            }
        },
        {"$unwind": {"path": "$commands", "preserveNullAndEmptyArrays": False}},
        {
            "$group": {
                "_id": "$commands.k",
                "total_usage": {"$sum": {"$ifNull": ["$commands.v", 0]}}
            }
        },
        {"$sort": {"total_usage": -1}},
        {"$limit": 20}
    ]).to_list(length=None)
    
    result = [
        {
            "command": item["_id"],
            "usage_count": item["total_usage"]
        }
        for item in pipeline
    ]
    
    await cache.set(cache_key, result, COMMAND_STATS_TTL)
    return result


async def get_top_users_by_usage(db: AsyncIOMotorDatabase, limit: int = 10) -> List[Dict]:
    """
    Get top users by total command usage (cached)
    """
    cache_key = f"stats:top_users:{limit}"
    cached = await cache.get(cache_key)
    if cached:
        return cached
    
    pipeline = await db.quotas.aggregate([
        {
            "$project": {
                "sidhi_id": 1,
                "tier": {
                    "$ifNull": [
                        {
                            "$cond": [
                                {"$in": ["$tier", list(PricingConfig.VALID_TIERS)]},
                                "$tier",
                                PricingConfig.DEFAULT_TIER
                            ]
                        },
                        PricingConfig.DEFAULT_TIER
                    ]
                },
                "total_usage": {
                    "$sum": {
                        "$map": {
                            "input": {
                                "$ifNull": [
                                    {"$objectToArray": "$used.commands"},
                                    []
                                ]
                            },
                            "as": "cmd",
                            "in": {"$ifNull": ["$$cmd.v", 0]}
                        }
                    }
                }
            }
        },
        {"$sort": {"total_usage": -1}},
        {"$limit": limit}
    ]).to_list(length=limit)
    
    # Enrich with user profile data
    result = []
    for item in pipeline:
        profile = await db.users_profile.find_one(
            {"sidhi_id": item["sidhi_id"]},
            {"username": 1, "_id": 0}
        )
        result.append({
            "sidhi_id": item["sidhi_id"],
            "username": profile.get("username") if profile else "Unknown",
            "tier": item["tier"],
            "total_usage": item["total_usage"]
        })
    
    await cache.set(cache_key, result, COMMAND_STATS_TTL)
    return result


# ============================================================================
# FastAPI Integration Example
# ============================================================================

"""
# main.py
from fastapi import FastAPI, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase

app = FastAPI()

@app.on_event("startup")
async def startup():
    db_manager.connect()

@app.on_event("shutdown")
async def shutdown():
    db_manager.disconnect()

@app.get("/admin/dashboard/stats")
async def dashboard_stats(db: AsyncIOMotorDatabase = Depends(get_db)):
    return await get_dashboard_stats(db)

@app.get("/admin/charts/revenue")
async def revenue_chart(days: int = 30, db: AsyncIOMotorDatabase = Depends(get_db)):
    return await get_revenue_chart(db, days)

@app.get("/admin/charts/user-growth")
async def user_growth(days: int = 30, db: AsyncIOMotorDatabase = Depends(get_db)):
    return await get_user_growth_chart(db, days)

@app.get("/admin/stats/commands")
async def command_stats(db: AsyncIOMotorDatabase = Depends(get_db)):
    return await get_command_usage_stats(db)

@app.get("/admin/stats/top-users")
async def top_users(limit: int = 10, db: AsyncIOMotorDatabase = Depends(get_db)):
    return await get_top_users_by_usage(db, limit)

@app.post("/admin/cache/clear")
async def clear_cache():
    await cache.clear()
    return {"message": "Cache cleared"}
"""
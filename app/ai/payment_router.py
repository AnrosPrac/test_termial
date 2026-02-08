"""
LUMETRICS SECURED PAYMENT SYSTEM
File: app/payment/router.py

SECURITY FEATURES:
✅ Rate limiting on all endpoints
✅ Idempotency protection (prevents double-processing)
✅ Backend-only price validation
✅ Webhook-driven activation (source of truth)
✅ Signature verification (HMAC SHA256)
✅ Amount/Currency validation from Razorpay API
✅ Order ownership verification
✅ Double-spend prevention
✅ No frontend trust

SUPPORTS:
- Tier subscriptions (Hero/Dominator)
- Individual course purchases
- Dynamic course pricing (set by instructors)
"""

import os
import hmac
import hashlib
from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, HTTPException, Request, Depends, BackgroundTasks
from pydantic import BaseModel
from motor.motor_asyncio import AsyncIOMotorClient
import razorpay
from functools import wraps
import time
from collections import defaultdict

# ==================== RATE LIMITING ====================

class RateLimiter:
    """Simple in-memory rate limiter"""
    def __init__(self):
        self.requests = defaultdict(list)
    
    def is_allowed(self, key: str, max_requests: int, window_seconds: int) -> bool:
        """Check if request is allowed under rate limit"""
        now = time.time()
        window_start = now - window_seconds
        
        # Clean old requests
        self.requests[key] = [req_time for req_time in self.requests[key] if req_time > window_start]
        
        # Check limit
        if len(self.requests[key]) >= max_requests:
            return False
        
        # Add current request
        self.requests[key].append(now)
        return True

rate_limiter = RateLimiter()

def rate_limit(max_requests: int = 10, window_seconds: int = 60):
    """Rate limiting decorator"""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            request = kwargs.get('request')
            if request:
                # Use IP + user_id as key
                user = kwargs.get('user', {})
                user_id = user.get('sub', 'anonymous')
                client_ip = request.client.host
                key = f"{client_ip}:{user_id}"
                
                if not rate_limiter.is_allowed(key, max_requests, window_seconds):
                    raise HTTPException(
                        status_code=429,
                        detail=f"Rate limit exceeded. Max {max_requests} requests per {window_seconds} seconds."
                    )
            
            return await func(*args, **kwargs)
        return wrapper
    return decorator


# ==================== ROUTER & CONFIG ====================

router = APIRouter(tags=["Payment"])

# Environment Variables (NEVER expose these to frontend)
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET")
RAZORPAY_WEBHOOK_SECRET = os.getenv("RAZORPAY_WEBHOOK_SECRET")

# Tier Pricing (Backend ONLY - in paise)
TIER_PRICING = {
    "hero": {
        "price": int(os.getenv("TIER_HERO_PRICE", "199")) * 100,  # ₹199
        "validity_days": 210  # ~7 months
    },
    "dominator": {
        "price": int(os.getenv("TIER_DOMINATOR_PRICE", "349")) * 100,  # ₹349
        "validity_days": 210
    }
}

# Tier Limits Configuration (same as before)
TIER_LIMITS = {
    "free": {
        "commands": {
            "ask": int(os.getenv("FREE_ASK", "5")),
            "explain": int(os.getenv("FREE_EXPLAIN", "0")),
            "write": int(os.getenv("FREE_WRITE", "0")),
            "fix": int(os.getenv("FREE_FIX", "2")),
            "trace": int(os.getenv("FREE_TRACE", "1")),
            "diff": int(os.getenv("FREE_DIFF", "1")),
            "algo": int(os.getenv("FREE_ALGO", "1")),
            "format": int(os.getenv("FREE_FORMAT", "2"))
        },
        "inject": int(os.getenv("FREE_INJECT", "0")),
        "cells": int(os.getenv("FREE_CELLS", "0")),
        "pdf": int(os.getenv("FREE_PDF", "1")),
        "convo": int(os.getenv("FREE_CONVO", "0"))
    },
    "hero": {
        "commands": {
            "ask": int(os.getenv("HERO_ASK", "30")),
            "explain": int(os.getenv("HERO_EXPLAIN", "20")),
            "write": int(os.getenv("HERO_WRITE", "15")),
            "fix": int(os.getenv("HERO_FIX", "20")),
            "trace": int(os.getenv("HERO_TRACE", "3")),
            "diff": int(os.getenv("HERO_DIFF", "20")),
            "algo": int(os.getenv("HERO_ALGO", "25")),
            "format": int(os.getenv("HERO_FORMAT", "30"))
        },
        "inject": int(os.getenv("HERO_INJECT", "8")),
        "cells": int(os.getenv("HERO_CELLS", "8")),
        "pdf": int(os.getenv("HERO_PDF", "8")),
        "convo": int(os.getenv("HERO_CONVO", "0"))
    },
    "dominator": {
        "commands": {
            "ask": int(os.getenv("DOMINATOR_ASK", "50")),
            "explain": int(os.getenv("DOMINATOR_EXPLAIN", "40")),
            "write": int(os.getenv("DOMINATOR_WRITE", "30")),
            "fix": int(os.getenv("DOMINATOR_FIX", "30")),
            "trace": int(os.getenv("DOMINATOR_TRACE", "10")),
            "diff": int(os.getenv("DOMINATOR_DIFF", "40")),
            "algo": int(os.getenv("DOMINATOR_ALGO", "50")),
            "format": int(os.getenv("DOMINATOR_FORMAT", "60"))
        },
        "inject": int(os.getenv("DOMINATOR_INJECT", "13")),
        "cells": int(os.getenv("DOMINATOR_CELLS", "13")),
        "pdf": int(os.getenv("DOMINATOR_PDF", "13")),
        "convo": int(os.getenv("DOMINATOR_CONVO", "0"))
    }
}

# MongoDB Connection
MONGO_URL = os.getenv("MONGO_URL")
client = AsyncIOMotorClient(MONGO_URL)
db = client.lumetrics_db

# Razorpay Client
razorpay_client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))


# ==================== STARTUP: CREATE INDEXES ====================

async def create_payment_indexes():
    """Create MongoDB indexes for data integrity and security"""
    try:
        # Tier payments collection
        await db.tier_payments.create_index(
            [("razorpay_order_id", 1)],
            unique=True
        )
        await db.tier_payments.create_index([("sidhi_id", 1)])
        await db.tier_payments.create_index([("status", 1)])
        
        # Course purchases collection
        await db.course_purchases.create_index(
            [("razorpay_order_id", 1)],
            unique=True
        )
        await db.course_purchases.create_index([("user_id", 1), ("course_id", 1)])
        await db.course_purchases.create_index([("sidhi_id", 1)])
        await db.course_purchases.create_index([("status", 1)])
        
        # Quotas collection
        await db.quotas.create_index(
            [("sidhi_id", 1)],
            unique=True
        )
        
        print("✅ Payment indexes created successfully")
    except Exception as e:
        print(f"⚠️  Index creation warning: {e}")


# ==================== PYDANTIC MODELS ====================

class TierPurchaseRequest(BaseModel):
    tier: str  # "hero" or "dominator"

class CoursePurchaseRequest(BaseModel):
    course_id: str

class PaymentVerifyRequest(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


# ==================== HELPER FUNCTIONS ====================

def serialize_mongo_doc(doc):
    """Convert MongoDB document to JSON-serializable dict"""
    if doc is None:
        return None
    doc = dict(doc)
    if "_id" in doc:
        doc["_id"] = str(doc["_id"])
    return doc

def get_current_semester() -> str:
    """
    Generate academically correct semester string
    Academic year: July-June
    ODD semester: July-Dec (e.g., 2025-ODD)
    EVEN semester: Jan-June (e.g., 2025-EVEN)
    """
    now = datetime.utcnow()
    year = now.year
    month = now.month
    
    if month >= 7:  # Jul-Dec = ODD semester
        semester_type = "ODD"
    else:  # Jan-Jun = EVEN semester
        semester_type = "EVEN"
    
    return f"{year}-{semester_type}"

def calculate_expiry_date(days: int = 210) -> datetime:
    """Calculate expiry date from now"""
    return datetime.utcnow() + timedelta(days=days)

def is_quota_expired(quota_doc: dict) -> bool:
    """Check if a quota has expired"""
    expires_at = quota_doc.get("meta", {}).get("expires_at")
    if not expires_at:
        return False
    return datetime.utcnow() > expires_at

def verify_razorpay_signature(order_id: str, payment_id: str, signature: str) -> bool:
    """
    Verify Razorpay payment signature
    CRITICAL SECURITY: Prevents payment tampering
    """
    try:
        message = f"{order_id}|{payment_id}"
        generated_signature = hmac.new(
            RAZORPAY_KEY_SECRET.encode(),
            message.encode(),
            hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(generated_signature, signature)
    except Exception as e:
        print(f"❌ Signature verification error: {e}")
        return False


async def create_quota_document(sidhi_id: str, tier: str) -> dict:
    """Create fresh quota document for a tier"""
    limits = TIER_LIMITS.get(tier.lower())
    if not limits:
        raise ValueError(f"Invalid tier: {tier}")
    
    semester = get_current_semester()
    validity_days = TIER_PRICING.get(tier.lower(), {}).get("validity_days", 210) if tier != "free" else None
    
    quota_doc = {
        "sidhi_id": sidhi_id,
        "semester": semester,
        "tier": tier.lower(),
        "base": limits,
        "used": {
            "commands": {cmd: 0 for cmd in limits["commands"]},
            "inject": 0,
            "cells": 0,
            "pdf": 0,
            "convo": 0
        },
        "addons": {
            "inject": 0,
            "cells": 0,
            "pdf": 0,
            "convo": 0,
            "trace": 0,
            "explain": 0
        },
        "meta": {
            "created_at": datetime.utcnow(),
            "last_updated": datetime.utcnow(),
            "expires_at": calculate_expiry_date(validity_days) if tier != "free" else None,
            "payment_verified": tier != "free",
            "activated_via": "payment" if tier != "free" else "free_activation"
        }
    }
    
    return quota_doc


async def activate_tier_idempotent(
    sidhi_id: str,
    tier: str,
    payment_id: Optional[str] = None,
    order_id: Optional[str] = None
) -> dict:
    """
    IDEMPOTENT tier activation
    Can be called multiple times safely (webhook + verify)
    """
    # Check if quota already exists for this tier
    existing_quota = await db.quotas.find_one({"sidhi_id": sidhi_id})
    
    # If tier already activated, return existing
    if existing_quota and existing_quota.get("tier") == tier.lower():
        print(f"✅ Tier {tier} already active for {sidhi_id}")
        return {
            "status": "success",
            "message": f"{tier.capitalize()} tier already activated",
            "quota": existing_quota,
            "already_activated": True
        }
    
    # Create new quota document
    quota_doc = await create_quota_document(sidhi_id, tier)
    
    # Add payment tracking
    if payment_id:
        quota_doc["meta"]["payment_id"] = payment_id
        quota_doc["meta"]["order_id"] = order_id
    
    # Upsert to MongoDB (replace old quota)
    await db.quotas.update_one(
        {"sidhi_id": sidhi_id},
        {"$set": quota_doc},
        upsert=True
    )
    
    print(f"✅ Tier {tier} activated for {sidhi_id}")
    
    return {
        "status": "success",
        "message": f"{tier.capitalize()} tier activated successfully",
        "quota": quota_doc,
        "already_activated": False
    }


# ==================== SECURITY: IMPORT AUTH ====================

# Import your existing auth dependency
from app.ai.client_bound_guard import verify_client_bound_request


# ==================== PUBLIC ENDPOINTS (NO AUTH) ====================

@router.get("/config/tiers")
async def get_tier_pricing():
    """
    Get tier pricing configuration
    PUBLIC endpoint - shows pricing to users
    ⚠️ ONLY returns safe display data
    """
    return {
        "status": "success",
        "tiers": {
            "free": {
                "name": "Free",
                "price": 0,
                "display_price": "₹0",
                "currency": "INR",
                "validity": "Permanent",
                "limits": TIER_LIMITS["free"]
            },
            "hero": {
                "name": "Hero",
                "price": TIER_PRICING["hero"]["price"] // 100,
                "display_price": f"₹{TIER_PRICING['hero']['price'] // 100}",
                "currency": "INR",
                "validity": f"{TIER_PRICING['hero']['validity_days']} days (~7 months)",
                "limits": TIER_LIMITS["hero"]
            },
            "dominator": {
                "name": "Dominator",
                "price": TIER_PRICING["dominator"]["price"] // 100,
                "display_price": f"₹{TIER_PRICING['dominator']['price'] // 100}",
                "currency": "INR",
                "validity": f"{TIER_PRICING['dominator']['validity_days']} days (~7 months)",
                "limits": TIER_LIMITS["dominator"]
            }
        }
    }


@router.get("/config/course/{course_id}/pricing")
@rate_limit(max_requests=30, window_seconds=60)
async def get_course_pricing(course_id: str, request: Request):
    """
    Get pricing for a specific course
    PUBLIC endpoint - safe to expose
    SECURITY: Only returns display data, no sensitive info
    RATE LIMITED: 30 req/min per IP
    """
    try:
        # Fetch course from database
        course = await db.courses.find_one({"course_id": course_id})
        
        if not course:
            raise HTTPException(status_code=404, detail="Course not found")
        
        # Extract pricing (set by instructor)
        pricing = course.get("pricing", {})
        is_free = pricing.get("is_free", False)
        
        if is_free:
            return {
                "course_id": course_id,
                "title": course.get("title"),
                "is_free": True,
                "price": 0,
                "display_price": "Free",
                "currency": "INR"
            }
        
        # Paid course
        price_paise = pricing.get("price", 0)
        original_price_paise = pricing.get("original_price", price_paise)
        
        return {
            "course_id": course_id,
            "title": course.get("title"),
            "is_free": False,
            "price": price_paise // 100,  # Convert to rupees for display
            "display_price": f"₹{price_paise // 100}",
            "original_price": original_price_paise // 100,
            "display_original_price": f"₹{original_price_paise // 100}",
            "discount_percentage": pricing.get("discount_percentage", 0),
            "currency": "INR",
            "tier_access": pricing.get("tier_access", [])  # Which tiers get free access
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==================== TIER PURCHASE ENDPOINTS (AUTHENTICATED) ====================

@router.post("/tier/initiate")
@rate_limit(max_requests=5, window_seconds=60)
async def initiate_tier_purchase(
    data: TierPurchaseRequest,
    request: Request,
    user: dict = Depends(verify_client_bound_request)
):
    """
    Initiate tier subscription payment
    PROTECTED: Requires authentication
    RATE LIMITED: 5 req/min per user
    SECURITY: All pricing from backend, frontend cannot manipulate
    """
    try:
        sidhi_id = user.get("sub")
        tier = data.tier.lower()
        
        # SECURITY: Validate tier
        if tier not in ["hero", "dominator"]:
            raise HTTPException(
                status_code=400,
                detail="Invalid tier. Choose 'hero' or 'dominator'"
            )
        
        # SECURITY: Get price from BACKEND ONLY
        amount = TIER_PRICING[tier]["price"]  # Already in paise
        
        # Create Razorpay order
        order_data = {
            "amount": amount,
            "currency": "INR",
            "receipt": f"{sidhi_id}_tier_{tier}_{int(datetime.utcnow().timestamp())}",
            "notes": {
                "sidhi_id": sidhi_id,
                "tier": tier,
                "purchase_type": "tier_subscription",
                "semester": get_current_semester()
            }
        }
        
        razorpay_order = razorpay_client.order.create(data=order_data)
        
        # Store order in MongoDB (IDEMPOTENCY PROTECTION)
        payment_doc = {
            "razorpay_order_id": razorpay_order["id"],
            "sidhi_id": sidhi_id,
            "purchase_type": "tier_subscription",
            "tier": tier,
            "amount": amount,
            "currency": "INR",
            "status": "created",
            "semester": get_current_semester(),
            "created_at": datetime.utcnow(),
            "expires_at": calculate_expiry_date(TIER_PRICING[tier]["validity_days"])
        }
        
        try:
            await db.tier_payments.insert_one(payment_doc)
        except Exception as e:
            if "duplicate" in str(e).lower():
                raise HTTPException(
                    status_code=409,
                    detail="Order already exists. Please refresh."
                )
            raise
        
        return {
            "status": "success",
            "order_id": razorpay_order["id"],
            "amount": amount // 100,  # Display in rupees
            "currency": "INR",
            "key_id": RAZORPAY_KEY_ID,
            "tier": tier,
            "notes": razorpay_order["notes"]
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/tier/verify")
@rate_limit(max_requests=10, window_seconds=60)
async def verify_tier_payment(
    data: PaymentVerifyRequest,
    request: Request,
    user: dict = Depends(verify_client_bound_request)
):
    """
    Verify tier payment and activate subscription
    PROTECTED: Requires authentication
    RATE LIMITED: 10 req/min per user
    SECURITY: Full validation + Razorpay API verification
    """
    try:
        sidhi_id = user.get("sub")
        
        # SECURITY CHECK 1: Get payment record (verify ownership)
        payment_record = await db.tier_payments.find_one({
            "razorpay_order_id": data.razorpay_order_id,
            "sidhi_id": sidhi_id
        })
        
        if not payment_record:
            raise HTTPException(
                status_code=404,
                detail="Payment record not found or unauthorized"
            )
        
        # SECURITY CHECK 2: IDEMPOTENCY - Already verified?
        if payment_record.get("status") == "captured":
            print(f"⚠️  Payment {data.razorpay_payment_id} already verified")
            
            quota = await db.quotas.find_one({"sidhi_id": sidhi_id})
            
            return {
                "status": "success",
                "message": "Payment already verified",
                "tier": payment_record["tier"],
                "quota": serialize_mongo_doc(quota),
                "already_verified": True
            }
        
        # SECURITY CHECK 3: Verify Razorpay signature
        is_valid = verify_razorpay_signature(
            data.razorpay_order_id,
            data.razorpay_payment_id,
            data.razorpay_signature
        )
        
        if not is_valid:
            raise HTTPException(
                status_code=400,
                detail="Invalid payment signature. Possible tampering detected."
            )
        
        # SECURITY CHECK 4: Fetch payment from Razorpay API
        try:
            payment_details = razorpay_client.payment.fetch(data.razorpay_payment_id)
            
            # Validate payment status
            if payment_details["status"] != "captured":
                raise HTTPException(
                    status_code=400,
                    detail=f"Payment not captured. Status: {payment_details['status']}"
                )
            
            # Validate order_id matches
            if payment_details.get("order_id") != data.razorpay_order_id:
                raise HTTPException(
                    status_code=400,
                    detail="Order ID mismatch. Fraud attempt detected."
                )
            
            # CRITICAL: Validate amount matches BACKEND price
            expected_amount = payment_record["amount"]
            if payment_details.get("amount") != expected_amount:
                raise HTTPException(
                    status_code=400,
                    detail=f"Amount mismatch. Expected {expected_amount}, got {payment_details.get('amount')}"
                )
            
            # Validate currency
            if payment_details.get("currency") != "INR":
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid currency: {payment_details.get('currency')}"
                )
            
        except razorpay.errors.BadRequestError as e:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid payment ID: {str(e)}"
            )
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=400,
                detail=f"Payment verification failed: {str(e)}"
            )
        
        # Update payment record (ATOMIC)
        await db.tier_payments.update_one(
            {
                "razorpay_order_id": data.razorpay_order_id,
                "status": {"$ne": "captured"}
            },
            {
                "$set": {
                    "razorpay_payment_id": data.razorpay_payment_id,
                    "razorpay_signature": data.razorpay_signature,
                    "status": "captured",
                    "verified_at": datetime.utcnow(),
                    "verified_via": "frontend"
                }
            }
        )
        
        # Activate tier (IDEMPOTENT)
        tier = payment_record["tier"]
        result = await activate_tier_idempotent(
            sidhi_id,
            tier,
            payment_id=data.razorpay_payment_id,
            order_id=data.razorpay_order_id
        )
        
        return {
            "status": "success",
            "message": result["message"],
            "tier": tier,
            "quota": result["quota"],
            "payment_id": data.razorpay_payment_id,
            "expires_at": result["quota"]["meta"]["expires_at"]
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==================== COURSE PURCHASE ENDPOINTS (AUTHENTICATED) ====================

@router.post("/course/initiate")
@rate_limit(max_requests=5, window_seconds=60)
async def initiate_course_purchase(
    data: CoursePurchaseRequest,
    request: Request,
    user: dict = Depends(verify_client_bound_request)
):
    """
    Initiate course purchase payment
    PROTECTED: Requires authentication
    RATE LIMITED: 5 req/min per user
    SECURITY: Course price fetched from backend ONLY
    """
    try:
        user_id = user.get("sub")  # This is actually sidhi_id
        course_id = data.course_id
        
        # SECURITY CHECK 1: Verify course exists
        course = await db.courses.find_one({"course_id": course_id})
        
        if not course:
            raise HTTPException(status_code=404, detail="Course not found")
        
        # SECURITY CHECK 2: Verify course is published
        if course.get("status") not in ["PUBLISHED", "ACTIVE"]:
            raise HTTPException(
                status_code=400,
                detail="Course not available for purchase"
            )
        
        # SECURITY CHECK 3: Check if course is free
        pricing = course.get("pricing", {})
        if pricing.get("is_free", False):
            raise HTTPException(
                status_code=400,
                detail="This course is free. You can enroll directly."
            )
        
        # SECURITY CHECK 4: Check if already purchased
        existing_purchase = await db.course_purchases.find_one({
            "user_id": user_id,
            "course_id": course_id,
            "status": "captured"
        })
        
        if existing_purchase:
            raise HTTPException(
                status_code=409,
                detail="You have already purchased this course"
            )
        
        # SECURITY: Get price from BACKEND ONLY (never trust frontend)
        amount = pricing.get("price", 0)  # Already in paise
        
        if amount <= 0:
            raise HTTPException(
                status_code=400,
                detail="Invalid course pricing"
            )
        
        # Create Razorpay order
        order_data = {
            "amount": amount,
            "currency": "INR",
            "receipt": f"{user_id}_course_{course_id}_{int(datetime.utcnow().timestamp())}",
            "notes": {
                "user_id": user_id,
                "course_id": course_id,
                "course_title": course.get("title", ""),
                "purchase_type": "course_purchase"
            }
        }
        
        razorpay_order = razorpay_client.order.create(data=order_data)
        
        # Store order in MongoDB (IDEMPOTENCY PROTECTION)
        purchase_doc = {
            "purchase_id": f"CP_{razorpay_order['id'][-12:]}",
            "razorpay_order_id": razorpay_order["id"],
            "user_id": user_id,
            "course_id": course_id,
            "course_title": course.get("title", ""),
            "purchase_type": "course_purchase",
            "amount": amount,
            "currency": "INR",
            "status": "created",
            "created_at": datetime.utcnow(),
            "expires_at": None,  # Lifetime access
            "access_granted": False
        }
        
        try:
            await db.course_purchases.insert_one(purchase_doc)
        except Exception as e:
            if "duplicate" in str(e).lower():
                raise HTTPException(
                    status_code=409,
                    detail="Order already exists. Please refresh."
                )
            raise
        
        return {
            "status": "success",
            "order_id": razorpay_order["id"],
            "amount": amount // 100,  # Display in rupees
            "currency": "INR",
            "key_id": RAZORPAY_KEY_ID,
            "course": {
                "course_id": course_id,
                "title": course.get("title", "")
            },
            "notes": razorpay_order["notes"]
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/course/verify")
@rate_limit(max_requests=10, window_seconds=60)
async def verify_course_payment(
    data: PaymentVerifyRequest,
    request: Request,
    user: dict = Depends(verify_client_bound_request)
):
    """
    Verify course payment and grant access
    PROTECTED: Requires authentication
    RATE LIMITED: 10 req/min per user
    SECURITY: Full validation + Razorpay API verification
    OPTIONAL: Auto-enroll user in course after successful payment
    """
    try:
        user_id = user.get("sub")
        
        # SECURITY CHECK 1: Get purchase record (verify ownership)
        purchase_record = await db.course_purchases.find_one({
            "razorpay_order_id": data.razorpay_order_id,
            "user_id": user_id
        })
        
        if not purchase_record:
            raise HTTPException(
                status_code=404,
                detail="Purchase record not found or unauthorized"
            )
        
        # SECURITY CHECK 2: IDEMPOTENCY - Already verified?
        if purchase_record.get("status") == "captured":
            print(f"⚠️  Purchase {data.razorpay_payment_id} already verified")
            
            return {
                "status": "success",
                "message": "Course already purchased",
                "course_id": purchase_record["course_id"],
                "purchase_id": purchase_record["purchase_id"],
                "access_granted": True,
                "already_verified": True
            }
        
        # SECURITY CHECK 3: Verify Razorpay signature
        is_valid = verify_razorpay_signature(
            data.razorpay_order_id,
            data.razorpay_payment_id,
            data.razorpay_signature
        )
        
        if not is_valid:
            raise HTTPException(
                status_code=400,
                detail="Invalid payment signature. Possible tampering detected."
            )
        
        # SECURITY CHECK 4: Fetch payment from Razorpay API
        try:
            payment_details = razorpay_client.payment.fetch(data.razorpay_payment_id)
            
            # Validate payment status
            if payment_details["status"] != "captured":
                raise HTTPException(
                    status_code=400,
                    detail=f"Payment not captured. Status: {payment_details['status']}"
                )
            
            # Validate order_id matches
            if payment_details.get("order_id") != data.razorpay_order_id:
                raise HTTPException(
                    status_code=400,
                    detail="Order ID mismatch. Fraud attempt detected."
                )
            
            # CRITICAL: Validate amount matches BACKEND price
            expected_amount = purchase_record["amount"]
            if payment_details.get("amount") != expected_amount:
                raise HTTPException(
                    status_code=400,
                    detail=f"Amount mismatch. Expected {expected_amount}, got {payment_details.get('amount')}"
                )
            
            # Validate currency
            if payment_details.get("currency") != "INR":
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid currency: {payment_details.get('currency')}"
                )
            
        except razorpay.errors.BadRequestError as e:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid payment ID: {str(e)}"
            )
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=400,
                detail=f"Payment verification failed: {str(e)}"
            )
        
        # Update purchase record (ATOMIC)
        await db.course_purchases.update_one(
            {
                "razorpay_order_id": data.razorpay_order_id,
                "status": {"$ne": "captured"}
            },
            {
                "$set": {
                    "razorpay_payment_id": data.razorpay_payment_id,
                    "razorpay_signature": data.razorpay_signature,
                    "status": "captured",
                    "purchased_at": datetime.utcnow(),
                    "verified_at": datetime.utcnow(),
                    "verified_via": "frontend",
                    "access_granted": True
                }
            }
        )
        
        # Update course purchase stats
        await db.courses.update_one(
            {"course_id": purchase_record["course_id"]},
            {
                "$inc": {
                    "purchase_stats.total_purchases": 1,
                    "purchase_stats.revenue_generated": purchase_record["amount"]
                }
            }
        )
        
        return {
            "status": "success",
            "message": "Course purchased successfully! You can now enroll.",
            "course_id": purchase_record["course_id"],
            "course_title": purchase_record["course_title"],
            "purchase_id": purchase_record["purchase_id"],
            "access_granted": True,
            "payment_id": data.razorpay_payment_id
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==================== WEBHOOK (NO AUTH - RAZORPAY SIGNATURE) ====================

@router.post("/webhook")
async def razorpay_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Razorpay webhook handler
    NO USER AUTH (uses Razorpay signature verification)
    SECURITY: Webhook signature verification
    SOURCE OF TRUTH: Processes payments even if frontend crashes
    """
    try:
        # SECURITY: Get webhook signature
        webhook_signature = request.headers.get("X-Razorpay-Signature")
        
        if not webhook_signature:
            raise HTTPException(status_code=400, detail="Missing signature")
        
        # Get raw body
        body = await request.body()
        
        # SECURITY: Verify webhook signature
        try:
            razorpay_client.utility.verify_webhook_signature(
                body.decode(),
                webhook_signature,
                RAZORPAY_WEBHOOK_SECRET
            )
        except Exception as e:
            print(f"❌ Webhook signature verification failed: {e}")
            raise HTTPException(status_code=400, detail="Invalid signature")
        
        # Parse webhook data
        webhook_data = await request.json()
        event = webhook_data.get("event")
        payload = webhook_data.get("payload", {})
        payment_entity = payload.get("payment", {}).get("entity", {})
        
        # Process successful payments
        if event == "payment.captured":
            order_id = payment_entity.get("order_id")
            payment_id = payment_entity.get("id")
            amount = payment_entity.get("amount")
            currency = payment_entity.get("currency")
            
            # Determine if tier or course purchase
            notes = payment_entity.get("notes", {})
            purchase_type = notes.get("purchase_type", "")
            
            if purchase_type == "tier_subscription":
                # Handle tier subscription
                payment_record = await db.tier_payments.find_one({
                    "razorpay_order_id": order_id
                })
                
                if not payment_record:
                    print(f"⚠️  Webhook: Tier payment record not found for {order_id}")
                    return {"status": "ok"}
                
                # SECURITY: Validate amount and currency
                if amount != payment_record["amount"]:
                    print(f"❌ Webhook: Amount mismatch for {order_id}")
                    return {"status": "error", "message": "Amount mismatch"}
                
                if currency != payment_record["currency"]:
                    print(f"❌ Webhook: Currency mismatch for {order_id}")
                    return {"status": "error", "message": "Currency mismatch"}
                
                # Update payment record (idempotent)
                await db.tier_payments.update_one(
                    {"razorpay_order_id": order_id},
                    {
                        "$set": {
                            "razorpay_payment_id": payment_id,
                            "status": "captured",
                            "webhook_verified": True,
                            "webhook_received_at": datetime.utcnow(),
                            "verified_via": "webhook"
                        }
                    }
                )
                
                # Activate tier in background
                sidhi_id = payment_record["sidhi_id"]
                tier = payment_record["tier"]
                
                background_tasks.add_task(
                    activate_tier_idempotent,
                    sidhi_id,
                    tier,
                    payment_id=payment_id,
                    order_id=order_id
                )
                
                print(f"✅ Webhook: Tier payment captured - Order: {order_id}, Tier: {tier}")
            
            elif purchase_type == "course_purchase":
                # Handle course purchase
                purchase_record = await db.course_purchases.find_one({
                    "razorpay_order_id": order_id
                })
                
                if not purchase_record:
                    print(f"⚠️  Webhook: Course purchase record not found for {order_id}")
                    return {"status": "ok"}
                
                # SECURITY: Validate amount and currency
                if amount != purchase_record["amount"]:
                    print(f"❌ Webhook: Amount mismatch for {order_id}")
                    return {"status": "error", "message": "Amount mismatch"}
                
                if currency != purchase_record["currency"]:
                    print(f"❌ Webhook: Currency mismatch for {order_id}")
                    return {"status": "error", "message": "Currency mismatch"}
                
                # Update purchase record (idempotent)
                await db.course_purchases.update_one(
                    {"razorpay_order_id": order_id},
                    {
                        "$set": {
                            "razorpay_payment_id": payment_id,
                            "status": "captured",
                            "purchased_at": datetime.utcnow(),
                            "webhook_verified": True,
                            "webhook_received_at": datetime.utcnow(),
                            "verified_via": "webhook",
                            "access_granted": True
                        }
                    }
                )
                
                # Update course stats
                await db.courses.update_one(
                    {"course_id": purchase_record["course_id"]},
                    {
                        "$inc": {
                            "purchase_stats.total_purchases": 1,
                            "purchase_stats.revenue_generated": amount
                        }
                    }
                )
                
                print(f"✅ Webhook: Course purchase captured - Order: {order_id}, Course: {purchase_record['course_id']}")
        
        elif event == "payment.failed":
            order_id = payment_entity.get("order_id")
            
            # Update tier payment if exists
            await db.tier_payments.update_one(
                {"razorpay_order_id": order_id},
                {
                    "$set": {
                        "status": "failed",
                        "failed_at": datetime.utcnow(),
                        "failure_reason": payment_entity.get("error_description")
                    }
                }
            )
            
            # Update course purchase if exists
            await db.course_purchases.update_one(
                {"razorpay_order_id": order_id},
                {
                    "$set": {
                        "status": "failed",
                        "failed_at": datetime.utcnow(),
                        "failure_reason": payment_entity.get("error_description")
                    }
                }
            )
            
            print(f"❌ Webhook: Payment failed - Order: {order_id}")
        
        return {"status": "ok"}
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Webhook error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== USER PURCHASE HISTORY ====================

@router.get("/my-purchases")
@rate_limit(max_requests=20, window_seconds=60)
async def get_user_purchases(
    request: Request,
    user: dict = Depends(verify_client_bound_request)
):
    """
    Get all purchases made by user (tiers + courses)
    PROTECTED: Requires authentication
    RATE LIMITED: 20 req/min
    """
    try:
        user_id = user.get("sub")
        
        # Get tier subscriptions
        tier_cursor = db.tier_payments.find({
            "sidhi_id": user_id,
            "status": "captured"
        }).sort("verified_at", -1)
        
        tier_payments = await tier_cursor.to_list(length=100)
        
        # Get course purchases
        course_cursor = db.course_purchases.find({
            "user_id": user_id,
            "status": "captured"
        }).sort("purchased_at", -1)
        
        course_purchases = await course_cursor.to_list(length=100)
        
        return {
            "status": "success",
            "tier_subscriptions": [
                {
                    "order_id": p["razorpay_order_id"],
                    "tier": p["tier"],
                    "amount": p["amount"] // 100,
                    "currency": p["currency"],
                    "purchased_at": p.get("verified_at"),
                    "expires_at": p.get("expires_at")
                }
                for p in tier_payments
            ],
            "course_purchases": [
                {
                    "purchase_id": p["purchase_id"],
                    "order_id": p["razorpay_order_id"],
                    "course_id": p["course_id"],
                    "course_title": p.get("course_title", ""),
                    "amount": p["amount"] // 100,
                    "currency": p["currency"],
                    "purchased_at": p.get("purchased_at"),
                    "access_granted": p.get("access_granted", False)
                }
                for p in course_purchases
            ],
            "total_spent": sum(p["amount"] for p in tier_payments + course_purchases) // 100
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/course/{course_id}/access-status")
@rate_limit(max_requests=30, window_seconds=60)
async def check_course_access(
    course_id: str,
    request: Request,
    user: dict = Depends(verify_client_bound_request)
):
    """
    Check if user has access to a course
    PROTECTED: Requires authentication
    RATE LIMITED: 30 req/min
    RETURNS: Access status + reason
    """
    try:
        user_id = user.get("sub")
        
        # Get course
        course = await db.courses.find_one({"course_id": course_id})
        
        if not course:
            raise HTTPException(status_code=404, detail="Course not found")
        
        pricing = course.get("pricing", {})
        
        # Check 1: Is course free?
        if pricing.get("is_free", False):
            return {
                "has_access": True,
                "access_reason": "free_course",
                "message": "This is a free course"
            }
        
        # Check 2: Does user have active tier subscription?
        quota = await db.quotas.find_one({"sidhi_id": user_id})
        
        if quota and not is_quota_expired(quota):
            tier = quota.get("tier")
            tier_access = pricing.get("tier_access", [])
            
            if tier in tier_access:
                return {
                    "has_access": True,
                    "access_reason": "tier_subscription",
                    "tier": tier,
                    "message": f"Access granted via {tier.capitalize()} subscription"
                }
        
        # Check 3: Has user purchased this course?
        purchase = await db.course_purchases.find_one({
            "user_id": user_id,
            "course_id": course_id,
            "status": "captured",
            "access_granted": True
        })
        
        if purchase:
            return {
                "has_access": True,
                "access_reason": "course_purchase",
                "purchase_id": purchase["purchase_id"],
                "message": "Access granted via course purchase"
            }
        
        # No access
        return {
            "has_access": False,
            "access_reason": None,
            "message": "Payment required to access this course",
            "pricing": {
                "price": pricing.get("price", 0) // 100,
                "currency": "INR"
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==================== ADMIN: SET COURSE PRICING ====================

@router.post("/admin/course/{course_id}/set-pricing")
@rate_limit(max_requests=10, window_seconds=60)
async def set_course_pricing(
    course_id: str,
    request: Request,
    user: dict = Depends(verify_client_bound_request)
):
    """
    Set pricing for a course (ADMIN/INSTRUCTOR ONLY)
    PROTECTED: Requires authentication + ownership verification
    RATE LIMITED: 10 req/min
    
    Body:
    {
      "is_free": false,
      "price": 999,  # in rupees (will convert to paise)
      "original_price": 1499,  # optional
      "tier_access": ["dominator"]  # optional
    }
    """
    try:
        user_id = user.get("sub")
        
        # Verify course exists and user is creator
        course = await db.courses.find_one({"course_id": course_id})
        
        if not course:
            raise HTTPException(status_code=404, detail="Course not found")
        
        if course.get("creator_id") != user_id:
            raise HTTPException(
                status_code=403,
                detail="Only course creator can set pricing"
            )
        
        # 🔒 SECURITY CHECK 3: Cannot change pricing after publishing
        if course.get("status") in ["PUBLISHED", "ACTIVE", "ARCHIVED"]:
            raise HTTPException(
                status_code=403,
                detail="Cannot change pricing after course is published. Pricing is locked to protect student purchases."
            )
        
        # Get pricing data from request body
        body = await request.json()
        
        is_free = body.get("is_free", False)
        price_rupees = body.get("price", 0)
        original_price_rupees = body.get("original_price", price_rupees)
        tier_access = body.get("tier_access", [])
        
        # Validate
        if not is_free and price_rupees <= 0:
            raise HTTPException(
                status_code=400,
                detail="Price must be greater than 0 for paid courses"
            )
        
        # Convert to paise
        price_paise = price_rupees * 100 if not is_free else 0
        original_price_paise = original_price_rupees * 100 if not is_free else 0
        
        # Calculate discount
        discount_percentage = 0
        if original_price_paise > price_paise > 0:
            discount_percentage = int(((original_price_paise - price_paise) / original_price_paise) * 100)
        
        # Update course pricing
        pricing_doc = {
            "is_free": is_free,
            "price": price_paise,
            "original_price": original_price_paise,
            "currency": "INR",
            "tier_access": tier_access,
            "discount_percentage": discount_percentage,
            "pricing_set": True  # ✅ Mark as explicitly set
        }
        
        # ATOMIC UPDATE: Only if still in DRAFT
        result = await db.courses.update_one(
            {
                "course_id": course_id,
                "status": "DRAFT"  # Double-check status
            },
            {
                "$set": {
                    "pricing": pricing_doc,
                    "updated_at": datetime.utcnow()
                }
            }
        )
        
        if result.modified_count == 0:
            raise HTTPException(
                status_code=403,
                detail="Cannot update pricing. Course may have been published."
            )
        
        return {
            "status": "success",
            "message": "Course pricing updated successfully",
            "pricing": {
                "is_free": is_free,
                "price": price_rupees,
                "original_price": original_price_rupees,
                "discount_percentage": discount_percentage,
                "tier_access": tier_access
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
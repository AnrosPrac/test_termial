"""
Razorpay UPI Payment Integration for Lumetrics - PRODUCTION SECURE
File: app/payment/router.py

SECURITY FIXES:
✅ Idempotency protection
✅ Webhook-driven quota activation
✅ Full payment validation (order_id, amount, currency)
✅ Expiry enforcement
✅ Proper semester calculation
✅ Free tier re-activation protection

Add to main.py:
from app.payment.router import router as payment_router
app.include_router(payment_router, prefix="/payment")
"""

import os
import hmac
import hashlib
from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, HTTPException, Request, Depends, Header, BackgroundTasks
from pydantic import BaseModel
from motor.motor_asyncio import AsyncIOMotorClient
import razorpay
from app.ai.client_bound_guard import verify_client_bound_request

router = APIRouter(tags=["Payment"])

# Environment Variables
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET")
RAZORPAY_WEBHOOK_SECRET = os.getenv("RAZORPAY_WEBHOOK_SECRET")

# Tier Pricing (INR in paise - multiply by 100)
TIER_HERO_PRICE = int(os.getenv("TIER_HERO_PRICE", "199")) * 100  # ₹199
TIER_DOMINATOR_PRICE = int(os.getenv("TIER_DOMINATOR_PRICE", "349")) * 100  # ₹349

class PaymentInitiateRequest(BaseModel):
    tier: str  # "hero" or "dominator"
    referral_code: Optional[str] = None  # NEW: Optional referral code
def serialize_mongo_doc(doc):
    """Convert MongoDB document to JSON-serializable dict"""
    if doc is None:
        return None
    doc = dict(doc)
    if "_id" in doc:
        doc["_id"] = str(doc["_id"])  # Convert ObjectId to string
    return doc
# Tier Limits Configuration
TIER_LIMITS = {
    "free": {
        "commands": {
            "ask": int(os.getenv("FREE_ASK", "50")),
            "explain": int(os.getenv("FREE_EXPLAIN", "40")),
            "write": int(os.getenv("FREE_WRITE", "30")),
            "fix": int(os.getenv("FREE_FIX", "25")),
            "trace": int(os.getenv("FREE_TRACE", "20")),
            "diff": int(os.getenv("FREE_DIFF", "15")),
            "algo": int(os.getenv("FREE_ALGO", "10")),
            "format": int(os.getenv("FREE_FORMAT", "20"))
        },
        "inject": int(os.getenv("FREE_INJECT", "5")),
        "cells": int(os.getenv("FREE_CELLS", "100")),
        "pdf": int(os.getenv("FREE_PDF", "10")),
        "convo": int(os.getenv("FREE_CONVO", "200"))
    },
    "hero": {
        "commands": {
            "ask": int(os.getenv("HERO_ASK", "150")),
            "explain": int(os.getenv("HERO_EXPLAIN", "120")),
            "write": int(os.getenv("HERO_WRITE", "100")),
            "fix": int(os.getenv("HERO_FIX", "80")),
            "trace": int(os.getenv("HERO_TRACE", "60")),
            "diff": int(os.getenv("HERO_DIFF", "50")),
            "algo": int(os.getenv("HERO_ALGO", "40")),
            "format": int(os.getenv("HERO_FORMAT", "60"))
        },
        "inject": int(os.getenv("HERO_INJECT", "20")),
        "cells": int(os.getenv("HERO_CELLS", "300")),
        "pdf": int(os.getenv("HERO_PDF", "30")),
        "convo": int(os.getenv("HERO_CONVO", "500"))
    },
    "dominator": {
        "commands": {
            "ask": int(os.getenv("DOMINATOR_ASK", "300")),
            "explain": int(os.getenv("DOMINATOR_EXPLAIN", "250")),
            "write": int(os.getenv("DOMINATOR_WRITE", "200")),
            "fix": int(os.getenv("DOMINATOR_FIX", "150")),
            "trace": int(os.getenv("DOMINATOR_TRACE", "120")),
            "diff": int(os.getenv("DOMINATOR_DIFF", "100")),
            "algo": int(os.getenv("DOMINATOR_ALGO", "80")),
            "format": int(os.getenv("DOMINATOR_FORMAT", "120"))
        },
        "inject": int(os.getenv("DOMINATOR_INJECT", "50")),
        "cells": int(os.getenv("DOMINATOR_CELLS", "600")),
        "pdf": int(os.getenv("DOMINATOR_PDF", "60")),
        "convo": int(os.getenv("DOMINATOR_CONVO", "1000"))
    }
}

# MongoDB Connection
MONGO_URL = os.getenv("MONGO_URL")
client = AsyncIOMotorClient(MONGO_URL)
db = client.lumetrics_db
# Add this after TIER_DOMINATOR_PRICE line
BASE_DISCOUNT = int(os.getenv("BASE_DISCOUNT", "39")) * 100  # ₹39 in paise (default)

# Razorpay Client
razorpay_client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))


# ==================== STARTUP: CREATE INDEXES ====================

async def create_indexes():
    """Create MongoDB indexes for data integrity"""
    try:
        # Unique index on razorpay_order_id (prevents duplicate processing)
        await db.payments.create_index(
            [("razorpay_order_id", 1)],
            unique=True
        )
        
        # Index on sidhi_id for fast lookups
        await db.payments.create_index([("sidhi_id", 1)])
        
        # Unique index on sidhi_id in quotas (one quota per user)
        await db.quotas.create_index(
            [("sidhi_id", 1)],
            unique=True
        )
        
        print("✅ Payment indexes created successfully")
    except Exception as e:
        print(f"⚠️  Index creation warning: {e}")


# Call this on app startup (add to main.py)
# @app.on_event("startup")
# async def startup_event():
#     await create_indexes()


# ==================== PYDANTIC MODELS ====================

class TierActivationRequest(BaseModel):
    tier: str  # "free", "hero", "dominator"


class PaymentInitiateRequest(BaseModel):
    tier: str  # "hero" or "dominator" only


class PaymentVerifyRequest(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


# ==================== HELPER FUNCTIONS ====================

def get_current_semester() -> str:
    """
    Generate academically correct semester string
    Academic year: July-June
    ODD semester: July-Dec (e.g., 2025-ODD)
    EVEN semester: Jan-June (e.g., 2025-EVEN, belongs to 2024-2025 academic year)
    """
    now = datetime.utcnow()
    year = now.year
    month = now.month
    
    # Academic year starts in July
    if month >= 7:  # Jul-Dec = ODD semester of current year
        semester_type = "ODD"
    else:  # Jan-Jun = EVEN semester (but academic year started previous year)
        semester_type = "EVEN"
    
    return f"{year}-{semester_type}"


def calculate_expiry_date() -> datetime:
    """Calculate expiry date (7 months from now)"""
    return datetime.utcnow() + timedelta(days=7*30)  # ~7 months


def is_quota_expired(quota_doc: dict) -> bool:
    """Check if a quota has expired"""
    expires_at = quota_doc.get("meta", {}).get("expires_at")
    if not expires_at:
        return False  # Free tier or no expiry set
    
    return datetime.utcnow() > expires_at


async def create_quota_document(sidhi_id: str, tier: str) -> dict:
    """Create fresh quota document for a tier"""
    limits = TIER_LIMITS.get(tier.lower())
    if not limits:
        raise ValueError(f"Invalid tier: {tier}")
    
    semester = get_current_semester()
    
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
            "expires_at": calculate_expiry_date() if tier != "free" else None,
            "payment_verified": tier != "free",
            "activated_via": "payment" if tier != "free" else "free_activation"
        }
    }
    
    return quota_doc


def verify_razorpay_signature(order_id: str, payment_id: str, signature: str) -> bool:
    """Verify Razorpay payment signature"""
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


async def activate_tier_idempotent(
    sidhi_id: str,
    tier: str,
    payment_id: Optional[str] = None,
    order_id: Optional[str] = None
) -> dict:
    """
    IDEMPOTENT tier activation
    Can be called multiple times safely (webhook + verify)
    Returns existing quota if already activated
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


# ==================== API ENDPOINTS ====================

@router.get("/config")
async def get_tier_config():
    """
    Get tier pricing and limits configuration
    Public endpoint - no auth required
    """
    return {
        "status": "success",
        "tiers": {
            "free": {
                "price": 0,
                "price_inr": "₹0",
                "limits": TIER_LIMITS["free"],
                "validity": "Permanent"
            },
            "hero": {
                "price": TIER_HERO_PRICE // 100,
                "price_inr": f"₹{TIER_HERO_PRICE // 100}",
                "limits": TIER_LIMITS["hero"],
                "validity": "7 months"
            },
            "dominator": {
                "price": TIER_DOMINATOR_PRICE // 100,
                "price_inr": f"₹{TIER_DOMINATOR_PRICE // 100}",
                "limits": TIER_LIMITS["dominator"],
                "validity": "7 months"
            }
        }
    }


@router.post("/activate-free")
async def activate_free_tier(
    data: TierActivationRequest,
    user: dict = Depends(verify_client_bound_request)
):
    """
    Activate FREE tier - No payment required
    Protected endpoint - requires authentication
    
    FIX: Prevents re-activation spam
    """
    try:
        sidhi_id = user.get("sub")
        
        if data.tier.lower() != "free":
            raise HTTPException(
                status_code=400, 
                detail="Use /payment/initiate for paid tiers"
            )
        
        # Check existing quota
        existing = await db.quotas.find_one({"sidhi_id": sidhi_id})
        
        # Prevent downgrade from paid tier
        if existing and existing.get("tier") in ["hero", "dominator"]:
            # Check if expired
            if not is_quota_expired(existing):
                raise HTTPException(
                    status_code=400,
                    detail=f"You have an active {existing['tier']} tier. Wait for expiry or contact support."
                )
        
        # Prevent free tier re-activation (quota reset exploit)
        if existing and existing.get("tier") == "free":
            raise HTTPException(
                status_code=400,
                detail="Free tier already activated. Cannot reset quota."
            )
        
        # Activate free tier
        result = await activate_tier_idempotent(sidhi_id, "free")
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/referral/my-code")
async def get_my_referral_code(user: dict = Depends(verify_client_bound_request)):
    """
    Generate/retrieve user's unique referral code
    Only for FREE tier users
    """
    try:
        sidhi_id = user.get("sub")
        
        # Check: User must be on FREE tier to share code
        quota = await db.quotas.find_one({"sidhi_id": sidhi_id})
        if not quota or quota.get("tier") != "free":
            raise HTTPException(
                status_code=403,
                detail="Referral codes only available for free tier users"
            )
        
        # Check if code already exists
        existing = await db.referral_codes.find_one({"sidhi_id": sidhi_id})
        
        if existing:
            return {
                "status": "success",
                "referral_code": existing["code"],
                "total_referrals": existing.get("total_referrals", 0),
                "pending_bonus": existing.get("pending_bonus", 0) // 100  # Convert to rupees
            }
        
        # Generate unique code
        import secrets
        code = f"{sidhi_id.split('@')[0].upper()}_{secrets.token_hex(4).upper()}"
        
        # Save code
        await db.referral_codes.insert_one({
            "sidhi_id": sidhi_id,
            "code": code,
            "tier_at_creation": "free",
            "total_referrals": 0,
            "pending_bonus": 0,
            "created_at": datetime.utcnow()
        })
        
        return {
            "status": "success",
            "referral_code": code,
            "total_referrals": 0,
            "pending_bonus": 0
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
@router.post("/initiate")
async def initiate_payment(
    data: PaymentInitiateRequest,
    user: dict = Depends(verify_client_bound_request)
):
    """
    Initiate Razorpay payment for Hero or Dominator tier
    WITH REFERRAL CODE SUPPORT (non-paid to non-paid only)
    """
    try:
        sidhi_id = user.get("sub")
        tier = data.tier.lower()
        
        # Validate tier
        if tier not in ["hero", "dominator"]:
            raise HTTPException(
                status_code=400,
                detail="Invalid tier. Use 'hero' or 'dominator'"
            )
        
        # Base price
        amount = TIER_HERO_PRICE if tier == "hero" else TIER_DOMINATOR_PRICE
        discount = 0
        referral_valid = False
        referrer_sidhi_id = None
        credit_used = 0
        
        # Check user's current tier
        quota = await db.quotas.find_one({"sidhi_id": sidhi_id})
        user_is_free_tier = not quota or quota.get("tier") == "free"
        
        # ===== REFERRAL CODE VALIDATION =====
        if data.referral_code:
            # Only free tier users can use referral codes
            if not user_is_free_tier:
                raise HTTPException(
                    status_code=403,
                    detail="Referral codes can only be used by free tier users on their first payment"
                )
            
            # Find referral code owner
            referral_code_doc = await db.referral_codes.find_one({
                "code": data.referral_code.upper()
            })
            
            if not referral_code_doc:
                raise HTTPException(status_code=400, detail="Invalid referral code")
            
            referrer_sidhi_id = referral_code_doc["sidhi_id"]
            
            # Can't use own code
            if referrer_sidhi_id == sidhi_id:
                raise HTTPException(status_code=400, detail="Cannot use your own referral code")
            
            # Check: Referrer must STILL be on FREE tier
            referrer_quota = await db.quotas.find_one({"sidhi_id": referrer_sidhi_id})
            if not referrer_quota or referrer_quota.get("tier") != "free":
                raise HTTPException(
                    status_code=400,
                    detail="Referral code is no longer valid (referrer has upgraded to paid tier)"
                )
            
            # Check: User hasn't already used a referral code before
            already_used = await db.referrals.find_one({
                "referee_sidhi_id": sidhi_id,
                "status": {"$in": ["completed", "pending"]}
            })
            
            if already_used:
                raise HTTPException(
                    status_code=400,
                    detail="You have already used a referral code"
                )
            
            # ALL CHECKS PASSED - Apply discount
            discount = BASE_DISCOUNT
            amount = max(amount - discount, 0)
            referral_valid = True
        
        # ===== REFERRAL CREDIT (for referrers making their own payment) =====
        if not data.referral_code:  # Only check credit if NOT using referral code
            referral_credit_doc = await db.referral_codes.find_one({"sidhi_id": sidhi_id})
            available_credit = referral_credit_doc.get("pending_bonus", 0) if referral_credit_doc else 0
            
            if available_credit > 0:
                credit_used = min(available_credit, amount)  # Can't exceed order amount
                amount -= credit_used
                
                # Deduct from pending bonus (will be confirmed after payment)
                # We'll finalize this in /verify endpoint
        
        # Create Razorpay order
        order_data = {
            "amount": amount,
            "currency": "INR",
            "receipt": f"{sidhi_id}_{tier}_{int(datetime.utcnow().timestamp())}",
            "notes": {
                "sidhi_id": sidhi_id,
                "tier": tier,
                "semester": get_current_semester(),
                "referral_applied": referral_valid,
                "discount_amount": discount,
                "credit_used": credit_used
            }
        }
        
        razorpay_order = razorpay_client.order.create(data=order_data)
        
        # Store payment record
        payment_doc = {
            "razorpay_order_id": razorpay_order["id"],
            "sidhi_id": sidhi_id,
            "tier": tier,
            "amount": amount,
            "original_amount": amount + discount + credit_used,
            "discount": discount,
            "credit_used": credit_used,
            "currency": "INR",
            "status": "created",
            "semester": get_current_semester(),
            "referral_code": data.referral_code.upper() if referral_valid else None,
            "referrer_sidhi_id": referrer_sidhi_id if referral_valid else None,
            "created_at": datetime.utcnow(),
            "expires_at": calculate_expiry_date()
        }
        
        await db.payments.insert_one(payment_doc)
        
        # Create pending referral record
        if referral_valid:
            await db.referrals.insert_one({
                "referrer_sidhi_id": referrer_sidhi_id,
                "referee_sidhi_id": sidhi_id,
                "referrer_tier_at_share": "free",
                "referee_tier_at_use": "free",
                "referral_code": data.referral_code.upper(),
                "order_id": razorpay_order["id"],
                "status": "pending",
                "discount_applied": discount,
                "created_at": datetime.utcnow()
            })
        
        return {
            "status": "success",
            "order_id": razorpay_order["id"],
            "amount": amount // 100,
            "original_amount": (amount + discount + credit_used) // 100,
            "discount": discount // 100,
            "credit_used": credit_used // 100,
            "currency": "INR",
            "key_id": RAZORPAY_KEY_ID,
            "tier": tier,
            "referral_applied": referral_valid,
            "notes": razorpay_order["notes"]
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/verify")
async def verify_payment(
    data: PaymentVerifyRequest,
    user: dict = Depends(verify_client_bound_request)
):
    """
    Verify payment and activate tier
    Protected endpoint - requires authentication
    
    FIXES:
    ✅ Idempotency - can be called multiple times safely
    ✅ Full payment validation (order_id, amount, currency)
    ✅ Status check before processing
    """
    try:
        sidhi_id = user.get("sub")
        
        # Get payment record
        payment_record = await db.payments.find_one({
            "razorpay_order_id": data.razorpay_order_id,
            "sidhi_id": sidhi_id
        })
        
        if not payment_record:
            raise HTTPException(
                status_code=404,
                detail="Payment record not found"
            )
        
        # ✅ FIX #1: IDEMPOTENCY - Check if already verified
        if payment_record.get("status") == "captured":
            print(f"⚠️  Payment {data.razorpay_payment_id} already verified")
            
            # Return existing quota
            quota = await db.quotas.find_one({"sidhi_id": sidhi_id})
            
            return {
                "status": "success",
                "message": "Payment already verified",
                "tier": payment_record["tier"],
                "quota": serialize_mongo_doc(quota),
                "already_verified": True
            }
        
        # Verify signature
        is_valid = verify_razorpay_signature(
            data.razorpay_order_id,
            data.razorpay_payment_id,
            data.razorpay_signature
        )
        
        if not is_valid:
            raise HTTPException(
                status_code=400,
                detail="Invalid payment signature"
            )
        
        # ✅ FIX #3: FULL PAYMENT VALIDATION
        try:
            payment_details = razorpay_client.payment.fetch(data.razorpay_payment_id)
            
            # Validate payment status
            if payment_details["status"] != "captured":
                raise HTTPException(
                    status_code=400,
                    detail=f"Payment not captured. Status: {payment_details['status']}"
                )
            
            # ✅ Validate order_id matches
            if payment_details.get("order_id") != data.razorpay_order_id:
                raise HTTPException(
                    status_code=400,
                    detail="Payment order_id mismatch. Possible fraud attempt."
                )
            
            # ✅ Validate amount matches expected
            expected_amount = payment_record["amount"]
            if payment_details.get("amount") != expected_amount:
                raise HTTPException(
                    status_code=400,
                    detail=f"Payment amount mismatch. Expected {expected_amount}, got {payment_details.get('amount')}"
                )
            
            # ✅ Validate currency
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
        
        # Update payment record (ATOMIC - only once due to unique index)
        await db.payments.update_one(
            {
                "razorpay_order_id": data.razorpay_order_id,
                "status": {"$ne": "captured"}  # Only update if not already captured
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
        
        # ===== PROCESS REFERRAL BONUS =====
        referral = await db.referrals.find_one({
            "order_id": data.razorpay_order_id,
            "status": "pending"
        })
        
        if referral:
            referrer_id = referral["referrer_sidhi_id"]
            
            # Double-check referrer is STILL on free tier
            referrer_quota = await db.quotas.find_one({"sidhi_id": referrer_id})
            
            if referrer_quota and referrer_quota.get("tier") == "free":
                # Grant referrer the bonus credit
                await db.referral_codes.update_one(
                    {"sidhi_id": referrer_id},
                    {
                        "$inc": {
                            "total_referrals": 1,
                            "pending_bonus": BASE_DISCOUNT
                        }
                    }
                )
                
                # Mark referral as completed
                await db.referrals.update_one(
                    {"order_id": data.razorpay_order_id},
                    {
                        "$set": {
                            "status": "completed",
                            "completed_at": datetime.utcnow(),
                            "referrer_bonus_granted": True
                        }
                    }
                )
                
                print(f"✅ Referral bonus ₹{BASE_DISCOUNT // 100} granted to {referrer_id}")
            else:
                # Referrer upgraded before payment completed - invalidate
                await db.referrals.update_one(
                    {"order_id": data.razorpay_order_id},
                    {"$set": {"status": "invalid", "reason": "referrer_upgraded"}}
                )
                print(f"⚠️ Referral invalid: {referrer_id} upgraded to paid tier")
        
        # Deduct used referral credit (if any was used in this payment)
        if payment_record.get("credit_used", 0) > 0:
            await db.referral_codes.update_one(
                {"sidhi_id": sidhi_id},
                {"$inc": {"pending_bonus": -payment_record["credit_used"]}}
            )
            print(f"✅ Deducted ₹{payment_record['credit_used'] // 100} credit from {sidhi_id}")
        # ===== END REFERRAL PROCESSING =====
        
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


@router.post("/webhook")
async def razorpay_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Razorpay webhook handler - NO AUTH (Razorpay signature verification)
    
    FIX #2: Webhook now ACTIVATES quota as authoritative fallback
    This ensures payment success even if frontend crashes
    """
    try:
        # Get webhook signature
        webhook_signature = request.headers.get("X-Razorpay-Signature")
        
        if not webhook_signature:
            raise HTTPException(status_code=400, detail="Missing signature")
        
        # Get raw body
        body = await request.body()
        
        # Verify webhook signature
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
            
            # Get payment record
            payment_record = await db.payments.find_one({
                "razorpay_order_id": order_id
            })
            
            if not payment_record:
                print(f"⚠️  Webhook: Payment record not found for order {order_id}")
                return {"status": "ok"}
            
            # Validate amount and currency
            if amount != payment_record["amount"]:
                print(f"❌ Webhook: Amount mismatch for {order_id}")
                return {"status": "error", "message": "Amount mismatch"}
            
            if currency != payment_record["currency"]:
                print(f"❌ Webhook: Currency mismatch for {order_id}")
                return {"status": "error", "message": "Currency mismatch"}
            
            # Update payment record (idempotent)
            await db.payments.update_one(
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
            
            # ✅ FIX #2: WEBHOOK ACTIVATES QUOTA (authoritative fallback)
            sidhi_id = payment_record["sidhi_id"]
            tier = payment_record["tier"]
            
            # Activate tier in background (won't block webhook response)
            background_tasks.add_task(
                activate_tier_idempotent,
                sidhi_id,
                tier,
                payment_id=payment_id,
                order_id=order_id
            )
            
            print(f"✅ Webhook: Payment captured - Order: {order_id}, Tier: {tier} activation scheduled")
        
        elif event == "payment.failed":
            order_id = payment_entity.get("order_id")
            
            await db.payments.update_one(
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
@router.get("/referral/stats")
async def get_referral_stats(user: dict = Depends(verify_client_bound_request)):
    """
    Get user's referral statistics
    """
    try:
        sidhi_id = user.get("sub")
        
        # Get referral code info
        code_doc = await db.referral_codes.find_one({"sidhi_id": sidhi_id})
        
        if not code_doc:
            return {
                "status": "success",
                "has_code": False,
                "message": "No referral code generated yet"
            }
        
        # Get referral history
        referrals = await db.referrals.find({
            "referrer_sidhi_id": sidhi_id
        }).to_list(length=None)
        
        return {
            "status": "success",
            "has_code": True,
            "referral_code": code_doc["code"],
            "total_referrals": code_doc.get("total_referrals", 0),
            "pending_bonus": code_doc.get("pending_bonus", 0) // 100,  # Convert to rupees
            "referrals": [
                {
                    "referee_id": r["referee_sidhi_id"],
                    "status": r["status"],
                    "discount_applied": r.get("discount_applied", 0) // 100,
                    "created_at": r["created_at"],
                    "completed_at": r.get("completed_at")
                }
                for r in referrals
            ]
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/status/{order_id}")
async def check_payment_status(
    order_id: str,
    user: dict = Depends(verify_client_bound_request)
):
    """
    Check payment status
    Protected endpoint - requires authentication
    """
    try:
        sidhi_id = user.get("sub")
        
        payment_record = await db.payments.find_one({
            "razorpay_order_id": order_id,
            "sidhi_id": sidhi_id
        })
        
        if not payment_record:
            raise HTTPException(status_code=404, detail="Payment not found")
        
        # Remove MongoDB _id from response
        payment_record.pop("_id", None)
        
        return {
            "status": "success",
            "payment": payment_record
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/check-expiry")
async def check_quota_expiry(user: dict = Depends(verify_client_bound_request)):
    """
    Check if user's quota has expired
    
    FIX #4: Expiry enforcement endpoint
    Call this before allowing quota usage
    """
    try:
        sidhi_id = user.get("sub")
        
        quota = await db.quotas.find_one({"sidhi_id": sidhi_id})
        
        if not quota:
            return {
                "status": "success",
                "has_quota": False,
                "message": "No active quota found"
            }
        
        # Check expiry
        expired = is_quota_expired(quota)
        
        if expired:
            # Auto-downgrade to free tier
            result = await activate_tier_idempotent(sidhi_id, "free")
            
            return {
                "status": "success",
                "has_quota": True,
                "expired": True,
                "tier": "free",
                "message": "Quota expired. Downgraded to free tier.",
                "quota": result["quota"]
            }
        
        return {
            "status": "success",
            "has_quota": True,
            "expired": False,
            "tier": quota["tier"],
            "expires_at": quota.get("meta", {}).get("expires_at"),
            "quota": serialize_mongo_doc(quota)
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
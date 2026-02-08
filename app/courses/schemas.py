"""
UPDATED MongoDB Collection Schemas
File: app/courses/schemas.py

ADDED:
✅ Course pricing fields
✅ Course purchases collection
✅ Tier payments collection (separated from course purchases)
✅ Updated indexes for security
"""

# ==================== COURSES (UPDATED WITH PRICING) ====================

COURSES_SCHEMA = {
    "validator": {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["course_id", "title", "course_type", "domain", "status", "creator_id"],
            "properties": {
                "course_id": {"bsonType": "string"},
                "title": {"bsonType": "string"},
                "description": {"bsonType": "string"},
                "course_type": {"enum": ["OFFICIAL", "CREATOR"]},
                "domain": {"enum": ["SOFTWARE", "HARDWARE"]},
                "status": {"enum": ["DRAFT", "PUBLISHED", "ACTIVE", "ARCHIVED"]},
                "creator_id": {"bsonType": "string"},
                "instructor_id": {"bsonType": ["string", "null"]},
                "thumbnail_url": {"bsonType": ["string", "null"]},
                "tags": {"bsonType": "array"},
                "external_resources": {"bsonType": "array"},
                
                # 🆕 PRICING FIELDS
                "pricing": {
                    "bsonType": ["object", "null"],
                    "properties": {
                        "is_free": {"bsonType": "bool"},
                        "price": {"bsonType": "int"},  # in paise
                        "original_price": {"bsonType": "int"},  # in paise
                        "currency": {"bsonType": "string"},
                        "tier_access": {"bsonType": "array"},  # ["hero", "dominator"]
                        "discount_percentage": {"bsonType": "int"}
                    }
                },
                
                # 🆕 PURCHASE STATS
                "purchase_stats": {
                    "bsonType": ["object", "null"],
                    "properties": {
                        "total_purchases": {"bsonType": "int"},
                        "revenue_generated": {"bsonType": "int"}  # in paise
                    }
                },
                
                "created_at": {"bsonType": "date"},
                "updated_at": {"bsonType": "date"},
                "published_at": {"bsonType": ["date", "null"]},
                "stats": {
                    "bsonType": "object",
                    "properties": {
                        "enrollments": {"bsonType": "int"},
                        "completions": {"bsonType": "int"},
                        "avg_rating": {"bsonType": "double"}
                    }
                }
            }
        }
    }
}


# ==================== COURSE PURCHASES (NEW) ====================

COURSE_PURCHASES_SCHEMA = {
    "validator": {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["purchase_id", "razorpay_order_id", "user_id", "course_id", "amount", "status"],
            "properties": {
                "purchase_id": {"bsonType": "string"},
                "razorpay_order_id": {"bsonType": "string"},
                "razorpay_payment_id": {"bsonType": ["string", "null"]},
                "razorpay_signature": {"bsonType": ["string", "null"]},
                
                "user_id": {"bsonType": "string"},  # sidhi_id
                "course_id": {"bsonType": "string"},
                "course_title": {"bsonType": "string"},
                
                "purchase_type": {"bsonType": "string"},  # "course_purchase"
                "amount": {"bsonType": "int"},  # in paise
                "currency": {"bsonType": "string"},
                
                "status": {"enum": ["created", "captured", "failed"]},
                "access_granted": {"bsonType": "bool"},
                
                "created_at": {"bsonType": "date"},
                "purchased_at": {"bsonType": ["date", "null"]},
                "verified_at": {"bsonType": ["date", "null"]},
                "verified_via": {"bsonType": ["string", "null"]},  # "webhook" or "frontend"
                
                "webhook_verified": {"bsonType": ["bool", "null"]},
                "webhook_received_at": {"bsonType": ["date", "null"]},
                
                "failed_at": {"bsonType": ["date", "null"]},
                "failure_reason": {"bsonType": ["string", "null"]},
                
                "expires_at": {"bsonType": ["date", "null"]},  # null = lifetime access
                "access_revoked": {"bsonType": ["bool", "null"]},
                "revoked_at": {"bsonType": ["date", "null"]},
                "revoke_reason": {"bsonType": ["string", "null"]}
            }
        }
    }
}


# ==================== TIER PAYMENTS (NEW - SEPARATED) ====================

TIER_PAYMENTS_SCHEMA = {
    "validator": {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["razorpay_order_id", "sidhi_id", "tier", "amount", "status"],
            "properties": {
                "razorpay_order_id": {"bsonType": "string"},
                "razorpay_payment_id": {"bsonType": ["string", "null"]},
                "razorpay_signature": {"bsonType": ["string", "null"]},
                
                "sidhi_id": {"bsonType": "string"},
                "purchase_type": {"bsonType": "string"},  # "tier_subscription"
                "tier": {"enum": ["hero", "dominator"]},
                
                "amount": {"bsonType": "int"},  # in paise
                "currency": {"bsonType": "string"},
                
                "status": {"enum": ["created", "captured", "failed"]},
                "semester": {"bsonType": "string"},
                
                "created_at": {"bsonType": "date"},
                "verified_at": {"bsonType": ["date", "null"]},
                "verified_via": {"bsonType": ["string", "null"]},
                
                "webhook_verified": {"bsonType": ["bool", "null"]},
                "webhook_received_at": {"bsonType": ["date", "null"]},
                
                "failed_at": {"bsonType": ["date", "null"]},
                "failure_reason": {"bsonType": ["string", "null"]},
                
                "expires_at": {"bsonType": ["date", "null"]}
            }
        }
    }
}


# ==================== QUOTAS (EXISTING - NO CHANGES) ====================

QUOTAS_SCHEMA = {
    "validator": {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["sidhi_id", "tier", "semester"],
            "properties": {
                "sidhi_id": {"bsonType": "string"},
                "semester": {"bsonType": "string"},
                "tier": {"enum": ["free", "hero", "dominator"]},
                "base": {"bsonType": "object"},
                "used": {"bsonType": "object"},
                "addons": {"bsonType": "object"},
                "meta": {"bsonType": "object"}
            }
        }
    }
}


# ==================== COURSE QUESTIONS (EXISTING - NO CHANGES) ====================

COURSE_QUESTIONS_SCHEMA = {
    "validator": {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["question_id", "course_id", "title", "difficulty", "language"],
            "properties": {
                "question_id": {"bsonType": "string"},
                "course_id": {"bsonType": "string"},
                "module_id": {"bsonType": ["string", "null"]},
                "title": {"bsonType": "string"},
                "description": {"bsonType": "string"},
                "difficulty": {"enum": ["easy", "medium", "hard"]},
                "language": {"enum": ["c", "cpp", "python", "verilog", "vhdl", "systemverilog", "java", "javascript"]},
                "problem_type": {"enum": ["coding", "mcq", "theory"]},
                "test_cases": {"bsonType": "array"},
                "time_limit": {"bsonType": "double"},
                "memory_limit": {"bsonType": "int"},
                "points": {"bsonType": "int"},
                "created_at": {"bsonType": "date"},
                "is_active": {"bsonType": "bool"}
            }
        }
    }
}


# ==================== ENROLLMENTS (EXISTING - NO CHANGES) ====================

COURSE_ENROLLMENTS_SCHEMA = {
    "validator": {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["enrollment_id", "course_id", "user_id", "sidhi_id", "certificate_id"],
            "properties": {
                "enrollment_id": {"bsonType": "string"},
                "course_id": {"bsonType": "string"},
                "user_id": {"bsonType": "string"},
                "sidhi_id": {"bsonType": "string"},
                "certificate_id": {"bsonType": "string"},
                "enrolled_at": {"bsonType": "date"},
                "progress": {"bsonType": "double"},
                "current_league": {"enum": ["BRONZE", "SILVER", "GOLD", "PLATINUM", "DIAMOND", "MYTHIC", "LEGEND"]},
                "league_points": {"bsonType": "int"},
                "solved_questions": {"bsonType": "array"},
                "avg_efficiency": {"bsonType": "double"},
                "is_active": {"bsonType": "bool"}
            }
        }
    }
}


# ==================== SUBMISSIONS (EXISTING - NO CHANGES) ====================

COURSE_SUBMISSIONS_SCHEMA = {
    "validator": {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["submission_id", "course_id", "question_id", "user_id", "code", "language"],
            "properties": {
                "submission_id": {"bsonType": "string"},
                "course_id": {"bsonType": "string"},
                "question_id": {"bsonType": "string"},
                "user_id": {"bsonType": "string"},
                "code": {"bsonType": "string"},
                "language": {"bsonType": "string"},
                "status": {"enum": ["queued", "processing", "completed", "failed"]},
                "verdict": {"bsonType": ["string", "null"]},
                "result": {"bsonType": ["object", "null"]},
                "score": {"bsonType": ["double", "null"]},
                "submitted_at": {"bsonType": "date"},
                "graded_at": {"bsonType": ["date", "null"]}
            }
        }
    }
}


# ==================== INDEXES (UPDATED) ====================

INDEXES = {
    "courses": [
        {"keys": [("course_id", 1)], "unique": True},
        {"keys": [("course_type", 1), ("status", 1)]},
        {"keys": [("creator_id", 1)]},
        {"keys": [("domain", 1)]},
        {"keys": [("pricing.is_free", 1)]},  # 🆕 For filtering free courses
        {"keys": [("pricing.price", 1)]}     # 🆕 For price-based queries
    ],
    
    "course_purchases": [
        {"keys": [("purchase_id", 1)], "unique": True},
        {"keys": [("razorpay_order_id", 1)], "unique": True},  # 🔒 IDEMPOTENCY
        {"keys": [("user_id", 1), ("course_id", 1)]},
        {"keys": [("user_id", 1), ("status", 1)]},
        {"keys": [("course_id", 1), ("status", 1)]},
        {"keys": [("status", 1)]},
        {"keys": [("purchased_at", -1)]}
    ],
    
    "tier_payments": [
        {"keys": [("razorpay_order_id", 1)], "unique": True},  # 🔒 IDEMPOTENCY
        {"keys": [("sidhi_id", 1), ("tier", 1)]},
        {"keys": [("sidhi_id", 1), ("status", 1)]},
        {"keys": [("status", 1)]},
        {"keys": [("verified_at", -1)]}
    ],
    
    "quotas": [
        {"keys": [("sidhi_id", 1)], "unique": True},  # One quota per user
        {"keys": [("tier", 1)]},
        {"keys": [("semester", 1)]}
    ],
    
    "course_questions": [
        {"keys": [("question_id", 1)], "unique": True},
        {"keys": [("course_id", 1), ("is_active", 1)]},
        {"keys": [("difficulty", 1)]},
        {"keys": [("language", 1)]}
    ],
    
    "course_enrollments": [
        {"keys": [("enrollment_id", 1)], "unique": True},
        {"keys": [("user_id", 1), ("course_id", 1)], "unique": True},
        {"keys": [("certificate_id", 1)], "unique": True},
        {"keys": [("course_id", 1), ("league_points", -1)]},
        {"keys": [("current_league", 1)]}
    ],
    
    "course_submissions": [
        {"keys": [("submission_id", 1)], "unique": True},
        {"keys": [("user_id", 1), ("course_id", 1)]},
        {"keys": [("question_id", 1), ("user_id", 1)]},
        {"keys": [("submitted_at", -1)]}
    ],
    
    "alumni_board": [
        {"keys": [("user_id", 1)], "unique": True},
        {"keys": [("final_points", -1), ("graduation_date", 1)]},
        {"keys": [("final_league", 1)]}
    ],
    
    "user_achievements": [
        {"keys": [("achievement_id", 1)], "unique": True},
        {"keys": [("user_id", 1), ("course_id", 1)]},
        {"keys": [("badge_id", 1)]}
    ],
    
    "user_sample_progress": [
        {"keys": [("user_id", 1)], "unique": True}
    ]
}


# ==================== COLLECTION CREATION ====================

async def create_collections_with_validation(db):
    """Create all collections with schema validation"""
    
    schemas = {
        "courses": COURSES_SCHEMA,
        "course_purchases": COURSE_PURCHASES_SCHEMA,
        "tier_payments": TIER_PAYMENTS_SCHEMA,
        "quotas": QUOTAS_SCHEMA,
        "course_questions": COURSE_QUESTIONS_SCHEMA,
        "course_enrollments": COURSE_ENROLLMENTS_SCHEMA,
        "course_submissions": COURSE_SUBMISSIONS_SCHEMA,
        "alumni_board": ALUMNI_BOARD_SCHEMA,
        "user_achievements": USER_ACHIEVEMENTS_SCHEMA,
        "user_sample_progress": USER_SAMPLE_PROGRESS_SCHEMA
    }
    
    existing_collections = await db.list_collection_names()
    
    for collection_name, schema in schemas.items():
        if collection_name not in existing_collections:
            await db.create_collection(collection_name, **schema)
            print(f"✅ Created collection: {collection_name}")
        else:
            # Update validation rules
            try:
                await db.command({
                    "collMod": collection_name,
                    **schema
                })
                print(f"✅ Updated validation: {collection_name}")
            except Exception as e:
                print(f"⚠️  Could not update {collection_name}: {e}")


async def create_all_indexes(db):
    """Create all indexes for performance and security"""
    
    for collection_name, indexes in INDEXES.items():
        collection = db[collection_name]
        for index in indexes:
            try:
                await collection.create_index(
                    index["keys"],
                    unique=index.get("unique", False)
                )
                print(f"✅ Created index on {collection_name}: {index['keys']}")
            except Exception as e:
                print(f"⚠️  Index creation failed for {collection_name}: {e}")


# ==================== EXISTING SCHEMAS (NO CHANGES) ====================

ALUMNI_BOARD_SCHEMA = {
    "validator": {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["user_id", "sidhi_id", "final_league", "final_points"],
            "properties": {
                "user_id": {"bsonType": "string"},
                "sidhi_id": {"bsonType": "string"},
                "final_league": {"enum": ["BRONZE", "SILVER", "GOLD", "PLATINUM", "DIAMOND", "MYTHIC", "LEGEND"]},
                "final_points": {"bsonType": "int"},
                "total_problems_solved": {"bsonType": "int"},
                "graduation_date": {"bsonType": "date"},
                "peak_global_rank": {"bsonType": ["int", "null"]},
                "is_alumni": {"bsonType": "bool"}
            }
        }
    }
}

USER_ACHIEVEMENTS_SCHEMA = {
    "validator": {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["achievement_id", "user_id", "badge_id"],
            "properties": {
                "achievement_id": {"bsonType": "string"},
                "user_id": {"bsonType": "string"},
                "course_id": {"bsonType": ["string", "null"]},
                "badge_id": {"bsonType": "string"},
                "title": {"bsonType": "string"},
                "description": {"bsonType": "string"},
                "icon": {"bsonType": "string"},
                "unlocked_at": {"bsonType": "date"}
            }
        }
    }
}

USER_SAMPLE_PROGRESS_SCHEMA = {
    "validator": {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["user_id"],
            "properties": {
                "user_id": {"bsonType": "string"},
                "read_samples": {"bsonType": "array"}
            }
        }
    }
}
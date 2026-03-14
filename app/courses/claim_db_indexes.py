"""
CLAIM SYSTEM - MongoDB Index Setup
File: app/courses/claim_db_indexes.py

Call create_claim_indexes(db) inside startup_course_system() in app.py
"""

async def create_claim_indexes(db):
    try:
        # ── course_claims (auto-claims via college name) ─────────────────────
        await db.course_claims.create_index([("user_id", 1), ("status", 1)])
        await db.course_claims.create_index([("claim_id", 1)], unique=True, sparse=True)
        await db.course_claims.create_index([("course_id", 1), ("status", 1)])
        await db.course_claims.create_index([("created_at", -1)])

        # ── course_claim_access (the access grant — queried on every enroll) ─
        await db.course_claim_access.create_index(
            [("user_id", 1), ("course_id", 1)], unique=True
        )
        await db.course_claim_access.create_index([("user_id", 1), ("access_granted", 1)])
        await db.course_claim_access.create_index([("sidhi_id", 1)])

        # ── course_access_requests (manual requests — admin reviews these) ───
        await db.course_access_requests.create_index([("user_id", 1), ("status", 1)])
        await db.course_access_requests.create_index([("request_id", 1)], unique=True)
        await db.course_access_requests.create_index([("course_id", 1), ("status", 1)])
        await db.course_access_requests.create_index([("submitted_at", -1)])

        print("✅ Claim system indexes created")

    except Exception as e:
        print(f"⚠️  Claim index warning: {e}")
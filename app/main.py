from datetime import datetime
from fastapi import FastAPI, BackgroundTasks, Request, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from app.ai.router import router as ai_router
from app.chat.router import router as chat_router
from app.stream.router import router as stream_router
from app.api.auth_proxy import router as auth_router
from app.lum_cloud.sync_server import commit_to_github, setup_repo 
from nacl.signing import VerifyKey
from app.ai.payment_router import router as payment_router, create_indexes
import binascii
from app.lum_cloud.sync_server import LOCAL_REPO_DIR
import os
from app.ai.client_bound_guard import verify_client_bound_request
from fastapi import Query
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel
from app.ai.quota_manager import get_user_quotas, get_user_history, log_cloud_push, get_cloud_history, create_order, get_user_orders
from app.ai.bot_services import generate_bot_response
from app.admin.router import router as admin_router
from app.admin.hardened_firebase_auth import init_auth
from app.ai.training_router import router as training_router
from app.ai.coding_practice import router as coding_router
from app.plagiarism.plagiarism_router import router as plag_router
from app.teachers.teacher_router import router as teacher_router
from app.students.student_router import router as student_router
from app.teachers.database_setup import create_teacher_indexes, create_student_indexes



app = FastAPI(title="Lumetrics AI Engine")
ALLOWED_EXTENSIONS = {'.py', '.ipynb', '.c', '.cpp', '.h', '.java', '.js'}

# MongoDB Configuration
MONGO_URL = os.getenv("MONGO_URL")
VERSION = os.getenv("VERSION")
client = AsyncIOMotorClient(MONGO_URL)
db = client.lumetrics_db 


class UserDetailCreate(BaseModel):
    username: str
    sidhi_id: str
    user_id: str
    college: str
    department: str
    starting_year: str
    is_admin: bool = False
    is_teacher: bool = False
    degree: str
    role: str = "student"
    email_id: str


@app.on_event("startup")
async def startup_event():
    init_auth()
    setup_repo()
    await create_indexes()
    await create_teacher_indexes()  # ✅ ADD THIS
    await create_student_indexes()  # ✅ ADD THIS


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"]  # Added for Razorpay compatibility
)


async def verify_signature(
    request: Request,
    x_client_public_key: str = Header(None),
    x_client_signature: str = Header(None),
    x_client_timestamp: str = Header(None)
):
    if not x_client_public_key or not x_client_signature or not x_client_timestamp:
        raise HTTPException(status_code=401, detail="Missing auth headers")
    try:
        verify_key = VerifyKey(binascii.unhexlify(x_client_public_key))
        message = f"{x_client_timestamp}:{request.url.path}".encode()
        verify_key.verify(message, binascii.unhexlify(x_client_signature))
        return x_client_public_key
    except:
        raise HTTPException(status_code=401, detail="Invalid signature")


# ==================== ROUTER REGISTRATION ====================
app.include_router(ai_router, prefix="/ai")
app.include_router(chat_router)
app.include_router(auth_router)
app.include_router(admin_router,prefix="/admin")
app.include_router(stream_router)
app.include_router(plag_router, prefix="/plagiarism")
app.include_router(coding_router, prefix="/coding")
app.include_router(training_router, prefix="/training")
app.include_router(payment_router, prefix="/payment")
app.include_router(teacher_router)  # ✅ ADD THIS
app.include_router(student_router)  # ✅ ADD THIS
# ============================================================


class ApprovalRequest(BaseModel):
    sid_id: str
    college_roll: str
    username: str 


@app.get("/sync/cloudaccess/{sid_id}")
async def cloud_access(sid_id: str, user: str = Depends(verify_client_bound_request)):
    try:
        user_record = await db.users.find_one({"sid_id": sid_id})
        
        if not user_record:
            return {
                "status": "error",
                "sidhilynx_id": sid_id,
                "cloud_exists": False,
                "message": "User not registered"
            }

        student_folder = os.path.join(LOCAL_REPO_DIR, "vault", f"user_{sid_id}")
        files_on_disk = os.path.exists(student_folder) and any(os.scandir(student_folder))
        
        return {
            "status": "success",
            "sidhilynx_id": sid_id,
            "cloud_exists": files_on_disk,
            "user_data": {
                "college_roll": user_record.get("college_roll"),
                "name": user_record.get("name"),
                "is_active": user_record.get("is_active")
            },
            "message": "Backup available" if files_on_disk else "No backups found",
            "created_at": user_record.get("created_at") 
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/sync/cloudapprove")
async def cloud_approve(data: ApprovalRequest, user: str = Depends(verify_client_bound_request)):
    try:
        # ✅ SANITIZE ALL INPUTS
        sid_id = data.sid_id.strip()
        college_roll = data.college_roll.strip()
        username = data.username.strip()
        
        result = await db.users.update_one(
            {"sid_id": sid_id},
            {"$set": {
                "sid_id": sid_id,
                "college_roll": college_roll,
                "name": username,
                "is_active": True,
                "created_at": datetime.utcnow()
            }},
            upsert=True
        )
        
        return {
            "status": "success", 
            "message": f"User {college_roll} approved and linked to {sid_id}"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))




@app.post("/sync/push")
async def student_push(
    request: Request, 
    background_tasks: BackgroundTasks,
    authenticated_pk: str = Depends(verify_signature) 
):
    try:
        data = await request.json()
        sid_id = data.get("sidhilynx_id", "").strip()  # ✅ ADD .strip()
        roll_no = data.get("college_roll", "").strip()  # ✅ ADD .strip()
        files = data.get("files", {})
        
        # Payload size validation
        total_payload_size = sum(len(content.encode('utf-8')) for content in files.values())
        if total_payload_size > 2 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="Payload too large. Max 2MB allowed.")

        # Extension security check
        for filename in files.keys():
            _, ext = os.path.splitext(filename)
            if ext.lower() not in ALLOWED_EXTENSIONS:
                raise HTTPException(status_code=400, detail=f"File type {ext} not allowed.")
            
        # Identity verification
        user_record = await db.users.find_one({"college_roll": roll_no})
        
        if not user_record or user_record.get("sid_id", "").strip() != sid_id:  # ✅ ADD .strip()
            raise HTTPException(status_code=403, detail="Identity Mismatch: Terminal user not linked to Sidhi ID")

        if not sid_id or not files:
            return {"status": "error", "message": "Missing payload"}

        # Background tasks
        background_tasks.add_task(log_cloud_push, sid_id)
        background_tasks.add_task(commit_to_github, sid_id, files)
        
        return {"status": "success", "message": "Cloud sync initiated"}
    except HTTPException:
        raise 
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/sync/cloudview")
async def cloud_view(
    sid_id: str = Query(...),
    user: str = Depends(verify_client_bound_request)
):
    try:
        student_folder = os.path.join(LOCAL_REPO_DIR, "vault", f"user_{sid_id}")

        if not os.path.exists(student_folder):
            return {"status": "success", "files": {}, "message": "Vault is currently empty"}

        vault_contents = {}
        for root, _, files in os.walk(student_folder):
            for filename in files:
                full_path = os.path.join(root, filename)
                relative_path = os.path.relpath(full_path, student_folder)
                
                try:
                    with open(full_path, "r", encoding="utf-8") as f:
                        vault_contents[relative_path] = f.read()
                except Exception:
                    continue

        return {
            "status": "success",
            "sidhilynx_id": sid_id,
            "files": vault_contents
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/version")
def get_version():
    return {"version": VERSION or "unknown", "status": "stable"}
@app.get("/me/payments/history")
async def fetch_my_payments(user: dict = Depends(verify_client_bound_request)):
    try:
        sidhi_id = user.get("sub")
        
        # Fetch payments for the user, sorted by created_at descending (newest first)
        payments_cursor = db.payments.find(
            {"sidhi_id": sidhi_id},
            {
                "_id": 0,
                "created_at": 1,
                "expires_at": 1,
                "status": 1,
                "amount": 1,
                "sidhi_id": 1,
                "tier": 1
            }
        ).sort("created_at", -1)
        
        payments = await payments_cursor.to_list(length=None)
        
        if not payments:
            return {
                "status": "success",
                "sidhi_id": sidhi_id,
                "payments": [],
                "message": "No payment history found"
            }
        
        return {
            "status": "success",
            "sidhi_id": sidhi_id,
            "payments": payments,
            "count": len(payments)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/user/check/{sidhi_user_id}")
async def check_user_exists(sidhi_user_id: str, user: str = Depends(verify_client_bound_request)):
    try:
        user_record = await db.users_profile.find_one({"user_id": sidhi_user_id})
        
        if user_record:
            return {
                "status": "success",
                "exists": True,
                "message": "User found in database",
                "data": {
                    "username": user_record.get("username"),
                    "user_id": user_record.get("user_id"),
                    "email_id": user_record.get("email_id"),
                    "college": user_record.get("college"),
                    "department": user_record.get("department"),
                    "starting_year": user_record.get("starting_year"),
                    "is_admin": user_record.get("is_admin"),
                    "is_teacher": user_record.get("is_teacher", False),
                    "role": user_record.get("role", "student"),
                    "degree": user_record.get("degree"),
                    "sidhi_id": user_record.get("sidhi_id")
                }
            }
        
        return {
            "status": "success",
            "exists": False,
            "message": "User not found"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/user/register")
async def register_user_details(data: UserDetailCreate, user: str = Depends(verify_client_bound_request)):
    try:
        existing_user = await db.users_profile.find_one({"user_id": data.user_id})
        
        if existing_user:
            raise HTTPException(
                status_code=409, 
                detail="User already exists. Data entry not allowed."
            )

        user_data = data.model_dump()
        
        if user_data.get("is_teacher"):
            user_data["role"] = "teacher"
        else:
            user_data["role"] = "student"

        await db.users_profile.insert_one(user_data)

        return {
            "status": "success",
            "message": f"User details successfully registered as {user_data['role']}"
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
  

@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/me/quotas")
async def fetch_my_quotas(user: dict = Depends(verify_client_bound_request)):
    try:
        sidhi_id = user.get("sub")
        data = await get_user_quotas(sidhi_id)
        if not data:
            raise HTTPException(status_code=404, detail="Quota record not found")
        return data
    except Exception as e:
        if isinstance(e, HTTPException): 
            raise e
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/me/history")
async def fetch_my_history(user: dict = Depends(verify_client_bound_request)):
    try:
        sidhi_id = user.get("sub")
        data = await get_user_history(sidhi_id)
        if not data:
            return {"sidhi_id": sidhi_id, "logs": {}, "message": "No history found"}
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    
@app.get("/me/cloud-history")
async def fetch_cloud_history(
    user: dict = Depends(verify_client_bound_request),
    sidhi_id: str = Query(..., description="The Sidhi ID of the user example@sidhilynx.id")
):
    try:
        data = await get_cloud_history(sidhi_id)
        if not data:
            return {"sidhi_id": sidhi_id, "pushes": [], "message": "No push history found"}
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    

class OrderSummaryRequest(BaseModel):
    folder_name: str
    questions: list
    flowchart_selected_questions_index: list = []
    is_algo_needed: bool = False
    is_code_needed: bool = False
    is_output_needed: bool = False
    is_flowchart_needed: bool = False
    email_id: str
    client_id: str


@app.post("/orders/place")
async def place_order(
    data: OrderSummaryRequest, 
    user: dict = Depends(verify_client_bound_request)
):
    try:
        user_id = user.get("sub")
        order = await create_order(user_id, data.dict())
        return {"status": "success", "order": order}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/orders/history")
async def fetch_order_history(user: dict = Depends(verify_client_bound_request)):
    try:
        user_id = user.get("sub")
        history = await get_user_orders(user_id)
        return {"status": "success", "orders": history}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class HelpRequest(BaseModel):
    issue: str
    email_id: str


@app.post("/support/help")
async def submit_help_request(
    data: HelpRequest,
    user: dict = Depends(verify_client_bound_request)
):
    try:
        sidhi_id = user.get("sub")
        
        # Create help ticket
        help_ticket = {
            "sidhi_id": sidhi_id,
            "email_id": data.email_id,
            "issue": data.issue,
            "status": "pending",  # pending, in_progress, resolved, closed
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
            "admin_response": None,
            "resolved_at": None
        }
        
        result = await db.help_tickets.insert_one(help_ticket)
        
        return {
            "status": "success",
            "message": "Help request submitted successfully",
            "ticket_id": str(result.inserted_id)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/support/my-tickets")
async def get_my_help_tickets(user: dict = Depends(verify_client_bound_request)):
    try:
        sidhi_id = user.get("sub")
        
        tickets_cursor = db.help_tickets.find(
            {"sidhi_id": sidhi_id},
            {"_id": 0}
        ).sort("created_at", -1)
        
        tickets = await tickets_cursor.to_list(length=None)
        
        return {
            "status": "success",
            "sidhi_id": sidhi_id,
            "tickets": tickets,
            "count": len(tickets)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
@app.get("/notifications")
async def get_notifications(
    user: dict = Depends(verify_client_bound_request),
    unread_only: bool = Query(False, description="Fetch only unread notifications")
):
    try:
        sidhi_id = user.get("sub")
        
        # Build query: global notifications OR user-specific notifications
        query = {
            "$or": [
                {"target_type": "all"},  # Global notifications
                {"target_type": "specific", "target_users": sidhi_id}  # User-specific
            ]
        }
        
        # Add unread filter if requested
        if unread_only:
            query["read_by"] = {"$ne": sidhi_id}
        
        notifications_cursor = db.notifications.find(query).sort("created_at", -1)
        notifications = await notifications_cursor.to_list(length=None)
        
        # Format response: add read status for each notification
        formatted_notifications = []
        for notif in notifications:
            formatted_notifications.append({
                "notification_id": str(notif.get("_id")),
                "title": notif.get("title"),
                "message": notif.get("message"),
                "type": notif.get("type"),  # info, warning, success, error, announcement
                "priority": notif.get("priority"),  # low, medium, high, urgent
                "target_type": notif.get("target_type"),
                "created_at": notif.get("created_at"),
                "is_read": sidhi_id in notif.get("read_by", []),
                "action_url": notif.get("action_url"),  # Optional link for CTA
                "expires_at": notif.get("expires_at")  # Optional expiry
            })
        
        return {
            "status": "success",
            "notifications": formatted_notifications,
            "count": len(formatted_notifications),
            "unread_count": sum(1 for n in formatted_notifications if not n["is_read"])
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/notifications/{notification_id}/mark-read")
async def mark_notification_read(
    notification_id: str,
    user: dict = Depends(verify_client_bound_request)
):
    try:
        sidhi_id = user.get("sub")
        from bson import ObjectId
        
        # Add user to read_by array
        result = await db.notifications.update_one(
            {"_id": ObjectId(notification_id)},
            {"$addToSet": {"read_by": sidhi_id}}
        )
        
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Notification not found")
        
        return {
            "status": "success",
            "message": "Notification marked as read"
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/notifications/mark-all-read")
async def mark_all_notifications_read(user: dict = Depends(verify_client_bound_request)):
    try:
        sidhi_id = user.get("sub")
        
        # Mark all accessible notifications as read
        result = await db.notifications.update_many(
            {
                "$or": [
                    {"target_type": "all"},
                    {"target_type": "specific", "target_users": sidhi_id}
                ],
                "read_by": {"$ne": sidhi_id}
            },
            {"$addToSet": {"read_by": sidhi_id}}
        )
        
        return {
            "status": "success",
            "message": f"Marked {result.modified_count} notifications as read"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
from pydantic import BaseModel
from typing import Dict
from datetime import datetime
from fastapi import HTTPException, Depends

# Import the prompt generation function (add this at the top of your file)
# Make sure generate_coding_style_prompt is imported or defined in main.py
def generate_coding_style_prompt(answers):
    """
    Generate a personalized coding style prompt based on user answers.
    
    Args:
        answers (dict): Dictionary with keys q1-q20, values 'A', 'B', 'C', 'D', or 'E'
    
    Returns:
        str: Complete prompt string to send to an AI
    """
    
    prompt = "You are a code generator. Write code following these specific style guidelines:\n\n"
    
    # Q1: Problem-solving approach
    if answers['q1'] == 'A':
        prompt += "- Break problems into small, incremental functions. Use a step-by-step approach.\n"
    elif answers['q1'] == 'B':
        prompt += "- Start with high-level architecture comments, then implement details.\n"
    elif answers['q1'] == 'C':
        prompt += "- Use an iterative approach with multiple solution attempts. Include TODO comments for alternative approaches.\n"
    elif answers['q1'] == 'D':
        prompt += "- Include references to documentation and similar examples in comments.\n"
    
    # Q2: Documentation style
    if answers['q2'] == 'A':
        prompt += "- Add detailed comments explaining every step and decision.\n"
    elif answers['q2'] == 'B':
        prompt += "- Use minimal, concise comments only for complex logic.\n"
    elif answers['q2'] == 'C':
        prompt += "- Include ASCII diagrams or visual representations in comments where helpful.\n"
    elif answers['q2'] == 'D':
        prompt += "- Organize code with section headers and structured comment blocks.\n"
    elif answers['q2'] == 'E':
        prompt += "- NO COMMENTS AT ALL. Write self-documenting code with clear variable and function names only.\n"
    
    # Q3: Naming convention
    if answers['q3'] == 'A':
        prompt += "- Use very descriptive, long variable names (e.g., user_input_validation_result).\n"
    elif answers['q3'] == 'B':
        prompt += "- Use short, abbreviated names (e.g., usr_inp, val_res).\n"
    elif answers['q3'] == 'C':
        prompt += "- Include contextual prefixes in names (e.g., temp_data_2026, final_result_v2).\n"
    elif answers['q3'] == 'D':
        prompt += "- Use simple, generic names (e.g., data, result, value, temp).\n"
    
    # Q4: Code organization
    if answers['q4'] == 'A':
        prompt += "- Group related functions into clearly labeled sections with comment headers.\n"
    elif answers['q4'] == 'B':
        prompt += "- Organize functions chronologically or by when they're called.\n"
    elif answers['q4'] == 'C':
        prompt += "- Keep related helper functions near where they're used.\n"
    elif answers['q4'] == 'D':
        prompt += "- Keep all code in a simple, linear structure without heavy organization.\n"
    
    # Q5: Code precision/detail
    if answers['q5'] == 'A':
        prompt += "- Include explicit parameter validation, type checking, and edge case handling.\n"
    elif answers['q5'] == 'B':
        prompt += "- Keep code straightforward without excessive validation.\n"
    elif answers['q5'] == 'C':
        prompt += "- Add meaningful examples in comments to illustrate usage.\n"
    elif answers['q5'] == 'D':
        prompt += "- Write minimal code assuming standard use cases.\n"
    
    # Q6: Explanation style
    if answers['q6'] == 'A':
        prompt += "- Add step-by-step inline comments for every operation.\n"
    elif answers['q6'] == 'B':
        prompt += "- Only comment on the main logic flow, skip obvious details.\n"
    elif answers['q6'] == 'C':
        prompt += "- Use analogies and real-world examples in comments.\n"
    elif answers['q6'] == 'D':
        prompt += "- Assume code is self-explanatory, minimal comments.\n"
    
    # Q7: Error checking approach
    if answers['q7'] == 'A':
        prompt += "- Include comprehensive error handling with try-except blocks and validation at every step.\n"
    elif answers['q7'] == 'B':
        prompt += "- Add basic error handling only for critical operations.\n"
    elif answers['q7'] == 'C':
        prompt += "- Use defensive programming with fallback values and conditional checks.\n"
    elif answers['q7'] == 'D':
        prompt += "- Minimal error handling, trust inputs are correct.\n"
    
    # Q8: Debugging/fixing approach
    if answers['q8'] == 'A':
        prompt += "- Add detailed logging statements and debug comments explaining potential issues.\n"
    elif answers['q8'] == 'B':
        prompt += "- Keep code clean without debug statements, rely on clean logic.\n"
    elif answers['q8'] == 'C':
        prompt += "- Include multiple solution paths or alternative implementations commented out.\n"
    elif answers['q8'] == 'D':
        prompt += "- Write fresh, clean implementations without legacy code or comments.\n"
    
    # Q9: Planning vs doing
    if answers['q9'] == 'A':
        prompt += "- Start with a detailed pseudocode outline before any implementation.\n"
    elif answers['q9'] == 'B':
        prompt += "- Write working code first, refactor and optimize later.\n"
    elif answers['q9'] == 'C':
        prompt += "- Mix high-level planning comments with immediate implementation.\n"
    elif answers['q9'] == 'D':
        prompt += "- Dive straight into implementation without planning comments.\n"
    
    # Q10: Learning style
    if answers['q10'] == 'A':
        prompt += "- Include complete documentation references and usage instructions.\n"
    elif answers['q10'] == 'B':
        prompt += "- Write experimental code with various approaches to test.\n"
    elif answers['q10'] == 'C':
        prompt += "- Include example usage and sample outputs in comments.\n"
    elif answers['q10'] == 'D':
        prompt += "- Provide basic working code with room for experimentation.\n"
    
    # Q11: Code organization/cleanliness
    if answers['q11'] == 'A':
        prompt += "- Use strict formatting: consistent indentation, spacing, and alignment.\n"
    elif answers['q11'] == 'B':
        prompt += "- Flexible formatting, prioritize readability over strict rules.\n"
    elif answers['q11'] == 'C':
        prompt += "- Minimalist code style, remove unnecessary whitespace and comments.\n"
    elif answers['q11'] == 'D':
        prompt += "- Adaptive formatting based on context and code complexity.\n"
    
    # Q12: Writing/communication style
    if answers['q12'] == 'A':
        prompt += "- Write complete sentence comments with proper grammar and punctuation.\n"
    elif answers['q12'] == 'B':
        prompt += "- Use casual, conversational comments (e.g., 'grab the data', 'check if it works').\n"
    elif answers['q12'] == 'C':
        prompt += "- Use brief, fragmented comments and bullet points.\n"
    elif answers['q12'] == 'D':
        prompt += "- Mix formal and informal comments based on code context.\n"
    
    # Q13: Complexity preference
    if answers['q13'] == 'A':
        prompt += "- Prefer clear, explicit solutions even if verbose. Avoid clever tricks.\n"
    elif answers['q13'] == 'B':
        prompt += "- Use creative, elegant solutions and modern language features.\n"
    elif answers['q13'] == 'C':
        prompt += "- Implement challenging, optimized algorithms when possible.\n"
    elif answers['q13'] == 'D':
        prompt += "- Keep solutions simple and straightforward, avoid overengineering.\n"
    
    # Q14: Tool usage style
    if answers['q14'] == 'A':
        prompt += "- Include tutorial-style comments explaining how each part works.\n"
    elif answers['q14'] == 'B':
        prompt += "- Use diverse language features and libraries freely.\n"
    elif answers['q14'] == 'C':
        prompt += "- Stick to basic, well-known patterns and vanilla implementations.\n"
    elif answers['q14'] == 'D':
        prompt += "- Include helpful comments pointing to resources and documentation.\n"
    
    # Q15: Communication personality
    if answers['q15'] == 'A':
        prompt += "- Use comprehensive, well-structured comments with full explanations.\n"
    elif answers['q15'] == 'B':
        prompt += "- Keep comments brief and efficient, no fluff.\n"
    elif answers['q15'] == 'C':
        prompt += "- Use friendly, approachable language in comments (e.g., 'Let's do this!', 'Nice!').\n"
    elif answers['q15'] == 'D':
        prompt += "- Use emojis and expressive comments where appropriate (e.g., '# ✅ Works!', '# 🚀 Fast solution').\n"
    
    # Q16: Input method preference
    if answers['q16'] == 'A':
        prompt += "- Hardcode sample input values directly in the code for testing.\n"
    elif answers['q16'] == 'B':
        prompt += "- Use interactive prompts (input()) to get user input.\n"
    elif answers['q16'] == 'C':
        prompt += "- Read input from files or external data sources.\n"
    elif answers['q16'] == 'D':
        prompt += "- Use function parameters and pass data programmatically.\n"
    
    # Q17: Testing with different inputs
    if answers['q17'] == 'A':
        prompt += "- Create multiple test cases with different hardcoded values in the code.\n"
    elif answers['q17'] == 'B':
        prompt += "- Run the program multiple times and type different values each time.\n"
    elif answers['q17'] == 'C':
        prompt += "- Use lists of test inputs and loop through them.\n"
    elif answers['q17'] == 'D':
        prompt += "- Use a single representative input value that covers the general case.\n"
    
    # Q18: Input validation
    if answers['q18'] == 'A':
        prompt += "- Detailed validation with specific error messages for each type of invalid input.\n"
    elif answers['q18'] == 'B':
        prompt += "- Basic validation with simple retry loops until input is correct.\n"
    elif answers['q18'] == 'C':
        prompt += "- Use default values or corrections when input is invalid.\n"
    elif answers['q18'] == 'D':
        prompt += "- Minimal validation, assume users provide correct input.\n"
    
    # Q19: Multiple inputs handling
    if answers['q19'] == 'A':
        prompt += "- Ask for each input separately with clear prompts (e.g., 'Enter name: ', 'Enter age: ').\n"
    elif answers['q19'] == 'B':
        prompt += "- Get all inputs at once (e.g., comma-separated, space-separated using split()).\n"
    elif answers['q19'] == 'C':
        prompt += "- Use a menu or numbered options for users to select from.\n"
    elif answers['q19'] == 'D':
        prompt += "- Accept inputs as command-line arguments (sys.argv) or configuration.\n"
    
    # Q20: Input instruction display
    if answers['q20'] == 'A':
        prompt += "- Show detailed instructions with examples (e.g., 'Enter date (YYYY-MM-DD): ').\n"
    elif answers['q20'] == 'B':
        prompt += "- Brief prompts (e.g., 'Name: ', 'Age: ').\n"
    elif answers['q20'] == 'C':
        prompt += "- No prompts, let the code structure be self-evident.\n"
    elif answers['q20'] == 'D':
        prompt += "- Show instructions once at the beginning, then simple prompts.\n"
    
    prompt += "\nGenerate code that strictly follows ALL of these guidelines. The code should reflect this specific personality and style in every aspect."
    
    return prompt


# Add this model to your main.py
class PersonalizationAnswers(BaseModel):
    q1: str
    q2: str
    q3: str
    q4: str
    q5: str
    q6: str
    q7: str
    q8: str
    q9: str
    q10: str
    q11: str
    q12: str
    q13: str
    q14: str
    q15: str
    q16: str
    q17: str
    q18: str
    q19: str
    q20: str


# Add this endpoint to your main.py (after the other endpoints)
@app.post("/personalization/save")
async def save_personalization_preferences(
    answers: PersonalizationAnswers,
    user: dict = Depends(verify_client_bound_request)
):
    """
    Save user's coding style personalization preferences.
    Each answer should be 'A', 'B', 'C', 'D', or 'E'.
    Generates and stores the AI prompt string.
    """
    try:
        sidhi_id = user.get("sub")
        
        # Validate all answers are valid choices
        valid_choices = {'A', 'B', 'C', 'D', 'E'}
        answers_dict = answers.dict()
        
        for question, answer in answers_dict.items():
            if answer not in valid_choices:
                raise HTTPException(
                    status_code=400, 
                    detail=f"Invalid answer '{answer}' for {question}. Must be A, B, C, D, or E"
                )
        
        # Generate the prompt string using our function
        prompt_string = generate_coding_style_prompt(answers_dict)
        
        # Prepare personalization document
        personalization_doc = {
            "sidhi_id": sidhi_id,
            "answers": answers_dict,
            "prompt": prompt_string,  # Store the generated prompt
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
            "version": 1  # For future schema updates
        }
        
        # Upsert: Update if exists, insert if new
        result = await db.personalization.update_one(
            {"sidhi_id": sidhi_id},
            {
                "$set": {
                    "answers": answers_dict,
                    "prompt": prompt_string,
                    "updated_at": datetime.utcnow()
                },
                "$setOnInsert": {
                    "created_at": datetime.utcnow(),
                    "version": 1
                }
            },
            upsert=True
        )
        
        return {
            "status": "success",
            "message": "Personalization preferences saved successfully",
            "sidhi_id": sidhi_id,
            "is_new": result.upserted_id is not None,
            "updated_at": personalization_doc["updated_at"],
            "prompt_preview": prompt_string[:200] + "..."  # Show first 200 chars
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/personalization/get")
async def get_personalization_preferences(
    user: dict = Depends(verify_client_bound_request)
):
    """
    Retrieve user's saved personalization preferences.
    Returns null if user hasn't set preferences yet.
    """
    try:
        sidhi_id = user.get("sub")
        
        personalization = await db.personalization.find_one(
            {"sidhi_id": sidhi_id},
            {"_id": 0}  # Exclude MongoDB _id from response
        )
        
        if not personalization:
            return {
                "status": "success",
                "exists": False,
                "message": "No personalization preferences found",
                "sidhi_id": sidhi_id,
                "data": None
            }
        
        return {
            "status": "success",
            "exists": True,
            "sidhi_id": sidhi_id,
            "data": personalization
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/personalization/delete")
async def delete_personalization_preferences(
    user: dict = Depends(verify_client_bound_request)
):
    """
    Delete user's personalization preferences (reset to default).
    """
    try:
        sidhi_id = user.get("sub")
        
        result = await db.personalization.delete_one({"sidhi_id": sidhi_id})
        
        if result.deleted_count == 0:
            return {
                "status": "success",
                "message": "No personalization preferences to delete",
                "sidhi_id": sidhi_id
            }
        
        return {
            "status": "success",
            "message": "Personalization preferences deleted successfully",
            "sidhi_id": sidhi_id
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/personalization/stats")
async def get_personalization_stats(
    user: dict = Depends(verify_client_bound_request)
):
    """
    Get statistics about user's coding style preferences.
    Useful for displaying a profile summary.
    """
    try:
        sidhi_id = user.get("sub")
        
        personalization = await db.personalization.find_one({"sidhi_id": sidhi_id})
        
        if not personalization:
            raise HTTPException(
                status_code=404, 
                detail="No personalization preferences found. Please complete the quiz first."
            )
        
        answers = personalization.get("answers", {})
        
        # Analyze all 20 preferences
        stats = {
            "sidhi_id": sidhi_id,
            "profile": {
                "q1_problem_solving": _get_problem_solving_style(answers.get("q1")),
                "q2_documentation": _get_doc_style(answers.get("q2")),
                "q3_naming": _get_naming_style(answers.get("q3")),
                "q4_organization": _get_organization_style(answers.get("q4")),
                "q5_precision": _get_precision_level(answers.get("q5")),
                "q6_explanation": _get_explanation_style(answers.get("q6")),
                "q7_error_handling": _get_error_handling_style(answers.get("q7")),
                "q8_debugging": _get_debugging_style(answers.get("q8")),
                "q9_planning": _get_planning_style(answers.get("q9")),
                "q10_learning": _get_learning_style(answers.get("q10")),
                "q11_cleanliness": _get_code_cleanliness(answers.get("q11")),
                "q12_communication": _get_communication_style(answers.get("q12")),
                "q13_complexity": _get_complexity_preference(answers.get("q13")),
                "q14_tool_usage": _get_tool_usage_style(answers.get("q14")),
                "q15_personality": _get_personality_style(answers.get("q15")),
                "q16_input_method": _get_input_preference(answers.get("q16")),
                "q17_testing": _get_testing_style(answers.get("q17")),
                "q18_validation": _get_validation_style(answers.get("q18")),
                "q19_multi_input": _get_multi_input_style(answers.get("q19")),
                "q20_instructions": _get_instruction_style(answers.get("q20"))
            },
            "created_at": personalization.get("created_at"),
            "updated_at": personalization.get("updated_at")
        }
        
        return {
            "status": "success",
            "stats": stats
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Helper functions for all 20 questions
def _get_problem_solving_style(answer):
    """Q1: Problem-solving approach"""
    styles = {
        'A': 'Step-by-Step Breakdown',
        'B': 'Big Picture First',
        'C': 'Iterative Trial & Error',
        'D': 'Reference-Based Learning'
    }
    return styles.get(answer, 'Unknown')

def _get_doc_style(answer):
    """Q2: Documentation style"""
    styles = {
        'A': 'Detailed Documentation',
        'B': 'Minimal Comments',
        'C': 'Visual Diagrams',
        'D': 'Structured Blocks',
        'E': 'No Comments (Self-Documenting)'
    }
    return styles.get(answer, 'Unknown')

def _get_naming_style(answer):
    """Q3: Naming convention"""
    styles = {
        'A': 'Very Descriptive Names',
        'B': 'Short Abbreviations',
        'C': 'Contextual Prefixes',
        'D': 'Simple Generic Names'
    }
    return styles.get(answer, 'Unknown')

def _get_organization_style(answer):
    """Q4: Code organization"""
    styles = {
        'A': 'Clearly Labeled Sections',
        'B': 'Chronological Order',
        'C': 'Proximity-Based Grouping',
        'D': 'Simple Linear Structure'
    }
    return styles.get(answer, 'Unknown')

def _get_precision_level(answer):
    """Q5: Code precision/detail"""
    styles = {
        'A': 'Explicit Validation & Edge Cases',
        'B': 'Straightforward Logic',
        'C': 'Example-Driven',
        'D': 'Minimal Assumptions'
    }
    return styles.get(answer, 'Unknown')

def _get_explanation_style(answer):
    """Q6: Explanation approach"""
    styles = {
        'A': 'Step-by-Step Inline',
        'B': 'Main Logic Only',
        'C': 'Analogies & Examples',
        'D': 'Self-Explanatory Code'
    }
    return styles.get(answer, 'Unknown')

def _get_error_handling_style(answer):
    """Q7: Error checking approach"""
    styles = {
        'A': 'Comprehensive Try-Catch',
        'B': 'Basic Critical Checks',
        'C': 'Defensive Programming',
        'D': 'Trust Inputs'
    }
    return styles.get(answer, 'Unknown')

def _get_debugging_style(answer):
    """Q8: Debugging/fixing approach"""
    styles = {
        'A': 'Detailed Logging',
        'B': 'Clean Logic Only',
        'C': 'Multiple Solution Paths',
        'D': 'Fresh Clean Code'
    }
    return styles.get(answer, 'Unknown')

def _get_planning_style(answer):
    """Q9: Planning vs doing"""
    styles = {
        'A': 'Detailed Pseudocode First',
        'B': 'Code First, Refactor Later',
        'C': 'Mix Planning & Implementation',
        'D': 'Direct Implementation'
    }
    return styles.get(answer, 'Unknown')

def _get_learning_style(answer):
    """Q10: Learning approach"""
    styles = {
        'A': 'Complete Documentation',
        'B': 'Experimental Testing',
        'C': 'Example-Based',
        'D': 'Basic Experimentation'
    }
    return styles.get(answer, 'Unknown')

def _get_code_cleanliness(answer):
    """Q11: Code organization/cleanliness"""
    styles = {
        'A': 'Strict Formatting Rules',
        'B': 'Flexible Readability',
        'C': 'Minimalist Style',
        'D': 'Adaptive Context-Based'
    }
    return styles.get(answer, 'Unknown')

def _get_communication_style(answer):
    """Q12: Writing/communication style"""
    styles = {
        'A': 'Formal Complete Sentences',
        'B': 'Casual Conversational',
        'C': 'Brief Bullet Points',
        'D': 'Mixed Context-Based'
    }
    return styles.get(answer, 'Unknown')

def _get_complexity_preference(answer):
    """Q13: Complexity preference"""
    styles = {
        'A': 'Clear & Explicit',
        'B': 'Creative & Elegant',
        'C': 'Optimized & Challenging',
        'D': 'Simple & Straightforward'
    }
    return styles.get(answer, 'Unknown')

def _get_tool_usage_style(answer):
    """Q14: Tool usage approach"""
    styles = {
        'A': 'Tutorial-Style Explanations',
        'B': 'Diverse Features & Libraries',
        'C': 'Basic Vanilla Patterns',
        'D': 'Resource References'
    }
    return styles.get(answer, 'Unknown')

def _get_personality_style(answer):
    """Q15: Communication personality"""
    styles = {
        'A': 'Comprehensive Structured',
        'B': 'Brief & Efficient',
        'C': 'Friendly & Approachable',
        'D': 'Expressive with Emojis'
    }
    return styles.get(answer, 'Unknown')

def _get_input_preference(answer):
    """Q16: Input method preference"""
    styles = {
        'A': 'Hardcoded Test Values',
        'B': 'Interactive Prompts',
        'C': 'File-Based Input',
        'D': 'Function Parameters'
    }
    return styles.get(answer, 'Unknown')

def _get_testing_style(answer):
    """Q17: Testing approach"""
    styles = {
        'A': 'Multiple Hardcoded Tests',
        'B': 'Manual Repeated Runs',
        'C': 'Loop Through Test Arrays',
        'D': 'Single Representative Case'
    }
    return styles.get(answer, 'Unknown')

def _get_validation_style(answer):
    """Q18: Input validation"""
    styles = {
        'A': 'Detailed Error Messages',
        'B': 'Basic Retry Loops',
        'C': 'Auto-Correct Defaults',
        'D': 'Minimal Validation'
    }
    return styles.get(answer, 'Unknown')

def _get_multi_input_style(answer):
    """Q19: Multiple inputs handling"""
    styles = {
        'A': 'Separate Clear Prompts',
        'B': 'All at Once (Separated)',
        'C': 'Menu/Numbered Options',
        'D': 'Command-Line Arguments'
    }
    return styles.get(answer, 'Unknown')

def _get_instruction_style(answer):
    """Q20: Input instruction display"""
    styles = {
        'A': 'Detailed with Examples',
        'B': 'Brief Prompts',
        'C': 'No Prompts (Self-Evident)',
        'D': 'Instructions Once, Then Brief'
    }
    return styles.get(answer, 'Unknown')


class ChatMessage(BaseModel):
    message: str
    session_id: str = None  # Optional: for maintaining conversation


@app.post("/help-bot/chat")
async def chat_with_bot(
    data: ChatMessage,
    user: dict = Depends(verify_client_bound_request)
):
    """
    Main chatbot endpoint
    """
    try:
        sidhi_id = user.get("sub")
        
        # Rate limiting: max 20 queries per hour per user
        hour_ago = datetime.utcnow().timestamp() - 3600
        recent_count = await db.help_bot_history.count_documents({
            "sidhi_id": sidhi_id,
            "timestamp": {"$gte": hour_ago}
        })
        
        if recent_count >= 20:
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded. Max 20 queries per hour."
            )
        
        # Get conversation history
        session_id = data.session_id or f"{sidhi_id}_{int(datetime.utcnow().timestamp())}"
        history_cursor = db.help_bot_history.find({
            "sidhi_id": sidhi_id,
            "session_id": session_id
        }).sort("timestamp", -1).limit(5)
        
        history = await history_cursor.to_list(length=5)
        conversation_history = [
            {"user": h["user_message"], "bot": h["bot_response"]}
            for h in reversed(history)
        ]
        
        # Generate response
        bot_response = generate_bot_response(data.message, conversation_history)
        
        # Save to database
        chat_record = {
            "sidhi_id": sidhi_id,
            "session_id": session_id,
            "user_message": data.message,
            "bot_response": bot_response,
            "timestamp": datetime.utcnow().timestamp(),
            "created_at": datetime.utcnow()
        }
        await db.help_bot_history.insert_one(chat_record)
        
        return {
            "status": "success",
            "response": bot_response,
            "session_id": session_id
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/help-bot/history")
async def get_chat_history(
    user: dict = Depends(verify_client_bound_request),
    session_id: str = None
):
    """
    Get chat history for a user
    """
    try:
        sidhi_id = user.get("sub")
        
        query = {"sidhi_id": sidhi_id}
        if session_id:
            query["session_id"] = session_id
        
        history_cursor = db.help_bot_history.find(
            query,
            {"_id": 0}
        ).sort("timestamp", -1).limit(50)
        
        history = await history_cursor.to_list(length=50)
        
        return {
            "status": "success",
            "history": history,
            "count": len(history)
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/help-bot/clear-session")
async def clear_session(
    session_id: str,
    user: dict = Depends(verify_client_bound_request)
):
    """
    Clear a specific chat session
    """
    try:
        sidhi_id = user.get("sub")
        
        result = await db.help_bot_history.delete_many({
            "sidhi_id": sidhi_id,
            "session_id": session_id
        })
        
        return {
            "status": "success",
            "message": f"Deleted {result.deleted_count} messages"
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

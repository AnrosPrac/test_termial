from fastapi import FastAPI, BackgroundTasks, Request, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from app.ai.router import router as ai_router
from app.chat.router import router as chat_router
from app.stream.router import router as stream_router
from app.api.auth_proxy import router as auth_router
from app.lum_cloud.sync_server import commit_to_github, setup_repo 
from nacl.signing import VerifyKey
import binascii
from app.lum_cloud.sync_server import LOCAL_REPO_DIR
import os
from app.ai.client_bound_guard import verify_client_bound_request
from fastapi import Query
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel
from app.ai.quota_manager import manager


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
    degree: str
    email_id: str
@app.on_event("startup")
async def startup_event():
    setup_repo()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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

app.include_router(ai_router, prefix="/ai")
app.include_router(chat_router)
app.include_router(auth_router)
app.include_router(stream_router)


# Schema for the approval request


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
            "message": "Backup available" if files_on_disk else "Nobackups found"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
@app.post("/sync/cloudapprove")
async def cloud_approve(data: ApprovalRequest, user: str = Depends(verify_client_bound_request)):
    try:
        # Update or Insert the student record
        # This links the JLab username (college_roll) to the Sidhi ID
        result = await db.users.update_one(
            {"sid_id": data.sid_id},
            {"$set": {"college_roll": data.college_roll, "name": data.username, "is_active": True}},
            upsert=True
        )
        
        return {
            "status": "success", 
            "message": f"User {data.college_roll} approved and linked to {data.sid_id}"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
@app.get("/debug")
async def get_user_context(user: dict = Depends(verify_client_bound_request)):
    try:
        return {
            "status": "success",
            "raw_payload": user,
            "available_keys": list(user.keys())
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
        sid_id = data.get("sidhilynx_id")
        roll_no = data.get("college_roll")
        files = data.get("files", {})
        
        # --- IDENTITY LOCK ---
        # Verify Terminal User (roll_no) against Platform User (sid_id)
        total_payload_size = sum(len(content.encode('utf-8')) for content in files.values())
        if total_payload_size > 2 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="Payload too large. Max 2MB allowed.")

        # 2. EXTENSION SECURITY CHECK
        # Reject if any file has a forbidden extension (e.g., .exe, .sh)
        for filename in files.keys():
            _, ext = os.path.splitext(filename)
            if ext.lower() not in ALLOWED_EXTENSIONS:
                 raise HTTPException(status_code=400, detail=f"File type {ext} not allowed.")
            
        user_record = await db.users.find_one({"college_roll": roll_no})
        
        if not user_record or user_record.get("sid_id") != sid_id:
             raise HTTPException(status_code=403, detail="Identity Mismatch: Terminal user not linked to Sidhi ID")

        if not sid_id or not files:
            return {"status": "error", "message": "Missing payload"}

        # Use sid_id for folder naming in GitHub
        background_tasks.add_task(commit_to_github, sid_id, files)
        return {"status": "success", "message": "Cloud sync initiated"}
    except HTTPException:
        # Re-raise HTTPExceptions so FastAPI handles the status codes correctly
        raise 
    except Exception as e:
        # For any other unexpected server error, send a 500 Internal Server Error
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

        # 3. RECURSIVE FILE READ
        vault_contents = {}
        for root, _, files in os.walk(student_folder):
            for filename in files:
                full_path = os.path.join(root, filename)
                
                # Create a relative path for the key (e.g., 'main.py' or 'utils/helper.py')
                relative_path = os.path.relpath(full_path, student_folder)
                
                try:
                    with open(full_path, "r", encoding="utf-8") as f:
                        vault_contents[relative_path] = f.read()
                except Exception:
                    continue # Skip binary or unreadable files

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

        new_user = data.dict()


        await db.users_profile.insert_one(new_user)

        return {
            "status": "success",
            "message": "User details successfully registered"
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
  

@app.get("/health")
def health():
    return {"status": "ok"}
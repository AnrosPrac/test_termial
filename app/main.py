from fastapi import FastAPI, BackgroundTasks, Request, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from app.ai.router import router as ai_router
from app.chat.router import router as chat_router
from app.stream.router import router as stream_router
from app.api.auth_proxy import router as auth_router
from app.lum_cloud.sync_server import commit_to_github, setup_repo 
from nacl.signing import VerifyKey
import binascii
import os
from motor.motor_asyncio import AsyncIOMotorClient

app = FastAPI(title="Lumetrics AI Engine")

# MongoDB Configuration
MONGO_URL = os.getenv("MONGO_URL")
client = AsyncIOMotorClient(MONGO_URL)
db = client.lumetrics_db 

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
        files = data.get("files")
        
        # --- IDENTITY LOCK ---
        # Verify Terminal User (roll_no) against Platform User (sid_id)
        user_record = await db.users.find_one({"college_roll": roll_no})
        
        if not user_record or user_record.get("sid_id") != sid_id:
             raise HTTPException(status_code=403, detail="Identity Mismatch: Terminal user not linked to Sidhi ID")

        if not sid_id or not files:
            return {"status": "error", "message": "Missing payload"}

        # Use sid_id for folder naming in GitHub
        background_tasks.add_task(commit_to_github, sid_id, files)
        return {"status": "success", "message": "Cloud sync initiated"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/health")
def health():
    return {"status": "ok"}
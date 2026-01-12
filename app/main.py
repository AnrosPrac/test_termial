from fastapi import FastAPI, BackgroundTasks, Request, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from app.ai.router import router as ai_router
from app.chat.router import router as chat_router
from app.stream.router import router as stream_router
from app.api.auth_proxy import router as auth_router
# Import both the worker AND the setup function
from app.lum_cloud.sync_server import commit_to_github, setup_repo 
from nacl.signing import VerifyKey
from nacl.exceptions import BadSignatureError
import binascii
import time

app = FastAPI(title="Lumetrics AI Engine")

# --- CTO FIX: Ensure Vault is ready on startup ---
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

# --- CTO SECURITY: Signature Verification ---
async def verify_signature(
    request: Request,
    x_client_public_key: str = Header(None),
    x_client_signature: str = Header(None),
    x_client_timestamp: str = Header(None)
):
    if not x_client_public_key or not x_client_signature or not x_client_timestamp:
        raise HTTPException(status_code=401, detail="Missing auth headers")

    # Replay Attack Prevention (30s window)
    if abs(time.time() - float(x_client_timestamp)) > 30:
        raise HTTPException(status_code=401, detail="Request expired")

    try:
        verify_key = VerifyKey(binascii.unhexlify(x_client_public_key))
        message = f"{x_client_timestamp}:{request.url.path}".encode()
        verify_key.verify(message, binascii.unhexlify(x_client_signature))
        return x_client_public_key
    except (BadSignatureError, Exception):
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
        student_id = data.get("student_id")
        files = data.get("files")
        
        # Identity Check
        if student_id != authenticated_pk:
             return {"status": "error", "message": "Identity mismatch."}

        if not student_id or not files:
            return {"status": "error", "message": "Missing payload"}

        # Use the worker we imported
        background_tasks.add_task(commit_to_github, student_id, files)
        return {"status": "success", "message": "Cloud sync initiated"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/health")
def health():
    return {"status": "ok"}

# Add a Root route so you don't get 404s when checking the URL
@app.get("/")
def root():
    return {"message": "Lumetrics Engine Online", "vault": "active"}
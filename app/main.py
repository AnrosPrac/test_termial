from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.ai.router import router as ai_router
from app.chat.router import router as chat_router
from app.stream.router import router as stream_router

app = FastAPI(title="Lumetrics AI Engine")

# --- CTO SECURITY FIX: Allow JLab to connect ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Include Routers ---
app.include_router(ai_router, prefix="/ai")
app.include_router(chat_router)
app.include_router(stream_router) # This was missing!

@app.get("/health")
def health():
    return {"status": "ok"}
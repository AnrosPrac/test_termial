from fastapi import FastAPI
from app.ai.router import router as ai_router

app = FastAPI(title="Lumetrics AI Engine")

app.include_router(ai_router, prefix="/ai")

@app.get("/health")
def health():
    return {"status": "ok"}
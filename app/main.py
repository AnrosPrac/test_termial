from fastapi import FastAPI
from app.ai.router import router as ai_router
from app.chat.router import router as chat_router


app = FastAPI(title="Lumetrics AI Engine")

app.include_router(ai_router, prefix="/ai")
app.include_router(chat_router)
@app.get("/health")
def health():
    return {"status": "ok"}
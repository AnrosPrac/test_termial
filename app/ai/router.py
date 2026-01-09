from fastapi import APIRouter, HTTPException
from app.ai.services import execute_ai

router = APIRouter()

@router.post("/execute")
def ai_execute(payload: dict):
    try:
        return {
            "output": execute_ai(
                mode=payload["mode"],
                version=payload.get("version", "standard"),
                language=payload.get("language", "english"),
                input_text=payload["input"]
            )
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
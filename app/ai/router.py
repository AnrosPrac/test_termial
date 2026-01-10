from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from pathlib import Path
from app.ai.injector import process_injection_to_memory
from app.ai.services import execute_ai

router = APIRouter()

class InjectRequest(BaseModel):
    text_content: str

@router.post("/inject")
async def ai_inject(payload: InjectRequest):
    try:
        files_dict = process_injection_to_memory(payload.text_content)
        return {"status": "success", "files": files_dict}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/execute")
def ai_execute(payload: dict):
    try:
        input_primary = payload.get("input") or payload.get("input1")
        result = execute_ai(
            mode=payload["mode"],
            version=payload.get("version", "standard"),
            language=payload.get("language", "english"),
            input_text=input_primary
        )
        if isinstance(result, Path) and result.exists():
            return FileResponse(result, media_type="image/png")
        return {"output": result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
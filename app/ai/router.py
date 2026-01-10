from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel
from pathlib import Path
from app.ai.injector import process_injection_to_memory
from app.ai.services import execute_ai
from app.ai.formatter import process_formatting
from ...auth_utils import verify_lum_token

router = APIRouter()

class InjectRequest(BaseModel):
    text_content: str

class FormatRequest(BaseModel):
    text_content: str

@router.post("/format")
async def ai_format(payload: FormatRequest, user: dict = Depends(verify_lum_token)):
    try:
        formatted_text = process_formatting(payload.text_content)
        return {"status": "success", "output": formatted_text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/inject")
async def ai_inject(payload: dict, user: dict = Depends(verify_lum_token)):
    try:
        text_content = payload.get("text_content")
        files_dict = process_injection_to_memory(text_content)
        return {"status": "success", "files": files_dict}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/execute")
def ai_execute(payload: dict, user: dict = Depends(verify_lum_token)):
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
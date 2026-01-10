import httpx
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel
from pathlib import Path
from app.ai.injector import process_injection_to_memory
from app.ai.services import execute_ai
from app.ai.auth_utils import verify_token

router = APIRouter()

class LoginRequest(BaseModel):
    email: str
    password: str

class InjectRequest(BaseModel):
    text_content: str

@router.post("/login")
async def ai_login(payload: LoginRequest):
    auth_url = "https://clg-project-auth.onrender.com/api/v1/auth/login"
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                auth_url,
                json={"email": payload.email, "password": payload.password}
            )
            if response.status_code == 200:
                return response.json()
            raise HTTPException(status_code=response.status_code, detail="Auth failed")
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

@router.post("/inject")
async def ai_inject(payload: InjectRequest, token_data: dict = Depends(verify_token)):
    try:
        files_dict = process_injection_to_memory(payload.text_content)
        return {"status": "success", "files": files_dict}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/execute")
def ai_execute(payload: dict, token_data: dict = Depends(verify_token)):
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
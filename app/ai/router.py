from fastapi import APIRouter, HTTPException, Depends , BackgroundTasks
from fastapi.responses import FileResponse
from pydantic import BaseModel
from pathlib import Path
from app.ai.client_bound_guard import verify_client_bound_request
from app.ai.injector import process_injection_to_memory
from app.ai.services import execute_ai
from app.ai.formatter import process_formatting
from app.ai.auth_utils import verify_lum_token
from app.ai.quota_manager import check_and_use_quota,log_activity
from app.payments.router import get_user_quotas, is_quota_expired, activate_tier_idempotent
from app.ai.cell_logic import process_cells_generation # Import the new logic

router = APIRouter()

class InjectRequest(BaseModel):
    text_content: str

class FormatRequest(BaseModel):
    text_content: str


class CellsRequest(BaseModel):
    text_content: str


async def handle_quota_expiry(sidhi_id: str):
    """Helper function to check and handle quota expiry"""
    quota = await get_user_quotas(sidhi_id)
    
    if is_quota_expired(quota):
        # Auto-downgrade to free tier
        await activate_tier_idempotent(sidhi_id, "free")
        quota = await get_user_quotas(sidhi_id)
    
    return quota


@router.post("/cells")
async def ai_cells(payload: CellsRequest, user: dict = Depends(verify_client_bound_request)):
    
    try:
        sidhi_id = user.get("sub")
        quota = await handle_quota_expiry(sidhi_id)
        await check_and_use_quota(sidhi_id, "cells")
        data = process_cells_generation(payload.text_content)
        return {"status": "success", "tasks": data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
@router.post("/format")
async def ai_format(payload: FormatRequest, user: dict = Depends(verify_client_bound_request)):
    try:
        sidhi_id = user.get("sub")
        quota = await handle_quota_expiry(sidhi_id)
        await check_and_use_quota(sidhi_id, "format")
        formatted_text = process_formatting(payload.text_content)
        return {"status": "success", "output": formatted_text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/inject")
async def ai_inject(payload: dict, user: dict = Depends(verify_client_bound_request)):
    try:
        sidhi_id = user.get("sub")
        quota = await handle_quota_expiry(sidhi_id)
        await check_and_use_quota(sidhi_id, "inject")

        text_content = payload.get("text_content")
        files_dict = process_injection_to_memory(text_content)
        return {"status": "success", "files": files_dict}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/execute")
async def ai_execute(payload: dict, user: dict = Depends(verify_client_bound_request), background_tasks: BackgroundTasks = None):
    try:
        sidhi_id = user.get("sub")
        quota = await handle_quota_expiry(sidhi_id)
        await check_and_use_quota(sidhi_id, payload["mode"])
        input_primary = payload.get("input") or payload.get("input1")
        result = execute_ai(
            mode=payload["mode"],
            version=payload.get("version", "standard"),
            language=payload.get("language", "english"),
            input_text=input_primary
        )
        if isinstance(result, Path) and result.exists():
            return FileResponse(result, media_type="image/png")
        background_tasks.add_task(log_activity, sidhi_id, payload["mode"], True)
        return {"output": result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

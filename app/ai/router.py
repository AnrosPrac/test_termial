from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from app.ai.services import execute_ai
from pathlib import Path

router = APIRouter()

@router.post("/execute")
def ai_execute(payload: dict):
    try:
        result = execute_ai(
            mode=payload["mode"],
            version=payload.get("version", "standard"),
            language=payload.get("language", "english"),
            input_text=payload["input"]
        )

        # Check if the result is a file path (for Flowcharts)
        if isinstance(result, Path) and result.exists():
            return FileResponse(result, media_type="image/png")
        
        # Otherwise return standard JSON output
        return {"output": result}
        
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
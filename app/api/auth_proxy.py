# app/api/auth_proxy.py
from fastapi import APIRouter, HTTPException
import httpx

router = APIRouter()

@router.post("/auth/login")
async def login_proxy(data: dict):
    # Your server talks to SidhiLynx
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://auth.sidhi.xyz/api/v1/auth/login",
            json=data
        )
        
        if response.status_code != 200:
            raise HTTPException(status_code=401, detail="Authentication failed")
            
        # Return SidhiLynx tokens (access_token, refresh_token) to the CLI
        return response.json()
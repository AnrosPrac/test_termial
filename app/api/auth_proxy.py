# app/api/auth_proxy.py

from fastapi import APIRouter, HTTPException, Request
import httpx

router = APIRouter()

SIDHI_AUTH_URL = "https://auth.sidhi.xyz/api/v1/auth/login"


@router.post("/auth/login")
async def login_proxy(request: Request, data: dict):
    """
    Proxy login request to SidhiLynx Auth.

    IMPORTANT:
    - This endpoint MUST forward all X-* headers unchanged.
    - Lumetrix must NOT inspect, modify, or generate any auth headers.
    - SidhiLynx is the sole authority for login validation.
    """

    # ✅ Forward ONLY Sidhi-required headers (crypto + app metadata)
    forwarded_headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower().startswith("x-")
    }

    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            SIDHI_AUTH_URL,
            json=data,
            headers=forwarded_headers
        )

    if response.status_code != 200:
        # Pass through Sidhi error safely
        raise HTTPException(
            status_code=401,
            detail=response.json().get("detail", "Authentication failed")
        )

    # ✅ Return Sidhi-issued tokens EXACTLY as received
    return response.json()

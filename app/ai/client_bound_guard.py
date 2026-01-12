# app/middleware/client_bound_guard.py

import time
import hashlib
from fastapi import Header, HTTPException, Request, Depends
from nacl.signing import VerifyKey
from nacl.exceptions import BadSignatureError

from app.ai.auth_utils import verify_lum_token


def verify_client_bound_request(
    request: Request,
    token_payload: dict = Depends(verify_lum_token),

    x_client_public_key: str = Header(...),
    x_client_signature: str = Header(...),
    x_client_timestamp: str = Header(...)
):
    # 1️⃣ Timestamp replay protection (±60s)
    try:
        ts = int(x_client_timestamp)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid timestamp")

    now = int(time.time())
    if abs(now - ts) > 60:
        raise HTTPException(status_code=401, detail="Stale request")

    # 2️⃣ Decode public key
    try:
        public_key_bytes = bytes.fromhex(x_client_public_key)
        verify_key = VerifyKey(public_key_bytes)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid client public key")

    # 3️⃣ Derive client_id SERVER-SIDE (CRITICAL)
    derived_client_id = hashlib.sha256(public_key_bytes).hexdigest()

    # 4️⃣ Enforce client binding (JWT cid)
    token_client_id = token_payload.get("cid")
    if not token_client_id:
        raise HTTPException(status_code=401, detail="Token not client-bound")

    if token_client_id != derived_client_id:
        raise HTTPException(status_code=401, detail="Client mismatch")

    # 5️⃣ Verify signature: <timestamp>:<request_path>
    message = f"{x_client_timestamp}:{request.url.path}".encode()

    try:
        verify_key.verify(message, bytes.fromhex(x_client_signature))
    except BadSignatureError:
        raise HTTPException(status_code=401, detail="Invalid client signature")

    # ✅ All checks passed
    return token_payload

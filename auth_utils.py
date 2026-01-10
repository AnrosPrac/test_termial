# app/middleware/auth_guard.py
from jose import jwt, JWTError
from fastapi import Header, HTTPException
import os

SECRET_KEY = os.getenv("JWT_SECRET_KEY") # Shared secret with Sidhilynx
ALGORITHM = "HS256"

def _decode_jwt_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or Expired Token")


def verify_lum_token(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    token = authorization.split(" ")[1]
    try:
        # Decodes and checks expiration/signature
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload  # Contains user_id and scopes
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or Expired Token")
    
def verify_lum_token_ws(token: str) -> dict:
    if not token:
        raise Exception("Unauthorized")
    return _decode_jwt_token(token)

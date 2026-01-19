"""
Basic Authentication for Admin Panel
Verifies username/password and issues admin JWT tokens with hardened security
"""

import os
import jwt
import uuid
from datetime import datetime, timedelta
from typing import Optional
from fastapi import HTTPException, Header

# Configuration validation
class Config:
    """Validated configuration - fails fast on missing vars"""
    
    def __init__(self):
        # Basic auth configuration (required)
        self.ADMIN_USERNAME = self._require_env("ADMIN_USERNAME")
        self.ADMIN_PASSWORD = self._require_env("ADMIN_PASSWORD")
        
        # JWT configuration (required)
        self.ADMIN_JWT_SECRET = self._require_env("ADMIN_JWT_SECRET")
        self.ADMIN_JWT_ALGORITHM = "HS256"
        self.ADMIN_TOKEN_EXPIRE_HOURS = 24
        self.JWT_ISSUER = "admin-panel"
        self.JWT_AUDIENCE = "admin-api"
    
    @staticmethod
    def _require_env(key: str) -> str:
        """Get required environment variable or crash"""
        value = os.getenv(key)
        if not value:
            raise RuntimeError(f"❌ FATAL: Missing required environment variable: {key}")
        return value


# Global config instance
config: Optional[Config] = None

# Session blacklist (in production, use Redis/MongoDB)
# Format: {jti: expiration_timestamp}
revoked_sessions: dict[str, float] = {}


def init_auth() -> None:
    """
    Initialize authentication system at app startup
    Call this in FastAPI @app.on_event("startup")
    
    Raises:
        RuntimeError: If configuration invalid
    """
    global config
    
    try:
        # Validate configuration
        config = Config()
        
        print(f"✅ Authentication system initialized")
        print(f"✅ Admin user configured: {config.ADMIN_USERNAME}")
        
    except Exception as e:
        raise RuntimeError(f"❌ FATAL: Authentication initialization failed: {e}")


def verify_credentials(username: str, password: str) -> dict:
    """
    Verify username/password credentials
    
    Args:
        username: Admin username
        password: Admin password
        
    Returns:
        dict: User info with email and role (same format as before for compatibility)
        
    Raises:
        HTTPException: If credentials invalid
    """
    if config is None:
        raise HTTPException(status_code=500, detail="Authentication system not initialized")
    
    # Verify credentials
    if username != config.ADMIN_USERNAME or password != config.ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    # Return user info in compatible format
    return {
        "email": f"{username}@admin.local",
        "uid": username,
        "email_verified": True,
        "username": username
    }


def create_admin_jwt(email: str) -> str:
    """
    Create hardened admin JWT token with session tracking
    
    Args:
        email: Admin email/identifier
        
    Returns:
        str: JWT token
    """
    if config is None:
        raise RuntimeError("Authentication system not initialized")
    
    expire = datetime.utcnow() + timedelta(hours=config.ADMIN_TOKEN_EXPIRE_HOURS)
    token_id = str(uuid.uuid4())
    
    payload = {
        "jti": token_id,  # Unique token ID for revocation
        "iss": config.JWT_ISSUER,
        "aud": config.JWT_AUDIENCE,
        "email": email,
        "role": "admin",
        "exp": expire,
        "iat": datetime.utcnow()
    }
    
    token = jwt.encode(payload, config.ADMIN_JWT_SECRET, algorithm=config.ADMIN_JWT_ALGORITHM)
    
    # In production: Store session in Redis/MongoDB
    # redis.setex(f"admin_session:{token_id}", EXPIRE_HOURS * 3600, email)
    
    return token


def verify_admin_jwt(token: str) -> dict:
    """
    Verify admin JWT token and check revocation status
    
    Args:
        token: JWT token
        
    Returns:
        dict: Decoded token payload
        
    Raises:
        HTTPException: If token invalid, expired, or revoked
    """
    if config is None:
        raise HTTPException(status_code=500, detail="Authentication system not initialized")
    
    try:
        payload = jwt.decode(
            token, 
            config.ADMIN_JWT_SECRET, 
            algorithms=[config.ADMIN_JWT_ALGORITHM],
            audience=config.JWT_AUDIENCE,
            issuer=config.JWT_ISSUER
        )
        
        # Verify role
        if payload.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Access denied")
        
        # Check if session revoked
        jti = payload.get("jti")
        if jti and _is_session_revoked(jti):
            raise HTTPException(status_code=401, detail="Session revoked")
        
        # In production: Verify session exists in Redis/MongoDB
        # if not redis.exists(f"admin_session:{jti}"):
        #     raise HTTPException(status_code=401, detail="Session expired")
        
        return payload
        
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Session expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Authentication failed")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="Authentication failed")


def revoke_admin_session(token: str) -> None:
    """
    Revoke admin session (logout)
    
    Args:
        token: JWT token to revoke
    """
    try:
        # Decode without verification to get jti
        payload = jwt.decode(
            token, 
            config.ADMIN_JWT_SECRET, 
            algorithms=[config.ADMIN_JWT_ALGORITHM],
            options={"verify_signature": False}
        )
        
        jti = payload.get("jti")
        exp = payload.get("exp")
        
        if jti and exp:
            # Add to blacklist until token expiration
            revoked_sessions[jti] = exp
            
            # In production: Add to Redis with TTL
            # redis.setex(f"revoked:{jti}", exp - time.time(), "1")
            # redis.delete(f"admin_session:{jti}")
            
            # Cleanup expired entries
            _cleanup_revoked_sessions()
            
    except Exception:
        # Silent fail on revocation - token will expire naturally
        pass


def _is_session_revoked(jti: str) -> bool:
    """Check if session is revoked"""
    if jti in revoked_sessions:
        # Check if revocation still valid
        if revoked_sessions[jti] > datetime.utcnow().timestamp():
            return True
        else:
            # Cleanup expired revocation
            del revoked_sessions[jti]
    return False


def _cleanup_revoked_sessions() -> None:
    """Remove expired revocations from memory"""
    now = datetime.utcnow().timestamp()
    expired = [jti for jti, exp in revoked_sessions.items() if exp <= now]
    for jti in expired:
        del revoked_sessions[jti]


async def get_current_admin(authorization: str = Header(None)) -> dict:
    """
    FastAPI dependency to protect admin routes
    
    ⚠️ IMPORTANT: This returns the SAME structure as before!
    Existing endpoints using this dependency will continue to work without changes.
    
    Usage:
        @router.get("/admin/dashboard")
        async def dashboard(admin: dict = Depends(get_current_admin)):
            return {"email": admin["email"], "role": admin["role"]}
    
    Args:
        authorization: Authorization header
        
    Returns:
        dict: Admin payload with 'email', 'role', 'jti', etc.
        
    Raises:
        HTTPException: If authentication fails
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    # Extract token from "Bearer <token>"
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentication required")
    
    token = authorization.split(" ")[1]
    
    # Verify JWT - returns same payload structure as before
    admin = verify_admin_jwt(token)
    
    return admin


# Example FastAPI integration
"""
from fastapi import FastAPI, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

app = FastAPI()

class LoginRequest(BaseModel):
    username: str
    password: str

@app.on_event("startup")
async def startup_event():
    init_auth()

@app.post("/auth/login")
async def login(credentials: LoginRequest):
    '''
    Login with username and password
    Returns JWT token for subsequent requests
    '''
    # Verify credentials
    user = verify_credentials(credentials.username, credentials.password)
    
    # Create admin session JWT
    admin_jwt = create_admin_jwt(user['email'])
    
    return {"token": admin_jwt, "email": user['email']}

@app.post("/auth/logout")
async def logout(admin: dict = Depends(get_current_admin), authorization: str = Header(None)):
    '''
    Logout - revokes current JWT token
    '''
    token = authorization.split(" ")[1]
    revoke_admin_session(token)
    return {"message": "Logged out successfully"}

@app.get("/admin/dashboard")
async def dashboard(admin: dict = Depends(get_current_admin)):
    '''
    Protected admin route - requires valid JWT token
    No changes needed to existing endpoints!
    '''
    return {
        "email": admin["email"], 
        "role": admin["role"],
        "message": "Welcome to admin dashboard"
    }

@app.get("/admin/users")
async def get_users(admin: dict = Depends(get_current_admin)):
    '''
    Another protected route - works exactly as before
    '''
    return {"users": [], "admin": admin["email"]}
"""

# Environment variables required:
# ADMIN_USERNAME=your_username
# ADMIN_PASSWORD=your_secure_password
# ADMIN_JWT_SECRET=your_jwt_secret_key_min_32_chars
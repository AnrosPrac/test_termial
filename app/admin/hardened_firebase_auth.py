"""
Firebase Authentication for Admin Panel
Verifies Firebase ID tokens and issues admin JWT tokens with hardened security
"""

import os
import jwt
import uuid
from datetime import datetime, timedelta
from typing import Optional, Set
from fastapi import HTTPException, Header
from firebase_admin import credentials, auth
import firebase_admin

# Configuration validation
class Config:
    """Validated configuration - fails fast on missing vars"""
    
    def __init__(self):
        self.FIREBASE_PROJECT_ID = self._require_env("FIREBASE_PROJECT_ID")
        self.FIREBASE_PRIVATE_KEY = self._require_env("FIREBASE_PRIVATE_KEY").replace('\\n', '\n')
        self.FIREBASE_CLIENT_EMAIL = self._require_env("FIREBASE_CLIENT_EMAIL")
        self.ADMIN_EMAILS = self._parse_admin_emails(self._require_env("ADMIN_EMAILS"))
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
    
    @staticmethod
    def _parse_admin_emails(emails_str: str) -> Set[str]:
        """Parse comma-separated admin emails into set"""
        emails = {email.strip().lower() for email in emails_str.split(',') if email.strip()}
        if not emails:
            raise RuntimeError("❌ FATAL: ADMIN_EMAILS must contain at least one email")
        return emails


# Global config instance
config: Optional[Config] = None

# Session blacklist (in production, use Redis/MongoDB)
# Format: {jti: expiration_timestamp}
revoked_sessions: dict[str, float] = {}


def init_firebase() -> None:
    """
    Initialize Firebase Admin SDK at app startup
    Call this in FastAPI @app.on_event("startup")
    
    Raises:
        RuntimeError: If configuration invalid or Firebase init fails
    """
    global config
    
    try:
        # Validate configuration first
        config = Config()
        
        # Initialize Firebase Admin SDK
        if not firebase_admin._apps:
            cred = credentials.Certificate({
                "type": "service_account",
                "project_id": config.FIREBASE_PROJECT_ID,
                "private_key": config.FIREBASE_PRIVATE_KEY,
                "client_email": config.FIREBASE_CLIENT_EMAIL,
                "token_uri": "https://oauth2.googleapis.com/token",
            })
            firebase_admin.initialize_app(cred)
        
        print(f"✅ Firebase Admin SDK initialized")
        print(f"✅ Admin allowlist: {len(config.ADMIN_EMAILS)} email(s)")
        
    except Exception as e:
        raise RuntimeError(f"❌ FATAL: Firebase initialization failed: {e}")


def verify_firebase_token(firebase_token: str) -> dict:
    """
    Verify Firebase ID token with strict claim validation
    
    Args:
        firebase_token: Firebase ID token from frontend
        
    Returns:
        dict: Decoded token with user info
        
    Raises:
        HTTPException: If token invalid or user not admin
    """
    if config is None:
        raise HTTPException(status_code=500, detail="Authentication system not initialized")
    
    try:
        # Verify Firebase token
        decoded_token = auth.verify_id_token(firebase_token)
        
        # Validate critical claims
        _validate_token_claims(decoded_token)
        
        # Extract and validate email
        email = decoded_token.get('email', '').lower()
        if not email:
            raise HTTPException(status_code=403, detail="Access denied")
        
        # Check admin allowlist
        if email not in config.ADMIN_EMAILS:
            raise HTTPException(
                status_code=403,
                detail="Access denied"
            )
        
        return decoded_token
        
    except HTTPException:
        raise
    except (auth.InvalidIdTokenError, auth.ExpiredIdTokenError):
        raise HTTPException(status_code=401, detail="Authentication failed")
    except Exception:
        raise HTTPException(status_code=401, detail="Authentication failed")


def _validate_token_claims(decoded_token: dict) -> None:
    """
    Validate Firebase token claims for security
    
    Args:
        decoded_token: Decoded Firebase token
        
    Raises:
        HTTPException: If claims invalid
    """
    # Validate audience matches project ID
    aud = decoded_token.get('aud')
    if aud != config.FIREBASE_PROJECT_ID:
        raise HTTPException(status_code=401, detail="Authentication failed")
    
    # Validate issuer
    expected_issuer = f"https://securetoken.google.com/{config.FIREBASE_PROJECT_ID}"
    iss = decoded_token.get('iss')
    if iss != expected_issuer:
        raise HTTPException(status_code=401, detail="Authentication failed")
    
    # Enforce email verification
    email_verified = decoded_token.get('email_verified', False)
    if not email_verified:
        raise HTTPException(status_code=403, detail="Email verification required")


def create_admin_jwt(email: str) -> str:
    """
    Create hardened admin JWT token with session tracking
    
    Args:
        email: Admin email
        
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
    
    Usage:
        @router.get("/admin/dashboard")
        async def dashboard(admin: dict = Depends(get_current_admin)):
            return {"email": admin["email"]}
    
    Args:
        authorization: Authorization header
        
    Returns:
        dict: Admin payload
        
    Raises:
        HTTPException: If authentication fails
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    # Extract token from "Bearer <token>"
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentication required")
    
    token = authorization.split(" ")[1]
    
    # Verify JWT
    admin = verify_admin_jwt(token)
    
    return admin


# Example FastAPI integration
"""
from fastapi import FastAPI, Depends
from fastapi.responses import JSONResponse

app = FastAPI()

@app.on_event("startup")
async def startup_event():
    init_firebase()

@app.post("/auth/login")
async def login(firebase_token: str):
    # Verify Firebase token
    user = verify_firebase_token(firebase_token)
    
    # Create admin session
    admin_jwt = create_admin_jwt(user['email'])
    
    return {"token": admin_jwt}

@app.post("/auth/logout")
async def logout(admin: dict = Depends(get_current_admin), authorization: str = Header(None)):
    token = authorization.split(" ")[1]
    revoke_admin_session(token)
    return {"message": "Logged out successfully"}

@app.get("/admin/dashboard")
async def dashboard(admin: dict = Depends(get_current_admin)):
    return {"email": admin["email"], "role": admin["role"]}
"""
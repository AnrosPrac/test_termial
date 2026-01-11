from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from typing import Optional
from app.ai.auth_utils import verify_lum_token_ws
from .manager import stream_manager

router = APIRouter(prefix="/stream")

def extract_ws_token(websocket: WebSocket) -> Optional[str]:
    auth = websocket.headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1]
    return websocket.query_params.get("token")

@router.websocket("/source/{username}")
async def stream_source(websocket: WebSocket, username: str):
    token = extract_ws_token(websocket)
    if not token:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    try:
        user = verify_lum_token_ws(token)
        # Force the streamer to use their actual ID from the token
        authenticated_user = user.get("sidhi_id")
        
        if authenticated_user != username:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
            
    except Exception:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()
    await stream_manager.start_broadcast(authenticated_user)

    try:
        while True:
            # The CLI sends: {"code": "...", "file": "...", "ts": ...}
            data = await websocket.receive_json()
            # Broadcast to all spectators registered for this streamer
            await stream_manager.broadcast_code(authenticated_user, data)
    except (WebSocketDisconnect, Exception):
        await stream_manager.stop_stream(authenticated_user)

@router.websocket("/watch/{target_user}")
async def stream_watcher(websocket: WebSocket, target_user: str):
    token = extract_ws_token(websocket)
    # Auth check (Spectators must also be logged in)
    if not token:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    try:
        verify_lum_token_ws(token)
    except Exception:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()
    # Add this connection to the target_user's spectator list
    await stream_manager.add_spectator(target_user, websocket)

    try:
        while True:
            # Just keep the connection alive
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await stream_manager.remove_spectator(target_user, websocket)
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
        # Ensure the streamer is who they say they are
        authenticated_user = user.get("sidhi_id") or user.get("username")
        
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
            # Receive data from the streamer CLI
            data = await websocket.receive_json()
            await stream_manager.broadcast_code(authenticated_user, data)
    except WebSocketDisconnect:
        # ✅ Cleanly remove stream and notify watchers
        await stream_manager.stop_stream(authenticated_user)
    except Exception:
        await stream_manager.stop_stream(authenticated_user)

@router.websocket("/watch/{target_user}")
async def stream_watcher(websocket: WebSocket, target_user: str):
    token = extract_ws_token(websocket)
    if not token:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    try:
        verify_lum_token_ws(token)
    except Exception:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    # Accept the connection first
    await websocket.accept()
    
    # Manager now handles sending the "Cached" code immediately on join
    await stream_manager.add_spectator(target_user, websocket)

    try:
        while True:
            # Keep connection alive and listen for client-side disconnects
            await websocket.receive_text()
    except WebSocketDisconnect:
        # The manager handles the cleanup via the broadcast loop exception
        pass
    finally:
        # Force cleanup if not already handled
        if target_user in stream_manager.active_streams:
            if websocket in stream_manager.active_streams[target_user]:
                stream_manager.active_streams[target_user].remove(websocket)
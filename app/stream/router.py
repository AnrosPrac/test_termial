from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from typing import Optional
from auth_utils import verify_lum_token_ws
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
    except Exception:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    authenticated_user = user.get("sidhi_id") or user.get("username")

    await websocket.accept()
    await stream_manager.start_broadcast(authenticated_user)

    try:
        while True:
            data = await websocket.receive_json()
            await stream_manager.broadcast_code(authenticated_user, data)
    except WebSocketDisconnect:
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

    await websocket.accept()
    await stream_manager.add_spectator(target_user, websocket)

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from .manager import manager
from app.ai.auth_utils import verify_lum_token_ws

router = APIRouter(prefix="/stream")

@router.websocket("/source/{username}")
async def stream_source(websocket: WebSocket, username: str):
    token = websocket.headers.get("authorization")
    if token and token.lower().startswith("bearer "):
        token = token.split(" ", 1)[1]
    else:
        token = websocket.query_params.get("token")

    if not token:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    try:
        user = verify_lum_token_ws(token)
    except:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    auth_user = user.get("sidhi_id") or username
    await websocket.accept()
    await manager.start_broadcast(auth_user)

    try:
        while True:
            data = await websocket.receive_json()
            await manager.broadcast_code(auth_user, data)
    except WebSocketDisconnect:
        await manager.stop_stream(auth_user)

@router.websocket("/watch/{target_user}")
async def stream_watch(websocket: WebSocket, target_user: str):
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()
    await manager.add_spectator(target_user, websocket)

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await manager.remove_spectator(target_user, websocket)
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from .manager import manager
from app.ai.auth_utils import verify_lum_token_ws

router = APIRouter(prefix="/chat")

@router.websocket("/{channel_id}/{password}/{username}")
async def websocket_endpoint(
    websocket: WebSocket,
    channel_id: str,
    password: str,
    username: str
):
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
    except Exception:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    authenticated_username = user.get("sidhi_id") or user.get("username") or username

    await websocket.accept()

    success = await manager.connect(websocket, channel_id, password)
    if not success:
        return

    try:
        await manager.broadcast(channel_id, {
            "type": "system",
            "user": "SYSTEM",
            "msg": f"{authenticated_username} joined."
        })

        while True:
            data = await websocket.receive_text()
            await manager.broadcast(channel_id, {
                "type": "chat",
                "user": authenticated_username,
                "msg": data
            })

    except WebSocketDisconnect:
        await manager.disconnect(websocket, channel_id)
        await manager.broadcast(channel_id, {
            "type": "system",
            "user": "SYSTEM",
            "msg": f"{authenticated_username} left."
        })
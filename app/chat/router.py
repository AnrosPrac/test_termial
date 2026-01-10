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
    # 🔐 1. Extract token (header or query)
    token = None

    auth_header = websocket.headers.get("authorization")
    if auth_header and auth_header.lower().startswith("bearer "):
        token = auth_header.split(" ", 1)[1]
    else:
        token = websocket.query_params.get("token")

    # ❌ No token → reject immediately
    if not token:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    # 🔐 2. Verify token
    try:
        user = verify_lum_token_ws(token)
    except Exception:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    # 🧠 3. Server-truth identity (DO NOT trust URL username)
    authenticated_username = user.get("sidhi_id") or user.get("username")

    # ✅ 4. Accept connection
    await websocket.accept()

    # 🔐 5. Channel auth (existing logic preserved)
    success = await manager.connect(websocket, channel_id, password)
    if not success:
        await websocket.close()
        return

    try:
        # Announce arrival (use authenticated identity)
        await manager.broadcast(channel_id, {
            "type": "system",
            "user": "SYSTEM",
            "msg": f"{authenticated_username} joined the channel."
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

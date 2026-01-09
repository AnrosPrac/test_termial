from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from .manager import manager

router = APIRouter(prefix="/chat")

@router.websocket("/{channel_id}/{password}/{username}")
async def websocket_endpoint(websocket: WebSocket, channel_id: str, password: str, username: str):
    success = await manager.connect(websocket, channel_id, password)
    if not success:
        return

    try:
        # Announce arrival
        await manager.broadcast(channel_id, {
            "type": "system",
            "user": "SYSTEM",
            "msg": f"{username} joined the channel."
        })
        
        while True:
            data = await websocket.receive_text()
            await manager.broadcast(channel_id, {
                "type": "chat",
                "user": username,
                "msg": data
            })
    except WebSocketDisconnect:
        await manager.disconnect(websocket, channel_id)
        await manager.broadcast(channel_id, {
            "type": "system", 
            "user": "SYSTEM",
            "msg": f"{username} left."
        })
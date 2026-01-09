from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from .manager import stream_manager

router = APIRouter(prefix="/stream")

@router.websocket("/source/{username}")
async def stream_source(websocket: WebSocket, username: str):
    await websocket.accept()
    await stream_manager.start_broadcast(username)
    try:
        while True:
            data = await websocket.receive_json() # Expecting {"code": "...", "file": "..."}
            await stream_manager.broadcast_code(username, data["code"], data["file"])
    except WebSocketDisconnect:
        await stream_manager.stop_stream(username)

@router.websocket("/watch/{target_user}")
async def stream_watcher(websocket: WebSocket, target_user: str):
    await stream_manager.add_spectator(target_user, websocket)
    try:
        while True:
            await websocket.receive_text() # Keep connection alive
    except WebSocketDisconnect:
        pass
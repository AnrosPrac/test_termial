from fastapi import WebSocket
from typing import Dict, List

class ChatManager:
    def __init__(self):
        self.rooms: Dict[str, Dict] = {}

    async def connect(self, websocket: WebSocket, channel_id: str, password: str):
        if channel_id not in self.rooms:
            self.rooms[channel_id] = {"pass": password, "users": []}
        
        if self.rooms[channel_id]["pass"] != password:
            await websocket.send_json({
                "type": "error", 
                "user": "SYSTEM", 
                "msg": "Invalid Channel Password"
            })
            await websocket.close()
            return False
            
        self.rooms[channel_id]["users"].append(websocket)
        return True

    async def disconnect(self, websocket: WebSocket, channel_id: str):
        if channel_id in self.rooms:
            if websocket in self.rooms[channel_id]["users"]:
                self.rooms[channel_id]["users"].remove(websocket)
            
            if not self.rooms[channel_id]["users"]:
                del self.rooms[channel_id]

    async def broadcast(self, channel_id: str, message: dict):
        if channel_id in self.rooms:
            active_connections = list(self.rooms[channel_id]["users"])
            for connection in active_connections:
                try:
                    await connection.send_json(message)
                except Exception:
                    if connection in self.rooms[channel_id]["users"]:
                        self.rooms[channel_id]["users"].remove(connection)

manager = ChatManager()
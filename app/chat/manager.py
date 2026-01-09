from fastapi import WebSocket
from typing import Dict

class ChatManager:
    def __init__(self):
        self.rooms: Dict[str, Dict] = {}

    async def connect(self, websocket: WebSocket, channel_id: str, password: str):
        await websocket.accept()
        
        if channel_id not in self.rooms:
            self.rooms[channel_id] = {"pass": password, "users": []}
        
        if self.rooms[channel_id]["pass"] != password:
            await websocket.send_json({"type": "error", "user": "SYSTEM", "msg": "Wrong password!"})
            await websocket.close()
            return False
            
        self.rooms[channel_id]["users"].append(websocket)
        return True

    async def disconnect(self, websocket: WebSocket, channel_id: str):
        if channel_id in self.rooms:
            self.rooms[channel_id]["users"].remove(websocket)
            # CLEANUP: Delete the room if it's empty to save RAM
            if not self.rooms[channel_id]["users"]:
                del self.rooms[channel_id]

    async def broadcast(self, channel_id: str, message: dict):
        if channel_id in self.rooms:
            # Create a copy of the list to avoid 'size changed during iteration' errors
            for connection in list(self.rooms[channel_id]["users"]):
                try:
                    await connection.send_json(message)
                except:
                    # If a connection is dead, remove it silently
                    self.rooms[channel_id]["users"].remove(connection)

manager = ChatManager()
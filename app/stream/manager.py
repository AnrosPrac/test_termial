from fastapi import WebSocket
from typing import Dict, List
import json

class StreamManager:
    def __init__(self):
        self.active_streams: Dict[str, List[WebSocket]] = {}

    async def start_broadcast(self, streamer_name: str):
        if streamer_name not in self.active_streams:
            self.active_streams[streamer_name] = []

    async def add_spectator(self, streamer_name: str, websocket: WebSocket):
        await websocket.accept()
        if streamer_name not in self.active_streams:
            self.active_streams[streamer_name] = []
        self.active_streams[streamer_name].append(websocket)

    async def stop_stream(self, streamer_name: str):
        if streamer_name in self.active_streams:
            # Notify spectators or just close
            for ws in self.active_streams[streamer_name]:
                try:
                    await ws.close()
                except:
                    pass
            del self.active_streams[streamer_name]

    async def broadcast_code(self, streamer_name: str, data: dict):
        if streamer_name in self.active_streams:
            # We pass the entire dictionary (code, file, and ts for speed measurement)
            payload = {
                "type": "live_code", 
                "content": data.get("code"), 
                "file": data.get("file"),
                "ts": data.get("ts") 
            }
            
            disconnected_buckets = []
            for ws in self.active_streams[streamer_name]:
                try:
                    await ws.send_json(payload)
                except:
                    disconnected_buckets.append(ws)
            
            # Clean up disconnected spectators safely
            for ws in disconnected_buckets:
                if ws in self.active_streams[streamer_name]:
                    self.active_streams[streamer_name].remove(ws)

stream_manager = StreamManager()
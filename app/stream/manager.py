from fastapi import WebSocket
from typing import Dict, List

class StreamManager:
    def __init__(self):
        # Format: { "streamer_name": [spectator_ws1, spectator_ws2] }
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
            for ws in self.active_streams[streamer_name]:
                await ws.close()
            del self.active_streams[streamer_name]

    async def broadcast_code(self, streamer_name: str, code: str, filename: str):
        if streamer_name in self.active_streams:
            payload = {"type": "live_code", "content": code, "file": filename}
            for ws in self.active_streams[streamer_name]:
                try:
                    await ws.send_json(payload)
                except:
                    self.active_streams[streamer_name].remove(ws)

stream_manager = StreamManager()
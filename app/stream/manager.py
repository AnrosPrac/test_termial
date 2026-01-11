from fastapi import WebSocket
from typing import Dict, List
import asyncio

class StreamManager:
    def __init__(self):
        self.active_streams: Dict[str, List[WebSocket]] = {}
        self.stream_cache: Dict[str, dict] = {}
        self.lock = asyncio.Lock()

    async def start_broadcast(self, streamer_name: str):
        async with self.lock:
            if streamer_name not in self.active_streams:
                self.active_streams[streamer_name] = []

    async def stop_stream(self, streamer_name: str):
        async with self.lock:
            if streamer_name in self.active_streams:
                del self.active_streams[streamer_name]
            if streamer_name in self.stream_cache:
                del self.stream_cache[streamer_name]

    async def add_spectator(self, streamer_name: str, websocket: WebSocket):
        async with self.lock:
            if streamer_name not in self.active_streams:
                self.active_streams[streamer_name] = []
            self.active_streams[streamer_name].append(websocket)
        
        if streamer_name in self.stream_cache:
            await websocket.send_json(self.stream_cache[streamer_name])

    async def remove_spectator(self, streamer_name: str, websocket: WebSocket):
        async with self.lock:
            if streamer_name in self.active_streams:
                if websocket in self.active_streams[streamer_name]:
                    self.active_streams[streamer_name].remove(websocket)

    async def broadcast_code(self, streamer_name: str, payload: dict):
        self.stream_cache[streamer_name] = payload
        if streamer_name in self.active_streams:
            for connection in list(self.active_streams[streamer_name]):
                try:
                    await connection.send_json(payload)
                except:
                    await self.remove_spectator(streamer_name, connection)

manager = StreamManager()
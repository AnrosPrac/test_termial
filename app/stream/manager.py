from fastapi import WebSocket
from typing import Dict, List
import asyncio

class StreamManager:
    def __init__(self):
        # Key: streamer_name, Value: List of spectator WebSockets
        self.active_streams: Dict[str, List[WebSocket]] = {}
        # Key: streamer_name, Value: Last sent payload (code, filename, ts)
        self.stream_cache: Dict[str, dict] = {}
        self.lock = asyncio.Lock()

    async def start_broadcast(self, streamer_name: str):
        async with self.lock:
            if streamer_name not in self.active_streams:
                self.active_streams[streamer_name] = []

    async def stop_stream(self, streamer_name: str):
        async with self.lock:
            if streamer_name in self.active_streams:
                # Close all spectator connections
                for ws in self.active_streams[streamer_name]:
                    try:
                        await ws.close()
                    except:
                        pass
                del self.active_streams[streamer_name]
            if streamer_name in self.stream_cache:
                del self.stream_cache[streamer_name]

    async def add_spectator(self, streamer_name: str, websocket: WebSocket):
        async with self.lock:
            if streamer_name not in self.active_streams:
                self.active_streams[streamer_name] = []
            self.active_streams[streamer_name].append(websocket)
        
        # If there is cached code, send it immediately to the new spectator
        if streamer_name in self.stream_cache:
            await websocket.send_json(self.stream_cache[streamer_name])

    async def remove_spectator(self, streamer_name: str, websocket: WebSocket):
        async with self.lock:
            if streamer_name in self.active_streams:
                if websocket in self.active_streams[streamer_name]:
                    self.active_streams[streamer_name].remove(websocket)

    async def broadcast_code(self, streamer_name: str, payload: dict):
        # Update Cache
        self.stream_cache[streamer_name] = payload
        
        if streamer_name in self.active_streams:
            # Create a copy to avoid "Set changed during iteration" errors
            spectators = list(self.active_streams[streamer_name])
            for ws in spectators:
                try:
                    await ws.send_json(payload)
                except Exception:
                    await self.remove_spectator(streamer_name, ws)

stream_manager = StreamManager()
from fastapi import WebSocket
from typing import Dict, List
import json
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

    async def add_spectator(self, streamer_name: str, websocket: WebSocket):
        async with self.lock:
            if streamer_name not in self.active_streams:
                self.active_streams[streamer_name] = []
            self.active_streams[streamer_name].append(websocket)
        
        if streamer_name in self.stream_cache:
            try:
                await websocket.send_json(self.stream_cache[streamer_name])
            except:
                await self.remove_spectator(streamer_name, websocket)

    async def remove_spectator(self, streamer_name: str, websocket: WebSocket):
        async with self.lock:
            if streamer_name in self.active_streams:
                if websocket in self.active_streams[streamer_name]:
                    self.active_streams[streamer_name].remove(websocket)

    async def stop_stream(self, streamer_name: str):
        async with self.lock:
            if streamer_name in self.active_streams:
                websockets = self.active_streams[streamer_name]
                close_tasks = [self._safe_close(ws) for ws in websockets]
                if close_tasks:
                    await asyncio.gather(*close_tasks)
                del self.active_streams[streamer_name]
            
            if streamer_name in self.stream_cache:
                del self.stream_cache[streamer_name]

    async def _safe_close(self, ws: WebSocket):
        try:
            await ws.close()
        except:
            pass

    async def broadcast_code(self, streamer_name: str, data: dict):
        if streamer_name not in self.active_streams:
            return

        payload = {
            "type": "live_code", 
            "content": data.get("code"), 
            "file": data.get("file"),
            "ts": data.get("ts") 
        }
        
        self.stream_cache[streamer_name] = payload
        
        disconnected = []
        async with self.lock:
            targets = list(self.active_streams[streamer_name])

        if targets:
            tasks = [self._send_and_track(ws, payload, disconnected) for ws in targets]
            await asyncio.gather(*tasks)
        
        if disconnected:
            async with self.lock:
                for ws in disconnected:
                    if ws in self.active_streams.get(streamer_name, []):
                        self.active_streams[streamer_name].remove(ws)

    async def _send_and_track(self, ws: WebSocket, payload: dict, error_list: list):
        try:
            await ws.send_json(payload)
        except:
            error_list.append(ws)

stream_manager = StreamManager()
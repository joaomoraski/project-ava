"""WebSocket endpoint and connection manager."""
from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger("api.ws")

router = APIRouter()


class WebSocketManager:
    """Manages all connected WebSocket clients.

    Distinguishes between 'avatar' clients (Electron) and 'dashboard' clients (Next.js).
    Avatar-only events (lipsync, animation, audio_chunk) are only sent to avatar clients.
    """

    def __init__(self) -> None:
        self._clients: dict[str, dict] = {}  # client_id -> {ws, client_type}

    async def connect(self, websocket: WebSocket, client_id: str, client_type: str = "dashboard") -> None:
        await websocket.accept()
        self._clients[client_id] = {"ws": websocket, "type": client_type}
        logger.info(f"WebSocket client connected: {client_id} ({client_type})")

    def disconnect(self, client_id: str) -> None:
        self._clients.pop(client_id, None)
        logger.info(f"WebSocket client disconnected: {client_id}")

    def has_avatar_client(self) -> bool:
        return any(c["type"] == "avatar" for c in self._clients.values())

    def client_count(self) -> int:
        return len(self._clients)

    async def broadcast(self, message: dict[str, Any], avatar_only: bool = False) -> None:
        """Send a message to all connected clients (or avatar-only clients)."""
        if not self._clients:
            return

        payload = json.dumps(message)
        disconnected: list[str] = []

        for client_id, client in list(self._clients.items()):
            if avatar_only and client["type"] != "avatar":
                continue
            try:
                await client["ws"].send_text(payload)
            except Exception:
                disconnected.append(client_id)

        for cid in disconnected:
            self.disconnect(cid)

    async def send_to_client(self, client_id: str, message: dict[str, Any]) -> bool:
        """Send a message to a specific client. Returns False if not found."""
        client = self._clients.get(client_id)
        if not client:
            return False
        try:
            await client["ws"].send_text(json.dumps(message))
            return True
        except Exception:
            self.disconnect(client_id)
            return False


# Global manager instance
ws_manager = WebSocketManager()


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, client_type: str = "dashboard") -> None:
    """
    WebSocket endpoint for real-time communication.

    Query params:
      client_type: "dashboard" (Next.js, default) | "avatar" (Electron)

    Events sent to all clients: status, mode_change, text_delta, text_complete,
                                 tts_stop, error, plugin_event
    Events sent to avatar only: audio_chunk, lipsync, animation
    """
    import uuid
    client_id = str(uuid.uuid4())

    await ws_manager.connect(websocket, client_id, client_type)

    # Send welcome message
    await ws_manager.send_to_client(client_id, {
        "type": "connected",
        "client_id": client_id,
        "client_type": client_type,
    })

    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                await _handle_client_message(client_id, msg)
            except json.JSONDecodeError:
                logger.warning(f"Invalid JSON from client {client_id}: {data[:100]}")
    except WebSocketDisconnect:
        ws_manager.disconnect(client_id)


async def _handle_client_message(client_id: str, msg: dict) -> None:
    """Handle incoming messages from WebSocket clients."""
    msg_type = msg.get("type")

    if msg_type == "ping":
        await ws_manager.send_to_client(client_id, {"type": "pong"})
    else:
        logger.debug(f"Received from {client_id}: {msg_type}")

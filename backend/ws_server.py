"""Standalone WebSocket server on port 8472.

Runs as a separate process alongside the FastAPI app.
Handles real-time audio streaming, status updates, and avatar events.

Run:
    python ws_server.py
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys

import websockets
from websockets.server import WebSocketServerProtocol

# Ensure the backend directory is on the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.config import settings
from core.logging import setup_logging

setup_logging(settings.log_level)
logger = logging.getLogger("ws_server")

# Connected clients: {id -> {ws, type}}
_clients: dict[str, dict] = {}


async def handler(websocket: WebSocketServerProtocol, path: str) -> None:
    import uuid
    client_id = str(uuid.uuid4())
    # Determine client type from query param
    query = websocket.request.path if hasattr(websocket, "request") else ""
    client_type = "avatar" if "avatar" in query else "dashboard"

    _clients[client_id] = {"ws": websocket, "type": client_type}
    logger.info(f"WS client connected: {client_id} ({client_type}) — total: {len(_clients)}")

    try:
        await websocket.send(json.dumps({
            "type": "connected",
            "client_id": client_id,
            "client_type": client_type,
        }))

        async for message in websocket:
            try:
                msg = json.loads(message)
                if msg.get("type") == "ping":
                    await websocket.send(json.dumps({"type": "pong"}))
            except json.JSONDecodeError:
                pass
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        _clients.pop(client_id, None)
        logger.info(f"WS client disconnected: {client_id} — total: {len(_clients)}")


async def broadcast(message: dict, avatar_only: bool = False) -> None:
    """Broadcast a message to all connected clients."""
    if not _clients:
        return
    payload = json.dumps(message)
    dead: list[str] = []
    for cid, client in list(_clients.items()):
        if avatar_only and client["type"] != "avatar":
            continue
        try:
            await client["ws"].send(payload)
        except Exception:
            dead.append(cid)
    for cid in dead:
        _clients.pop(cid, None)


def has_avatar_client() -> bool:
    return any(c["type"] == "avatar" for c in _clients.values())


async def main() -> None:
    host = "0.0.0.0"
    port = settings.ws_port
    logger.info(f"WebSocket server starting on ws://{host}:{port}/ws")
    async with websockets.serve(handler, host, port):
        await asyncio.Future()  # run forever


if __name__ == "__main__":
    asyncio.run(main())

"""WebSocket endpoint and connection manager.

Handles:
  - ping/pong keepalive
  - chat_message: text chat with streaming LLM response
  - status, mode_change, text_delta, text_complete broadcasts
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid as _uuid
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger("api.ws")

router = APIRouter()


class WebSocketManager:
    """Manages all connected WebSocket clients (dashboard/Next.js)."""

    def __init__(self) -> None:
        self._clients: dict[str, dict] = {}  # client_id -> {ws}

    async def connect(self, websocket: WebSocket, client_id: str) -> None:
        await websocket.accept()
        self._clients[client_id] = {"ws": websocket}
        logger.info(f"WebSocket client connected: {client_id}")

    def disconnect(self, client_id: str) -> None:
        self._clients.pop(client_id, None)
        logger.info(f"WebSocket client disconnected: {client_id}")

    def client_count(self) -> int:
        return len(self._clients)

    async def broadcast(self, message: dict[str, Any]) -> None:
        """Send a message to all connected clients."""
        if not self._clients:
            return

        payload = json.dumps(message)
        disconnected: list[str] = []

        for client_id, client in list(self._clients.items()):
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
async def websocket_endpoint(websocket: WebSocket) -> None:
    """
    WebSocket endpoint for real-time communication.

    Events: status, mode_change, text_delta, text_complete, tts_stop, error, plugin_event
    """
    import uuid
    client_id = str(uuid.uuid4())

    await ws_manager.connect(websocket, client_id)

    # Send welcome message
    await ws_manager.send_to_client(client_id, {
        "type": "connected",
        "client_id": client_id,
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

    elif msg_type == "chat_message":
        asyncio.create_task(_handle_chat_message(client_id, msg))

    else:
        logger.debug(f"Received from {client_id}: {msg_type}")


async def _handle_chat_message(client_id: str, msg: dict) -> None:
    """Process a text chat message: stream LLM response + persist to DB."""
    text = (msg.get("text") or "").strip()
    workspace = msg.get("workspace", "personal")
    session_id = msg.get("session_id")

    if not text:
        await ws_manager.send_to_client(client_id, {
            "type": "error", "service": "chat", "message": "Empty message",
        })
        return

    # Notify: thinking
    await ws_manager.send_to_client(client_id, {"type": "status", "status": "thinking"})

    try:
        from core.db.engine import async_session
        from core.memory.chat_history import ChatManager, ChatSession
        from core.llm.agent import AvaAgent
        from core.plugins.registry import plugin_registry
        from langchain_core.messages import HumanMessage, AIMessage

        # Get or create chat session
        async with async_session() as db:
            manager = ChatManager(workspace)
            chat = None
            if session_id:
                chat = await manager.get_session(db, session_id)

            if not chat:
                chat = await manager.create_session(db, title=text[:60])
                session_id = chat.session_id
                await ws_manager.send_to_client(client_id, {
                    "type": "session_created",
                    "session_id": session_id,
                    "workspace": workspace,
                    "title": chat.title,
                })

            # Persist user message
            await chat.append(db, "user", text)

        # Build agent with workspace tools
        ws_config = await _load_workspace_config(workspace)
        tools = plugin_registry.get_tools_for_workspace(ws_config)
        logger.info(f"Chat agent for '{workspace}': {len(tools)} tools loaded — {[t.name for t in tools]}")
        system_prompt = ws_config.get("system_prompt") or None
        agent = AvaAgent(tools=tools if tools else None, system_prompt=system_prompt)

        # Build context with summarization for long conversations
        from core.memory.summarizer import ConversationSummarizer
        from langchain_core.messages import SystemMessage

        all_msgs = chat.messages
        max_recent = 20

        history: list = []

        if len(all_msgs) > max_recent:
            older = all_msgs[:-max_recent]
            recent = all_msgs[-max_recent:]

            summarizer = ConversationSummarizer()
            try:
                from core.llm.provider import get_llm
                summarizer.set_llm(get_llm(streaming=False))
            except Exception:
                pass

            summary = await summarizer.summarize(older)
            if summary:
                history.append(SystemMessage(content=f"[Previous conversation summary]\n{summary}"))
        else:
            recent = all_msgs

        for m in recent:
            if m["role"] == "user":
                history.append(HumanMessage(content=m["content"]))
            elif m["role"] == "assistant":
                history.append(AIMessage(content=m["content"]))

        # Don't include the current message in history (it's the user_input)
        if history and isinstance(history[-1], HumanMessage) and history[-1].content == text:
            history = history[:-1]

        # Stream response
        full_response = []
        async for token in agent.astream_tokens(text, history=history):
            full_response.append(token)
            await ws_manager.send_to_client(client_id, {
                "type": "text_delta", "delta": token,
            })

        response_text = "".join(full_response)

        # Persist assistant message
        async with async_session() as db:
            manager = ChatManager(workspace)
            chat = await manager.get_session(db, session_id)
            if chat:
                await chat.append(db, "assistant", response_text)

        # Send complete
        await ws_manager.send_to_client(client_id, {
            "type": "text_complete",
            "text": response_text,
            "session_id": session_id,
        })

    except Exception as e:
        logger.error(f"Chat handler error: {e}", exc_info=True)
        await ws_manager.send_to_client(client_id, {
            "type": "error", "service": "chat", "message": str(e),
        })

    finally:
        await ws_manager.send_to_client(client_id, {"type": "status", "status": "idle"})


async def _load_workspace_config(workspace_name: str) -> dict:
    """Load workspace config from database (primary) or JSON file (fallback)."""
    try:
        from core.db.engine import async_session
        from core.workspace import load_config
        async with async_session() as session:
            config = await load_config(session, workspace_name)
            logger.debug(f"Loaded workspace '{workspace_name}' from DB: {len(config.get('tools_enabled', []))} tools")
            return config
    except Exception as e:
        logger.warning(f"Failed to load workspace '{workspace_name}' from DB: {e}. Trying JSON fallback.")

    import os
    config_path = os.path.join("workspaces", workspace_name, "config.json")
    if os.path.exists(config_path):
        with open(config_path) as f:
            return json.load(f)

    from core.workspace import DEFAULT_CONFIG
    return {**DEFAULT_CONFIG}

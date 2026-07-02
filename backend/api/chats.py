"""Chat history API endpoints.

GET  /api/chats                    — list sessions (optionally filtered by workspace)
GET  /api/chats/{session_id}       — get full session with messages
POST /api/chats/search             — full-text search across sessions
POST /api/chats/send               — send a text message and get streamed response
GET  /api/chats/{session_id}/context — get messages ready for LLM context
DELETE /api/chats/{session_id}     — delete a session
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_workspace
from api.schemas import ChatSearchRequest, OkResponse
from core.db.engine import get_session
from core.memory.chat_history import ChatManager, CrossWorkspaceChatManager

router = APIRouter(prefix="/api/chats", tags=["chats"])
logger = logging.getLogger("api.chats")


@router.get("")
async def list_chats(
    workspace: str = Depends(get_current_workspace),
    limit: int = Query(50, ge=1, le=200),
    all_workspaces: bool = Query(False),
    session: AsyncSession = Depends(get_session),
) -> dict:
    if all_workspaces:
        sessions = await CrossWorkspaceChatManager.list_all_sessions(session, limit=limit)
    else:
        manager = ChatManager(workspace)
        sessions = await manager.list_sessions(session, limit=limit)
    return {"sessions": sessions, "count": len(sessions)}


@router.get("/search")
async def search_chats_get(
    q: str = Query(..., min_length=1),
    workspace: str = Depends(get_current_workspace),
    all_workspaces: bool = Query(False),
    limit: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> dict:
    if all_workspaces:
        results = await CrossWorkspaceChatManager.search_all(session, q, limit=limit)
    else:
        manager = ChatManager(workspace)
        results = await manager.search(session, q, limit=limit)
    return {"results": results, "count": len(results), "query": q}


@router.post("/search")
async def search_chats(
    body: ChatSearchRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    if body.session_id:
        workspace = body.workspace or "personal"
        cs = await ChatManager(workspace).get_session(session, body.session_id)
        if not cs:
            raise HTTPException(status_code=404, detail=f"Session '{body.session_id}' not found")

        query_lower = body.query.lower()
        results = [
            {
                "session_id": cs.session_id,
                "session_title": cs.title,
                "workspace": cs.workspace,
                "role": msg["role"],
                "content": msg["content"],
                "timestamp": msg["timestamp"],
            }
            for msg in cs.messages
            if query_lower in msg.get("content", "").lower()
        ][:body.limit]
        return {"results": results, "count": len(results), "query": body.query}

    if body.workspace:
        manager = ChatManager(body.workspace)
        results = await manager.search(session, body.query, limit=body.limit)
    else:
        results = await CrossWorkspaceChatManager.search_all(session, body.query, limit=body.limit)
    return {"results": results, "count": len(results), "query": body.query}


class ChatSendRequest(BaseModel):
    text: str
    workspace: str = "personal"
    session_id: str | None = None


@router.post("/send")
async def send_chat_message(
    body: ChatSendRequest,
    session: AsyncSession = Depends(get_session),
):
    """Send a text message and stream the LLM response (SSE).

    If session_id is None, a new session is created.
    Response is streamed as `text/event-stream` with `data: {token}` lines.
    """
    from core.db.engine import async_session as get_async_session
    from core.llm.agent import AvaAgent
    from core.plugins.registry import plugin_registry
    from langchain_core.messages import HumanMessage, AIMessage
    import json as _json, os

    workspace = body.workspace
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Empty message")

    # Get or create session
    manager = ChatManager(workspace)
    chat = None
    session_id = body.session_id

    if session_id:
        chat = await manager.get_session(session, session_id)

    if not chat:
        chat = await manager.create_session(session, title=text[:60])
        session_id = chat.session_id

    # Persist user message
    await chat.append(session, "user", text)

    # Build agent
    config_path = os.path.join("workspaces", workspace, "config.json")
    ws_config = {}
    if os.path.exists(config_path):
        with open(config_path) as f:
            ws_config = _json.load(f)
    tools = plugin_registry.get_tools_for_workspace(ws_config)
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
            from core.llm.provider import get_llm as _get_llm
            summarizer.set_llm(_get_llm(streaming=False))
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
    if history and isinstance(history[-1], HumanMessage) and history[-1].content == text:
        history = history[:-1]

    async def stream_response():
        full = []
        yield f"data: {_json.dumps({'type': 'session', 'session_id': session_id})}\n\n"
        try:
            async for token in agent.astream_tokens(text, history=history):
                full.append(token)
                yield f"data: {_json.dumps({'type': 'token', 'content': token})}\n\n"
            response_text = "".join(full)
            # Persist assistant response
            async with get_async_session() as db2:
                mgr = ChatManager(workspace)
                cs = await mgr.get_session(db2, session_id)
                if cs:
                    await cs.append(db2, "assistant", response_text)
            yield f"data: {_json.dumps({'type': 'done', 'text': response_text})}\n\n"
        except Exception as e:
            logger.error(f"Chat stream error: {e}", exc_info=True)
            yield f"data: {_json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(stream_response(), media_type="text/event-stream")


@router.get("/{session_id}")
async def get_chat(
    session_id: str,
    workspace: str = Depends(get_current_workspace),
    all_workspaces: bool = Query(False),
    session: AsyncSession = Depends(get_session),
) -> dict:
    workspaces_to_try = (
        await CrossWorkspaceChatManager.list_all_workspaces(session)
        if all_workspaces
        else [workspace]
    )
    for ws in workspaces_to_try:
        cs = await ChatManager(ws).get_session(session, session_id)
        if cs:
            return {
                "session_id": cs.session_id,
                "workspace": cs.workspace,
                "title": cs.title,
                "created_at": cs.created_at,
                "updated_at": cs.updated_at,
                "messages": cs.messages,
                "message_count": len(cs),
            }
    raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")


@router.get("/{session_id}/context")
async def get_chat_context(
    session_id: str,
    workspace: str = Depends(get_current_workspace),
    all_workspaces: bool = Query(False),
    max_recent: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> dict:
    workspaces_to_try = (
        await CrossWorkspaceChatManager.list_all_workspaces(session)
        if all_workspaces
        else [workspace]
    )
    for ws in workspaces_to_try:
        cs = await ChatManager(ws).get_session(session, session_id)
        if cs:
            return {
                "session_id": session_id,
                "context_messages": cs.get_context_messages(max_recent=max_recent),
                "total_messages": len(cs),
                "showing_last": min(max_recent, len(cs)),
            }
    raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")


@router.delete("/{session_id}")
async def delete_chat(
    session_id: str,
    workspace: str = Depends(get_current_workspace),
    all_workspaces: bool = Query(False),
    session: AsyncSession = Depends(get_session),
) -> OkResponse:
    workspaces_to_try = (
        await CrossWorkspaceChatManager.list_all_workspaces(session)
        if all_workspaces
        else [workspace]
    )
    for ws in workspaces_to_try:
        cs = await ChatManager(ws).get_session(session, session_id)
        if cs:
            await ChatManager(ws).delete_session(session, session_id)
            return OkResponse(message=f"Session '{session_id}' deleted.")
    raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")

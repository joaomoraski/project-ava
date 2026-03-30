"""Chat history API endpoints.

GET  /api/chats                    — list sessions (optionally filtered by workspace)
GET  /api/chats/{session_id}       — get full session with messages
POST /api/chats/search             — full-text search across sessions
GET  /api/chats/{session_id}/context — get messages ready for LLM context
DELETE /api/chats/{session_id}     — delete a session
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from api.schemas import ChatSearchRequest, OkResponse
from core.memory.chat_history import ChatManager, CrossWorkspaceChatManager

router = APIRouter(prefix="/api/chats", tags=["chats"])
logger = logging.getLogger("api.chats")


@router.get("")
async def list_chats(
    workspace: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    all_workspaces: bool = Query(False),
) -> dict:
    """List chat sessions, newest first.

    - workspace=<name>: filter to a specific workspace
    - all_workspaces=true: list across all workspaces
    - default: lists from all workspaces
    """
    if all_workspaces or workspace is None:
        sessions = CrossWorkspaceChatManager.list_all_sessions(limit=limit)
    else:
        manager = ChatManager(workspace)
        sessions = manager.list_sessions(limit=limit)

    return {"sessions": sessions, "count": len(sessions)}


@router.get("/search")
async def search_chats_get(
    q: str = Query(..., min_length=1),
    workspace: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
) -> dict:
    """Search chat history by keyword (GET variant for simple queries)."""
    if workspace:
        manager = ChatManager(workspace)
        results = manager.search(q, limit=limit)
    else:
        results = CrossWorkspaceChatManager.search_all(q, limit=limit)

    return {"results": results, "count": len(results), "query": q}


@router.post("/search")
async def search_chats(body: ChatSearchRequest) -> dict:
    """Full-text search across chat sessions."""
    if body.session_id:
        # Search within a specific session
        workspace = body.workspace or "personal"
        session = ChatManager(workspace).get_session(body.session_id)
        if not session:
            raise HTTPException(status_code=404, detail=f"Session '{body.session_id}' not found")

        query_lower = body.query.lower()
        results = [
            {
                "session_id": session.session_id,
                "session_title": session.title,
                "workspace": session.workspace,
                "role": msg["role"],
                "content": msg["content"],
                "timestamp": msg["timestamp"],
            }
            for msg in session.messages
            if query_lower in msg.get("content", "").lower()
        ][:body.limit]
        return {"results": results, "count": len(results), "query": body.query}

    if body.workspace:
        manager = ChatManager(body.workspace)
        results = manager.search(body.query, limit=body.limit)
    else:
        results = CrossWorkspaceChatManager.search_all(body.query, limit=body.limit)

    return {"results": results, "count": len(results), "query": body.query}


@router.get("/{session_id}")
async def get_chat(
    session_id: str,
    workspace: str | None = Query(None),
) -> dict:
    """Get a full session with all messages."""
    # Try to find session — check workspace hint first, then search all
    workspaces_to_try = [workspace] if workspace else CrossWorkspaceChatManager.list_all_workspaces()

    for ws in workspaces_to_try:
        session = ChatManager(ws).get_session(session_id)
        if session:
            return {
                "session_id": session.session_id,
                "workspace": session.workspace,
                "title": session.title,
                "created_at": session.created_at,
                "updated_at": session.updated_at,
                "messages": session.messages,
                "message_count": len(session),
            }

    raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")


@router.get("/{session_id}/context")
async def get_chat_context(
    session_id: str,
    workspace: str | None = Query(None),
    max_recent: int = Query(20, ge=1, le=100),
) -> dict:
    """Get messages formatted for LLM context (last N messages only)."""
    workspaces_to_try = [workspace] if workspace else CrossWorkspaceChatManager.list_all_workspaces()

    for ws in workspaces_to_try:
        session = ChatManager(ws).get_session(session_id)
        if session:
            return {
                "session_id": session_id,
                "context_messages": session.get_context_messages(max_recent=max_recent),
                "total_messages": len(session),
                "showing_last": min(max_recent, len(session)),
            }

    raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")


@router.delete("/{session_id}")
async def delete_chat(
    session_id: str,
    workspace: str | None = Query(None),
) -> OkResponse:
    """Delete a chat session."""
    workspaces_to_try = [workspace] if workspace else CrossWorkspaceChatManager.list_all_workspaces()

    for ws in workspaces_to_try:
        manager = ChatManager(ws)
        session = manager.get_session(session_id)
        if session:
            manager.delete_session(session_id)
            return OkResponse(message=f"Session '{session_id}' deleted.")

    raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")

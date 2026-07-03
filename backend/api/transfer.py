"""Cross-workspace item transfer (move or copy).

POST /api/transfer — move or copy an item to another workspace.

Supported item types:
  - todo, note, action_item, alert  (simple single-row items)
  - meeting       — clones Meeting row + related KnowledgeChunk rows
  - chat_session  — clones ChatSession + all ChatMessage children
  - context       — clones Context shell + ContextLink children (auto_linked=False only)
  - knowledge_source — copies all KnowledgeChunk rows sharing the same source string
"""
from __future__ import annotations

import uuid
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, inspect, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.engine import get_session
from core.db.models import (
    Workspace, Todo, Note, ActionItem, Alert,
    Meeting, ChatSession, ChatMessage, Context, ContextLink, KnowledgeChunk,
)

router = APIRouter(prefix="/api/transfer", tags=["transfer"])
logger = logging.getLogger("api.transfer")

# Map of simple (single-row) transferable item types to their ORM model
TRANSFERABLE = {
    "todo": Todo,
    "note": Note,
    "action_item": ActionItem,
    "alert": Alert,
    "meeting": Meeting,
    "chat_session": ChatSession,
    "context": Context,
    "knowledge_source": None,  # handled separately — key on source string, not id
}

# Columns to skip when copying (new item gets fresh values)
SKIP_ON_COPY = {"id", "created_at", "updated_at", "completed_at", "fired_at"}


class TransferRequest(BaseModel):
    item_type: str
    item_id: str  # for knowledge_source this is the source string, not a UUID
    target_workspace: str
    mode: str = "move"  # "move" or "copy"


class TransferResponse(BaseModel):
    ok: bool
    message: str
    new_id: str | None = None  # only set on copy
    warnings: list[str] = []


def _clone_item(model: Any, source: Any, target_ws_id: uuid.UUID) -> Any:
    """Create a shallow copy of an ORM item with a new id and workspace."""
    mapper = inspect(model)
    data: dict[str, Any] = {}
    for col in mapper.columns:
        key = col.key
        if key in SKIP_ON_COPY:
            continue
        if key == "workspace_id":
            data[key] = target_ws_id
        else:
            data[key] = getattr(source, key)
    return model(**data)


async def _transfer_meeting(
    session: AsyncSession,
    item_id: uuid.UUID,
    target_ws_id: uuid.UUID,
    mode: str,
) -> TransferResponse:
    result = await session.execute(select(Meeting).where(Meeting.id == item_id))
    meeting: Meeting | None = result.scalar_one_or_none()
    if meeting is None:
        raise HTTPException(status_code=404, detail=f"meeting '{item_id}' not found.")

    if mode == "move":
        if meeting.workspace_id == target_ws_id:
            return TransferResponse(ok=True, message="Item already in target workspace.")
        old_ws_id = meeting.workspace_id
        meeting.workspace_id = target_ws_id
        # Update related knowledge chunks
        await session.execute(
            update(KnowledgeChunk)
            .where(
                KnowledgeChunk.workspace_id == old_ws_id,
                KnowledgeChunk.source == f"meeting:{item_id}",
            )
            .values(workspace_id=target_ws_id)
        )
        await session.commit()
        logger.info("Moved meeting %s to workspace %s", item_id, target_ws_id)
        return TransferResponse(ok=True, message="meeting moved.")

    # copy
    new_meeting = _clone_item(Meeting, meeting, target_ws_id)
    session.add(new_meeting)
    await session.flush()  # get new id

    # Clone related knowledge chunks
    chunks_result = await session.execute(
        select(KnowledgeChunk).where(
            KnowledgeChunk.workspace_id == meeting.workspace_id,
            KnowledgeChunk.source == f"meeting:{item_id}",
        )
    )
    chunks = chunks_result.scalars().all()
    new_source = f"meeting:{new_meeting.id}"
    for chunk in chunks:
        new_chunk = _clone_item(KnowledgeChunk, chunk, target_ws_id)
        new_chunk.source = new_source
        session.add(new_chunk)

    await session.commit()
    logger.info("Copied meeting %s to %s (new id %s, %d chunks)", item_id, target_ws_id, new_meeting.id, len(chunks))
    return TransferResponse(ok=True, message="meeting copied.", new_id=str(new_meeting.id))


async def _transfer_chat_session(
    session: AsyncSession,
    item_id: uuid.UUID,
    target_ws_id: uuid.UUID,
    mode: str,
) -> TransferResponse:
    result = await session.execute(select(ChatSession).where(ChatSession.id == item_id))
    chat: ChatSession | None = result.scalar_one_or_none()
    if chat is None:
        raise HTTPException(status_code=404, detail=f"chat_session '{item_id}' not found.")

    if mode == "move":
        if chat.workspace_id == target_ws_id:
            return TransferResponse(ok=True, message="Item already in target workspace.")
        chat.workspace_id = target_ws_id
        await session.commit()
        logger.info("Moved chat_session %s to workspace %s", item_id, target_ws_id)
        return TransferResponse(ok=True, message="chat_session moved.")

    # copy session
    new_session = _clone_item(ChatSession, chat, target_ws_id)
    session.add(new_session)
    await session.flush()

    # copy messages — preserve role/content/order; original timestamps kept
    msgs_result = await session.execute(
        select(ChatMessage)
        .where(ChatMessage.session_id == item_id)
        .order_by(ChatMessage.created_at)
    )
    messages = msgs_result.scalars().all()
    for msg in messages:
        new_msg = ChatMessage(
            session_id=new_session.id,
            role=msg.role,
            content=msg.content,
            created_at=msg.created_at,
        )
        session.add(new_msg)

    await session.commit()
    logger.info("Copied chat_session %s to %s (new id %s, %d messages)", item_id, target_ws_id, new_session.id, len(messages))
    return TransferResponse(ok=True, message="chat_session copied.", new_id=str(new_session.id))


async def _transfer_context(
    session: AsyncSession,
    item_id: uuid.UUID,
    target_ws_id: uuid.UUID,
    mode: str,
) -> TransferResponse:
    result = await session.execute(select(Context).where(Context.id == item_id))
    ctx: Context | None = result.scalar_one_or_none()
    if ctx is None:
        raise HTTPException(status_code=404, detail=f"context '{item_id}' not found.")

    if mode == "move":
        if ctx.workspace_id == target_ws_id:
            return TransferResponse(ok=True, message="Item already in target workspace.")
        ctx.workspace_id = target_ws_id
        await session.commit()
        logger.info("Moved context %s to workspace %s", item_id, target_ws_id)
        return TransferResponse(ok=True, message="context moved.")

    # copy context shell
    new_ctx = _clone_item(Context, ctx, target_ws_id)
    session.add(new_ctx)
    await session.flush()

    # copy manual links only (auto_linked=False)
    links_result = await session.execute(
        select(ContextLink).where(
            ContextLink.context_id == item_id,
            ContextLink.auto_linked == False,  # noqa: E712
        )
    )
    links = links_result.scalars().all()
    for link in links:
        new_link = ContextLink(
            context_id=new_ctx.id,
            item_type=link.item_type,
            item_id=link.item_id,
            auto_linked=False,
            created_at=link.created_at,
        )
        session.add(new_link)

    await session.commit()
    warnings: list[str] = []
    if links:
        warnings.append(
            f"{len(links)} link(s) copied — linked items may not exist in the target workspace "
            "and will resolve to nothing until added there."
        )
    logger.info("Copied context %s to %s (new id %s, %d links)", item_id, target_ws_id, new_ctx.id, len(links))
    return TransferResponse(
        ok=True,
        message="context copied.",
        new_id=str(new_ctx.id),
        warnings=warnings,
    )


async def _transfer_knowledge_source(
    session: AsyncSession,
    source_string: str,
    target_ws_id: uuid.UUID,
    mode: str,
) -> TransferResponse:
    chunks_result = await session.execute(
        select(KnowledgeChunk).where(KnowledgeChunk.source == source_string)
    )
    chunks = chunks_result.scalars().all()
    if not chunks:
        raise HTTPException(status_code=404, detail=f"knowledge_source '{source_string}' not found.")

    if mode == "move":
        src_ws_id = chunks[0].workspace_id
        if src_ws_id == target_ws_id:
            return TransferResponse(ok=True, message="Item already in target workspace.")
        await session.execute(
            update(KnowledgeChunk)
            .where(KnowledgeChunk.source == source_string)
            .values(workspace_id=target_ws_id)
        )
        await session.commit()
        logger.info("Moved knowledge_source '%s' (%d chunks) to workspace %s", source_string, len(chunks), target_ws_id)
        return TransferResponse(ok=True, message=f"knowledge_source moved ({len(chunks)} chunks).")

    # copy — new chunk ids, same source string, target workspace
    for chunk in chunks:
        new_chunk = _clone_item(KnowledgeChunk, chunk, target_ws_id)
        session.add(new_chunk)
    await session.commit()
    logger.info("Copied knowledge_source '%s' (%d chunks) to workspace %s", source_string, len(chunks), target_ws_id)
    return TransferResponse(ok=True, message=f"knowledge_source copied ({len(chunks)} chunks).")


@router.post("")
async def transfer_item(
    body: TransferRequest,
    session: AsyncSession = Depends(get_session),
) -> TransferResponse:
    """Move or copy an item to a different workspace.

    item_type must be one of: todo, note, action_item, alert, meeting,
    chat_session, context, knowledge_source.

    For knowledge_source, item_id is the source string (not a UUID).
    """
    if body.item_type not in TRANSFERABLE:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid item_type '{body.item_type}'. Valid: {list(TRANSFERABLE.keys())}",
        )

    if body.mode not in ("move", "copy"):
        raise HTTPException(status_code=422, detail="mode must be 'move' or 'copy'.")

    # Resolve target workspace
    result = await session.execute(
        select(Workspace.id).where(Workspace.name == body.target_workspace)
    )
    target_ws_id = result.scalar_one_or_none()
    if target_ws_id is None:
        raise HTTPException(
            status_code=404,
            detail=f"Workspace '{body.target_workspace}' not found.",
        )

    # --- Complex types with their own handlers ---
    if body.item_type == "knowledge_source":
        return await _transfer_knowledge_source(session, body.item_id, target_ws_id, body.mode)

    # Parse UUID for all other types
    try:
        item_uuid = uuid.UUID(body.item_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid item_id format.")

    if body.item_type == "meeting":
        return await _transfer_meeting(session, item_uuid, target_ws_id, body.mode)
    if body.item_type == "chat_session":
        return await _transfer_chat_session(session, item_uuid, target_ws_id, body.mode)
    if body.item_type == "context":
        return await _transfer_context(session, item_uuid, target_ws_id, body.mode)

    # --- Simple single-row types (todo, note, action_item, alert) ---
    model = TRANSFERABLE[body.item_type]
    result = await session.execute(
        select(model).where(model.id == item_uuid)
    )
    item = result.scalar_one_or_none()
    if item is None:
        raise HTTPException(
            status_code=404,
            detail=f"{body.item_type} '{body.item_id}' not found.",
        )

    if body.mode == "copy":
        new_item = _clone_item(model, item, target_ws_id)
        session.add(new_item)
        await session.commit()
        await session.refresh(new_item)
        logger.info(
            "Copied %s %s to '%s' as %s", body.item_type, body.item_id, body.target_workspace, new_item.id
        )
        return TransferResponse(
            ok=True,
            message=f"{body.item_type} copied to '{body.target_workspace}'.",
            new_id=str(new_item.id),
        )
    else:
        if item.workspace_id == target_ws_id:
            return TransferResponse(ok=True, message="Item already in target workspace.")
        item.workspace_id = target_ws_id
        await session.commit()
        logger.info(
            "Moved %s %s to '%s'", body.item_type, body.item_id, body.target_workspace
        )
        return TransferResponse(
            ok=True,
            message=f"{body.item_type} moved to '{body.target_workspace}'.",
        )

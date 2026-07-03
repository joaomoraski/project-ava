"""Contexts CRUD API endpoints.

GET    /api/contexts              — list contexts for a workspace
POST   /api/contexts              — create context
PUT    /api/contexts/{context_id} — update context
DELETE /api/contexts/{context_id} — delete context + all links
POST   /api/contexts/{context_id}/link   — link an item to this context
DELETE /api/contexts/{context_id}/link   — unlink an item from this context
GET    /api/contexts/{context_id}/items  — get all items linked to this context
GET    /api/contexts/by-name/{name}      — lookup by name + return bundle
GET    /api/contexts/{context_id}/bundle — return full content of all linked items
"""
from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_workspace
from api.schemas import (
    ContextCreate,
    ContextUpdate,
    ContextResponse,
    ContextLinkRequest,
    ContextLinkResponse,
    OkResponse,
)
from core.db.engine import get_session
from core.db.models import (
    Context,
    ContextLink,
    Workspace,
    Meeting,
    Note,
    Todo,
    ActionItem,
)

router = APIRouter(prefix="/api/contexts", tags=["contexts"])
logger = logging.getLogger("api.contexts")

VALID_ITEM_TYPES = {"meeting", "note", "todo", "alert", "action_item"}


async def _resolve_workspace_id(session: AsyncSession, workspace_name: str) -> uuid.UUID | None:
    result = await session.execute(
        select(Workspace.id).where(Workspace.name == workspace_name)
    )
    return result.scalar_one_or_none()


def _context_to_response(ctx: Context) -> ContextResponse:
    return ContextResponse(
        id=str(ctx.id),
        workspace_id=str(ctx.workspace_id),
        name=ctx.name,
        description=ctx.description or "",
        color=ctx.color or "#6366f1",
        created_at=ctx.created_at.isoformat() if ctx.created_at else "",
    )


def _link_to_response(link: ContextLink) -> ContextLinkResponse:
    return ContextLinkResponse(
        id=str(link.id),
        context_id=str(link.context_id),
        item_type=link.item_type,
        item_id=str(link.item_id),
        auto_linked=bool(link.auto_linked),
        created_at=link.created_at.isoformat() if link.created_at else "",
    )


async def _get_context_or_404(session: AsyncSession, context_id: str) -> Context:
    try:
        uid = uuid.UUID(context_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid context ID format.")

    result = await session.execute(select(Context).where(Context.id == uid))
    ctx = result.scalar_one_or_none()
    if ctx is None:
        raise HTTPException(status_code=404, detail=f"Context '{context_id}' not found.")
    return ctx


# ─── CRUD ────────────────────────────────────────────────────────────────────


@router.get("")
async def list_contexts(
    workspace: str = Depends(get_current_workspace),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> dict:
    workspace_id = await _resolve_workspace_id(session, workspace)
    if workspace_id is None:
        raise HTTPException(status_code=404, detail=f"Workspace '{workspace}' not found.")

    stmt = (
        select(Context)
        .where(Context.workspace_id == workspace_id)
        .order_by(Context.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    result = await session.execute(stmt)
    contexts = result.scalars().all()

    return {
        "contexts": [_context_to_response(c).model_dump() for c in contexts],
        "count": len(contexts),
    }


@router.post("", status_code=201)
async def create_context(
    body: ContextCreate,
    session: AsyncSession = Depends(get_session),
) -> ContextResponse:
    workspace_id = await _resolve_workspace_id(session, body.workspace)
    if workspace_id is None:
        raise HTTPException(status_code=404, detail=f"Workspace '{body.workspace}' not found.")

    ctx = Context(
        workspace_id=workspace_id,
        name=body.name,
        description=body.description,
        color=body.color,
    )
    session.add(ctx)
    await session.commit()
    await session.refresh(ctx)
    logger.info(f"Created context '{ctx.name}' (id={ctx.id}) in workspace '{body.workspace}'.")

    return _context_to_response(ctx)


@router.get("/{context_id}")
async def get_context(
    context_id: str,
    workspace: str = Depends(get_current_workspace),
    session: AsyncSession = Depends(get_session),
) -> ContextResponse:
    ctx = await _get_context_or_404(session, context_id)
    ws_id = await _resolve_workspace_id(session, workspace)
    if ws_id is None or ctx.workspace_id != ws_id:
        raise HTTPException(status_code=404, detail=f"Context '{context_id}' not found.")
    return _context_to_response(ctx)


@router.put("/{context_id}")
async def update_context(
    context_id: str,
    body: ContextUpdate,
    workspace: str = Depends(get_current_workspace),
    session: AsyncSession = Depends(get_session),
) -> ContextResponse:
    ctx = await _get_context_or_404(session, context_id)
    ws_id = await _resolve_workspace_id(session, workspace)
    if ws_id is None or ctx.workspace_id != ws_id:
        raise HTTPException(status_code=404, detail=f"Context '{context_id}' not found.")

    if body.name is not None:
        ctx.name = body.name
    if body.description is not None:
        ctx.description = body.description
    if body.color is not None:
        ctx.color = body.color

    await session.commit()
    await session.refresh(ctx)
    logger.info(f"Updated context '{ctx.name}' (id={ctx.id}).")

    return _context_to_response(ctx)


@router.delete("/{context_id}")
async def delete_context(
    context_id: str,
    workspace: str = Depends(get_current_workspace),
    session: AsyncSession = Depends(get_session),
) -> OkResponse:
    ctx = await _get_context_or_404(session, context_id)
    ws_id = await _resolve_workspace_id(session, workspace)
    if ws_id is None or ctx.workspace_id != ws_id:
        raise HTTPException(status_code=404, detail=f"Context '{context_id}' not found.")

    await session.delete(ctx)
    await session.commit()
    logger.info(f"Deleted context {context_id} (cascade deletes links).")

    return OkResponse(message=f"Context '{context_id}' deleted.")


# ─── Links ───────────────────────────────────────────────────────────────────


@router.post("/{context_id}/link", status_code=201)
async def link_item(
    context_id: str,
    body: ContextLinkRequest,
    workspace: str = Depends(get_current_workspace),
    session: AsyncSession = Depends(get_session),
) -> ContextLinkResponse:
    ctx = await _get_context_or_404(session, context_id)
    ws_id = await _resolve_workspace_id(session, workspace)
    if ws_id is None or ctx.workspace_id != ws_id:
        raise HTTPException(status_code=404, detail=f"Context '{context_id}' not found.")

    if body.item_type not in VALID_ITEM_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid item_type '{body.item_type}'. Must be one of: {', '.join(sorted(VALID_ITEM_TYPES))}",
        )

    try:
        item_uid = uuid.UUID(body.item_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid item_id format.")

    # Check for duplicate link
    existing = await session.execute(
        select(ContextLink).where(
            and_(
                ContextLink.context_id == ctx.id,
                ContextLink.item_type == body.item_type,
                ContextLink.item_id == item_uid,
            )
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Item is already linked to this context.")

    link = ContextLink(
        context_id=ctx.id,
        item_type=body.item_type,
        item_id=item_uid,
    )
    session.add(link)
    await session.commit()
    await session.refresh(link)
    logger.info(f"Linked {body.item_type} {body.item_id} to context {context_id}.")

    return _link_to_response(link)


@router.delete("/{context_id}/link")
async def unlink_item(
    context_id: str,
    body: ContextLinkRequest,
    workspace: str = Depends(get_current_workspace),
    session: AsyncSession = Depends(get_session),
) -> OkResponse:
    ctx = await _get_context_or_404(session, context_id)
    ws_id = await _resolve_workspace_id(session, workspace)
    if ws_id is None or ctx.workspace_id != ws_id:
        raise HTTPException(status_code=404, detail=f"Context '{context_id}' not found.")

    try:
        item_uid = uuid.UUID(body.item_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid item_id format.")

    result = await session.execute(
        select(ContextLink).where(
            and_(
                ContextLink.context_id == ctx.id,
                ContextLink.item_type == body.item_type,
                ContextLink.item_id == item_uid,
            )
        )
    )
    link = result.scalar_one_or_none()
    if link is None:
        raise HTTPException(status_code=404, detail="Link not found.")

    await session.delete(link)
    await session.commit()
    logger.info(f"Unlinked {body.item_type} {body.item_id} from context {context_id}.")

    return OkResponse(message="Item unlinked from context.")


@router.get("/by-name/{name}")
async def get_context_by_name(
    name: str,
    workspace: str = Depends(get_current_workspace),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Case-insensitive lookup by name scoped to workspace. Returns context + bundle."""
    workspace_id = await _resolve_workspace_id(session, workspace)
    if workspace_id is None:
        raise HTTPException(status_code=404, detail=f"Workspace '{workspace}' not found.")

    result = await session.execute(
        select(Context).where(
            func.lower(Context.name) == name.lower(),
            Context.workspace_id == workspace_id,
        )
    )
    ctx = result.scalar_one_or_none()
    if ctx is None:
        raise HTTPException(status_code=404, detail=f"Context '{name}' not found in workspace '{workspace}'.")

    bundle = await _build_bundle(session, ctx)
    return {"context": _context_to_response(ctx).model_dump(), "bundle": bundle}


@router.get("/{context_id}/bundle")
async def get_context_bundle(
    context_id: str,
    workspace: str = Depends(get_current_workspace),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Return full content of every linked item, formatted."""
    ctx = await _get_context_or_404(session, context_id)
    ws_id = await _resolve_workspace_id(session, workspace)
    if ws_id is None or ctx.workspace_id != ws_id:
        raise HTTPException(status_code=404, detail=f"Context '{context_id}' not found.")
    bundle = await _build_bundle(session, ctx)
    return {"context": _context_to_response(ctx).model_dump(), "items": bundle["items"]}


async def _build_bundle(session: AsyncSession, ctx: Context) -> dict:
    """Fetch and format all items linked to a context."""
    links_result = await session.execute(
        select(ContextLink).where(ContextLink.context_id == ctx.id)
    )
    links = links_result.scalars().all()

    items: list[dict] = []
    for link in links:
        item_uid = link.item_id
        item_type = link.item_type
        row: dict | None = None

        if item_type == "meeting":
            res = await session.execute(select(Meeting).where(Meeting.id == item_uid))
            obj = res.scalar_one_or_none()
            if obj:
                row = {
                    "type": "meeting",
                    "id": str(obj.id),
                    "title": obj.title or "",
                    "started_at": obj.started_at.isoformat() if obj.started_at else None,
                    "summary": obj.summary or "",
                    "transcript": obj.transcript or "",
                    "participants": obj.participants or [],
                }

        elif item_type == "note":
            res = await session.execute(select(Note).where(Note.id == item_uid))
            obj = res.scalar_one_or_none()
            if obj:
                row = {
                    "type": "note",
                    "id": str(obj.id),
                    "title": obj.title,
                    "content": obj.content or "",
                    "tags": obj.tags or [],
                }

        elif item_type == "todo":
            res = await session.execute(select(Todo).where(Todo.id == item_uid))
            obj = res.scalar_one_or_none()
            if obj:
                row = {
                    "type": "todo",
                    "id": str(obj.id),
                    "title": obj.title,
                    "description": obj.description or "",
                    "status": obj.status,
                    "priority": obj.priority,
                    "due_date": obj.due_date.isoformat() if obj.due_date else None,
                }

        elif item_type == "action_item":
            res = await session.execute(select(ActionItem).where(ActionItem.id == item_uid))
            obj = res.scalar_one_or_none()
            if obj:
                row = {
                    "type": "action_item",
                    "id": str(obj.id),
                    "description": obj.description,
                    "owner": obj.owner or "",
                    "due_date": obj.due_date.isoformat() if obj.due_date else None,
                    "status": obj.status,
                }

        if row is not None:
            items.append(row)

    return {"items": items, "count": len(items)}


@router.get("/{context_id}/items")
async def get_context_items(
    context_id: str,
    workspace: str = Depends(get_current_workspace),
    item_type: str | None = Query(None, description="Filter by item type"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> dict:
    ctx = await _get_context_or_404(session, context_id)
    ws_id = await _resolve_workspace_id(session, workspace)
    if ws_id is None or ctx.workspace_id != ws_id:
        raise HTTPException(status_code=404, detail=f"Context '{context_id}' not found.")

    stmt = select(ContextLink).where(ContextLink.context_id == ctx.id)

    if item_type is not None:
        if item_type not in VALID_ITEM_TYPES:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid item_type '{item_type}'. Must be one of: {', '.join(sorted(VALID_ITEM_TYPES))}",
            )
        stmt = stmt.where(ContextLink.item_type == item_type)

    stmt = stmt.order_by(ContextLink.created_at.desc()).offset(offset).limit(limit)
    result = await session.execute(stmt)
    links = result.scalars().all()

    return {
        "context_id": str(ctx.id),
        "items": [_link_to_response(l).model_dump() for l in links],
        "count": len(links),
    }

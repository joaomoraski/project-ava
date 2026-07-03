"""Action item tracking across meetings."""
from __future__ import annotations

import uuid
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_workspace, resolve_workspace_id
from api.schemas import ActionItemCreate, ActionItemUpdate, ActionItemResponse, OkResponse
from core.db.engine import get_session
from core.db.models import ActionItem, Meeting, Workspace

logger = logging.getLogger("api.action_items")
router = APIRouter(prefix="/api/action-items", tags=["action-items"])


def _item_to_dict(a: ActionItem, meeting_title: str | None = None) -> dict:
    return {
        "id": str(a.id),
        "meeting_id": str(a.meeting_id) if a.meeting_id else None,
        "meeting_title": meeting_title,
        "workspace_id": str(a.workspace_id),
        "owner": a.owner,
        "description": a.description,
        "status": a.status or "pending",
        "due_date": a.due_date.isoformat() if a.due_date else None,
        "completed_at": a.completed_at.isoformat() if a.completed_at else None,
        "linked_item_id": str(a.linked_item_id) if a.linked_item_id else None,
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }


# ─── List ─────────────────────────────────────────────────────────────────────

@router.get("")
async def list_action_items(
    workspace: str = Depends(get_current_workspace),
    status: str | None = Query(None),
    owner: str | None = Query(None),
    meeting_id: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> dict:
    stmt = select(ActionItem)
    result = await session.execute(
        select(Workspace.id).where(Workspace.name == workspace)
    )
    ws_id = result.scalar_one_or_none()
    if ws_id is None:
        return {"action_items": [], "count": 0}
    stmt = stmt.where(ActionItem.workspace_id == ws_id)
    if status:
        stmt = stmt.where(ActionItem.status == status)
    if owner:
        stmt = stmt.where(ActionItem.owner == owner)
    if meeting_id:
        stmt = stmt.where(ActionItem.meeting_id == uuid.UUID(meeting_id))
    stmt = stmt.order_by(ActionItem.created_at.desc()).offset(offset).limit(limit)
    result = await session.execute(stmt)
    items = result.scalars().all()

    # Batch-fetch meeting titles for items that have meeting_id
    meeting_ids = {a.meeting_id for a in items if a.meeting_id}
    meeting_titles: dict[str, str] = {}
    if meeting_ids:
        mt_result = await session.execute(
            select(Meeting.id, Meeting.title).where(Meeting.id.in_(meeting_ids))
        )
        for mid, title in mt_result.all():
            meeting_titles[str(mid)] = title or "Untitled Meeting"

    return {
        "action_items": [
            _item_to_dict(a, meeting_titles.get(str(a.meeting_id)) if a.meeting_id else None)
            for a in items
        ],
        "count": len(items),
    }


# ─── Create ───────────────────────────────────────────────────────────────────

@router.post("")
async def create_action_item(
    body: ActionItemCreate,
    session: AsyncSession = Depends(get_session),
) -> dict:
    result = await session.execute(
        select(Workspace.id).where(Workspace.name == body.workspace)
    )
    ws_id = result.scalar_one_or_none()
    if ws_id is None:
        raise HTTPException(status_code=404, detail=f"Workspace '{body.workspace}' not found.")

    due_dt: datetime | None = None
    if body.due_date:
        try:
            due_dt = datetime.fromisoformat(body.due_date)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"Invalid due_date format: '{body.due_date}'.")

    item = ActionItem(
        workspace_id=ws_id,
        meeting_id=uuid.UUID(body.meeting_id) if body.meeting_id else None,
        owner=body.owner,
        description=body.description,
        status="pending",
        due_date=due_dt,
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)

    # Enqueue auto-categorization
    try:
        from core.jobs.tasks.contexts import auto_categorize_item
        await auto_categorize_item.defer_async(
            target_type="action_item",
            target_id=str(item.id),
            workspace_id=str(ws_id),
        )
    except Exception as exc:
        logger.warning("auto_categorize enqueue failed (non-fatal): %s", exc)

    return _item_to_dict(item)


# ─── Get one ──────────────────────────────────────────────────────────────────

@router.get("/{item_id}")
async def get_action_item(
    item_id: str,
    workspace: str = Depends(get_current_workspace),
    session: AsyncSession = Depends(get_session),
) -> dict:
    result = await session.execute(
        select(ActionItem).where(ActionItem.id == uuid.UUID(item_id))
    )
    item = result.scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail=f"Action item '{item_id}' not found.")
    ws_id = await resolve_workspace_id(session, workspace)
    if item.workspace_id != ws_id:
        raise HTTPException(status_code=404, detail=f"Action item '{item_id}' not found.")
    return _item_to_dict(item)


# ─── Update ───────────────────────────────────────────────────────────────────

@router.put("/{item_id}")
async def update_action_item(
    item_id: str,
    body: ActionItemUpdate,
    workspace: str = Depends(get_current_workspace),
    session: AsyncSession = Depends(get_session),
) -> dict:
    result = await session.execute(
        select(ActionItem).where(ActionItem.id == uuid.UUID(item_id))
    )
    item = result.scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail=f"Action item '{item_id}' not found.")
    ws_id = await resolve_workspace_id(session, workspace)
    if item.workspace_id != ws_id:
        raise HTTPException(status_code=404, detail=f"Action item '{item_id}' not found.")

    if body.description is not None:
        item.description = body.description
    if body.owner is not None:
        item.owner = body.owner
    if body.due_date is not None:
        try:
            item.due_date = datetime.fromisoformat(body.due_date)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"Invalid due_date format: '{body.due_date}'.")
    if body.status is not None:
        item.status = body.status
        if body.status == "done" and item.completed_at is None:
            item.completed_at = datetime.now(timezone.utc)

    await session.commit()
    await session.refresh(item)
    return _item_to_dict(item)


# ─── Delete ───────────────────────────────────────────────────────────────────

@router.delete("/{item_id}")
async def delete_action_item(
    item_id: str,
    workspace: str = Depends(get_current_workspace),
    session: AsyncSession = Depends(get_session),
) -> OkResponse:
    result = await session.execute(
        select(ActionItem).where(ActionItem.id == uuid.UUID(item_id))
    )
    item = result.scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail=f"Action item '{item_id}' not found.")
    ws_id = await resolve_workspace_id(session, workspace)
    if item.workspace_id != ws_id:
        raise HTTPException(status_code=404, detail=f"Action item '{item_id}' not found.")
    await session.delete(item)
    await session.commit()
    return OkResponse(message=f"Action item '{item_id}' deleted.")


# ─── Link (carry-forward) ─────────────────────────────────────────────────────

@router.post("/{item_id}/link")
async def link_action_item(
    item_id: str,
    body: dict,
    workspace: str = Depends(get_current_workspace),
    session: AsyncSession = Depends(get_session),
) -> dict:
    linked_item_id = body.get("linked_item_id")
    if not linked_item_id:
        raise HTTPException(status_code=422, detail="linked_item_id is required.")

    result = await session.execute(
        select(ActionItem).where(ActionItem.id == uuid.UUID(item_id))
    )
    item = result.scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail=f"Action item '{item_id}' not found.")
    ws_id = await resolve_workspace_id(session, workspace)
    if item.workspace_id != ws_id:
        raise HTTPException(status_code=404, detail=f"Action item '{item_id}' not found.")

    # Verify the linked item exists
    result2 = await session.execute(
        select(ActionItem).where(ActionItem.id == uuid.UUID(linked_item_id))
    )
    if result2.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail=f"Linked action item '{linked_item_id}' not found.")

    item.linked_item_id = uuid.UUID(linked_item_id)
    await session.commit()
    await session.refresh(item)
    return _item_to_dict(item)

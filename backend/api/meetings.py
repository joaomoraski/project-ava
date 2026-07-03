"""Meeting CRUD + prep + related endpoints."""
from __future__ import annotations

import asyncio
import uuid
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Depends, Query
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_workspace, resolve_workspace_id
from api.schemas import (
    MeetingCreate,
    MeetingUpdate,
    MeetingResponse,
    MeetingPrepResponse,
    OkResponse,
)
from core.db.engine import get_session, async_session
from core.db.models import Meeting, ActionItem, Workspace, KnowledgeChunk

logger = logging.getLogger("api.meetings")
router = APIRouter(prefix="/api/meetings", tags=["meetings"])


def _meeting_to_dict(m: Meeting) -> dict:
    return {
        "id": str(m.id),
        "workspace_id": str(m.workspace_id),
        "title": m.title or "",
        "participants": m.participants or [],
        "transcript": m.transcript or "",
        "summary": m.summary,
        "decisions": m.decisions or [],
        "document_md": m.document_md,
        "status": m.status or "recording",
        "started_at": m.started_at.isoformat() if m.started_at else "",
        "ended_at": m.ended_at.isoformat() if m.ended_at else None,
    }


async def _get_workspace_id(session: AsyncSession, workspace_name: str) -> uuid.UUID:
    result = await session.execute(
        select(Workspace.id).where(Workspace.name == workspace_name)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Workspace '{workspace_name}' not found.")
    return row


# ─── List ─────────────────────────────────────────────────────────────────────

@router.get("")
async def list_meetings(
    workspace: str = Depends(get_current_workspace),
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> dict:
    stmt = select(Meeting)
    result = await session.execute(
        select(Workspace.id).where(Workspace.name == workspace)
    )
    ws_id = result.scalar_one_or_none()
    if ws_id is None:
        return {"meetings": [], "count": 0}
    stmt = stmt.where(Meeting.workspace_id == ws_id)
    if status:
        stmt = stmt.where(Meeting.status == status)
    stmt = stmt.order_by(Meeting.started_at.desc()).offset(offset).limit(limit)
    result = await session.execute(stmt)
    meetings = result.scalars().all()
    return {"meetings": [_meeting_to_dict(m) for m in meetings], "count": len(meetings)}


# ─── Create ───────────────────────────────────────────────────────────────────

@router.post("")
async def create_meeting(
    body: MeetingCreate,
    session: AsyncSession = Depends(get_session),
) -> dict:
    ws_id = await _get_workspace_id(session, body.workspace)
    meeting = Meeting(
        workspace_id=ws_id,
        title=body.title,
        participants=body.participants,
        calendar_event_id=body.calendar_event_id,
        status="recording",
        started_at=datetime.now(timezone.utc),
    )
    session.add(meeting)
    await session.commit()
    await session.refresh(meeting)
    return _meeting_to_dict(meeting)


# ─── Prep briefing ────────────────────────────────────────────────────────────

@router.get("/prep")
async def meeting_prep(
    participants: str = Query(..., description="Comma-separated participant names"),
    workspace: str = Depends(get_current_workspace),
    session: AsyncSession = Depends(get_session),
) -> MeetingPrepResponse:
    participant_list = [p.strip() for p in participants.split(",") if p.strip()]
    if not participant_list:
        raise HTTPException(status_code=400, detail="At least one participant required.")

    # Find past meetings with overlapping participants using JSONB ?| operator
    from sqlalchemy import text, cast, ARRAY, String
    stmt = select(Meeting).where(
        Meeting.participants.op("?|")(cast(participant_list, ARRAY(String)))
    ).order_by(Meeting.started_at.desc()).limit(20)

    result_ws = await session.execute(
        select(Workspace.id).where(Workspace.name == workspace)
    )
    ws_id = result_ws.scalar_one_or_none()
    if ws_id:
        stmt = stmt.where(Meeting.workspace_id == ws_id)

    result = await session.execute(stmt)
    past_meetings = result.scalars().all()

    # Open action items for those participants — scoped to the current workspace.
    # Without workspace scoping this query would cross workspace boundaries and
    # expose action items from other workspaces to the requesting client.
    ai_stmt = (
        select(ActionItem)
        .where(
            ActionItem.owner.in_(participant_list),
            ActionItem.status != "done",
        )
        .order_by(ActionItem.created_at.desc())
        .limit(50)
    )
    if ws_id:
        ai_stmt = ai_stmt.where(ActionItem.workspace_id == ws_id)
    ai_result = await session.execute(ai_stmt)
    action_items = ai_result.scalars().all()

    return MeetingPrepResponse(
        past_meetings=[
            {
                "id": str(m.id),
                "title": m.title or "",
                "participants": m.participants or [],
                "summary": m.summary,
                "started_at": m.started_at.isoformat() if m.started_at else "",
                "status": m.status,
            }
            for m in past_meetings
        ],
        open_action_items=[
            {
                "id": str(a.id),
                "description": a.description,
                "owner": a.owner,
                "status": a.status,
                "due_date": a.due_date.isoformat() if a.due_date else None,
                "meeting_id": str(a.meeting_id) if a.meeting_id else None,
            }
            for a in action_items
        ],
    )


# ─── Decisions aggregate ──────────────────────────────────────────────────────

@router.get("/decisions")
async def list_decisions(
    workspace: str = Depends(get_current_workspace),
    limit: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
) -> dict:
    stmt = select(Meeting).where(Meeting.decisions != None)  # noqa: E711
    result_ws = await session.execute(
        select(Workspace.id).where(Workspace.name == workspace)
    )
    ws_id = result_ws.scalar_one_or_none()
    if ws_id:
        stmt = stmt.where(Meeting.workspace_id == ws_id)
    stmt = stmt.order_by(Meeting.started_at.desc()).limit(limit)
    result = await session.execute(stmt)
    meetings = result.scalars().all()

    decisions = []
    for m in meetings:
        for d in (m.decisions or []):
            decisions.append({
                "meeting_id": str(m.id),
                "meeting_title": m.title or "",
                "meeting_date": m.started_at.isoformat() if m.started_at else "",
                "decision": d,
            })
    return {"decisions": decisions, "count": len(decisions)}


# ─── Get one ──────────────────────────────────────────────────────────────────

@router.get("/{meeting_id}")
async def get_meeting(
    meeting_id: str,
    workspace: str = Depends(get_current_workspace),
    session: AsyncSession = Depends(get_session),
) -> dict:
    result = await session.execute(
        select(Meeting).where(Meeting.id == uuid.UUID(meeting_id))
    )
    meeting = result.scalar_one_or_none()
    if meeting is None:
        raise HTTPException(status_code=404, detail=f"Meeting '{meeting_id}' not found.")
    ws_id = await _get_workspace_id(session, workspace)
    if meeting.workspace_id != ws_id:
        raise HTTPException(status_code=404, detail=f"Meeting '{meeting_id}' not found.")
    return _meeting_to_dict(meeting)


# ─── Update ───────────────────────────────────────────────────────────────────

@router.put("/{meeting_id}")
async def update_meeting(
    meeting_id: str,
    body: MeetingUpdate,
    workspace: str = Depends(get_current_workspace),
    session: AsyncSession = Depends(get_session),
) -> dict:
    result = await session.execute(
        select(Meeting).where(Meeting.id == uuid.UUID(meeting_id))
    )
    meeting = result.scalar_one_or_none()
    if meeting is None:
        raise HTTPException(status_code=404, detail=f"Meeting '{meeting_id}' not found.")
    ws_id = await _get_workspace_id(session, workspace)
    if meeting.workspace_id != ws_id:
        raise HTTPException(status_code=404, detail=f"Meeting '{meeting_id}' not found.")

    content_changed = body.summary is not None and body.summary != meeting.summary

    if body.title is not None:
        meeting.title = body.title
    if body.participants is not None:
        meeting.participants = body.participants
    if body.summary is not None:
        meeting.summary = body.summary
    if body.status is not None:
        meeting.status = body.status

    # When the summary is updated the embedded chunks are stale — purge them so
    # the index never serves outdated text.  Re-embedding is deferred to the job
    # queue when the meeting is stopped; for mid-flight edits, deletion is the
    # safe conservative action.
    if content_changed:
        try:
            from core.knowledge.rag import KnowledgeBase
            kb = KnowledgeBase(workspace, "meetings")
            await kb.delete_source(session, f"meeting:{meeting.id}")
            logger.info("Purged stale KB chunks for meeting %s after summary edit", meeting.id)
        except Exception as kb_exc:
            logger.warning("Could not purge KB chunks for meeting %s: %s", meeting.id, kb_exc)

    await session.commit()
    await session.refresh(meeting)
    return _meeting_to_dict(meeting)


# ─── Delete ───────────────────────────────────────────────────────────────────

@router.delete("/{meeting_id}")
async def delete_meeting(
    meeting_id: str,
    workspace: str = Depends(get_current_workspace),
    session: AsyncSession = Depends(get_session),
) -> OkResponse:
    result = await session.execute(
        select(Meeting).where(Meeting.id == uuid.UUID(meeting_id))
    )
    meeting = result.scalar_one_or_none()
    if meeting is None:
        raise HTTPException(status_code=404, detail=f"Meeting '{meeting_id}' not found.")
    ws_id = await _get_workspace_id(session, workspace)
    if meeting.workspace_id != ws_id:
        raise HTTPException(status_code=404, detail=f"Meeting '{meeting_id}' not found.")

    # Remove knowledge-base chunks for this meeting to avoid orphaned embeddings.
    try:
        from core.knowledge.rag import KnowledgeBase
        kb = KnowledgeBase(workspace, "meetings")
        await kb.delete_source(session, f"meeting:{meeting_id}")
    except Exception as kb_exc:
        logger.warning("Could not delete KB chunks for meeting %s: %s", meeting_id, kb_exc)

    await session.delete(meeting)
    await session.commit()
    return OkResponse(message=f"Meeting '{meeting_id}' deleted.")


# ─── Stop ─────────────────────────────────────────────────────────────────────

@router.post("/{meeting_id}/stop")
async def stop_meeting(
    meeting_id: str,
    workspace: str = Depends(get_current_workspace),
    session: AsyncSession = Depends(get_session),
) -> dict:
    result = await session.execute(
        select(Meeting).where(Meeting.id == uuid.UUID(meeting_id))
    )
    meeting = result.scalar_one_or_none()
    if meeting is None:
        raise HTTPException(status_code=404, detail=f"Meeting '{meeting_id}' not found.")
    ws_id = await _get_workspace_id(session, workspace)
    if meeting.workspace_id != ws_id:
        raise HTTPException(status_code=404, detail=f"Meeting '{meeting_id}' not found.")

    meeting.status = "completed"
    meeting.ended_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(meeting)

    # Enqueue document generation + embedding via procrastinate
    try:
        from core.jobs.tracker import create_job_task
        from core.jobs.tasks.meetings import generate_meeting_doc
        task_row = await create_job_task(
            session=session,
            job_type="generate_meeting_doc",
            target_type="meeting",
            target_id=meeting.id,
            workspace_id=meeting.workspace_id,
        )
        await generate_meeting_doc.defer_async(
            meeting_id=str(meeting.id),
            workspace=workspace,
            task_id=str(task_row.id),
        )
        logger.info(
            "Enqueued generate_meeting_doc for meeting %s (job=%s)",
            meeting.id, task_row.id,
        )
        # Enqueue auto-categorization after document generation
        try:
            from core.jobs.tasks.contexts import auto_categorize_item
            await auto_categorize_item.defer_async(
                target_type="meeting",
                target_id=str(meeting.id),
                workspace_id=str(meeting.workspace_id),
            )
        except Exception as cat_exc:
            logger.warning("auto_categorize enqueue failed (non-fatal): %s", cat_exc)
    except Exception as exc:
        logger.error("Failed to enqueue generate_meeting_doc for %s: %s", meeting_id, exc)

    return _meeting_to_dict(meeting)


# ─── Related ──────────────────────────────────────────────────────────────────

@router.get("/{meeting_id}/related")
async def get_related_meetings(
    meeting_id: str,
    workspace: str = Depends(get_current_workspace),
    limit: int = Query(5, ge=1, le=20),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Find related meetings by topic similarity (with distance threshold) + participant overlap."""
    from sqlalchemy import text as sql_text

    MAX_DISTANCE = 0.35  # cosine distance threshold — only truly related meetings

    try:
        meeting_uuid = uuid.UUID(meeting_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid meeting ID format.")

    result = await session.execute(
        select(Meeting).where(Meeting.id == meeting_uuid)
    )
    meeting = result.scalar_one_or_none()
    if meeting is None:
        raise HTTPException(status_code=404, detail=f"Meeting '{meeting_id}' not found.")

    ws_id = await _get_workspace_id(session, workspace)
    if meeting.workspace_id != ws_id:
        raise HTTPException(status_code=404, detail=f"Meeting '{meeting_id}' not found.")

    related: list[dict] = []

    # 1. Vector similarity with strict threshold — scoped to current workspace only.
    #    Both kc.workspace_id and m.workspace_id are filtered to prevent cross-workspace
    #    vector content leaking through the JOIN.
    chunks_result = await session.execute(
        select(KnowledgeChunk).where(
            and_(
                KnowledgeChunk.workspace_id == ws_id,
                KnowledgeChunk.source_type == "meeting",
                KnowledgeChunk.source.contains(meeting_id),
            )
        ).limit(3)
    )
    chunks = chunks_result.scalars().all()

    if chunks:
        ref_chunk = chunks[0]
        if ref_chunk.embedding is not None:
            try:
                import numpy as np
                emb = ref_chunk.embedding
                if isinstance(emb, np.ndarray):
                    emb = emb.tolist()
                emb_str = "[" + ",".join(str(float(v)) for v in emb) + "]"

                similar_result = await session.execute(
                    sql_text("""
                        SELECT DISTINCT ON (m.id) m.id, m.title, m.participants, m.summary,
                               m.started_at, m.status,
                               MIN(kc.embedding <=> cast(:ref_embedding as vector)) AS distance
                        FROM knowledge_chunks kc
                        JOIN meetings m ON kc.source LIKE '%%' || m.id::text || '%%'
                        WHERE kc.source_type = 'meeting'
                          AND m.id != :meeting_id
                          AND kc.embedding IS NOT NULL
                          AND kc.workspace_id = cast(:workspace_id as uuid)
                          AND m.workspace_id = cast(:workspace_id as uuid)
                        GROUP BY m.id
                        HAVING MIN(kc.embedding <=> cast(:ref_embedding as vector)) < :max_distance
                        ORDER BY m.id, distance
                        LIMIT :limit
                    """),
                    {
                        "ref_embedding": emb_str,
                        "meeting_id": str(meeting_uuid),
                        "workspace_id": str(ws_id),
                        "limit": limit,
                        "max_distance": MAX_DISTANCE,
                    },
                )
                rows = similar_result.fetchall()
                related = [
                    {
                        "id": str(row.id),
                        "title": row.title or "",
                        "participants": row.participants or [],
                        "summary": row.summary,
                        "started_at": row.started_at.isoformat() if row.started_at else None,
                        "status": row.status or "completed",
                        "reason": f"Topic similarity ({(1 - row.distance) * 100:.0f}% match)",
                    }
                    for row in rows
                ]
            except Exception as exc:
                logger.warning(f"Vector similarity query failed for meeting {meeting_id}: {exc}")

    # 2. Also check participants overlap (different reason)
    seen_ids = {r["id"] for r in related}
    participants = meeting.participants or []
    if participants and len(related) < limit:
        try:
            from sqlalchemy.dialects.postgresql import ARRAY
            from sqlalchemy import String, cast
            fallback_result = await session.execute(
                select(Meeting).where(
                    and_(
                        Meeting.id != meeting_uuid,
                        Meeting.workspace_id == meeting.workspace_id,
                        Meeting.participants.op("?|")(cast(participants, ARRAY(String))),
                    )
                ).order_by(Meeting.started_at.desc()).limit(limit)
            )
            for m in fallback_result.scalars().all():
                mid = str(m.id)
                if mid not in seen_ids:
                    overlap = set(m.participants or []) & set(participants)
                    related.append({
                        "id": mid,
                        "title": m.title or "",
                        "participants": m.participants or [],
                        "summary": m.summary,
                        "started_at": m.started_at.isoformat() if m.started_at else None,
                        "status": m.status or "completed",
                        "reason": f"Same participants: {', '.join(overlap)}",
                    })
                    seen_ids.add(mid)
        except Exception as exc:
            logger.debug(f"Participant overlap query failed: {exc}")

    return {"related": related[:limit], "count": len(related[:limit])}

"""Notes CRUD API endpoints.

GET    /api/notes              — list notes (filter by workspace, tags, search)
POST   /api/notes              — create note + auto-embed into knowledge base
GET    /api/notes/{note_id}    — get one note
PUT    /api/notes/{note_id}    — update note + re-embed into knowledge base
DELETE /api/notes/{note_id}    — delete note + remove embeddings
"""
from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_workspace
from api.schemas import NoteCreate, NoteUpdate, NoteResponse, OkResponse
from core.db.engine import get_session
from core.db.models import Note, Workspace

router = APIRouter(prefix="/api/notes", tags=["notes"])
logger = logging.getLogger("api.notes")

CHUNK_SIZE = 500  # characters per chunk for note content


def _chunk_content(text: str, chunk_size: int = CHUNK_SIZE) -> list[str]:
    """Split text into chunks by paragraphs, falling back to fixed-size splits."""
    if not text.strip():
        return []

    # Try paragraph-based chunking first
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        return []

    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        if len(current) + len(para) + 2 <= chunk_size:
            current = (current + "\n\n" + para).strip() if current else para
        else:
            if current:
                chunks.append(current)
            # If a single paragraph exceeds chunk_size, split it further
            if len(para) > chunk_size:
                start = 0
                while start < len(para):
                    chunks.append(para[start : start + chunk_size])
                    start += chunk_size
                current = ""
            else:
                current = para
    if current:
        chunks.append(current)

    return chunks


def _note_source_key(note_id: str) -> str:
    return f"note:{note_id}"


def _note_to_response(note: Note) -> NoteResponse:
    return NoteResponse(
        id=str(note.id),
        workspace_id=str(note.workspace_id),
        title=note.title,
        content=note.content or "",
        tags=note.tags or [],
        created_at=note.created_at.isoformat() if note.created_at else "",
        updated_at=note.updated_at.isoformat() if note.updated_at else "",
    )


async def _embed_note(
    session: AsyncSession,
    note: Note,
    workspace_name: str,
) -> None:
    """Embed note content into the knowledge base. Failures are logged, not raised."""
    try:
        from core.knowledge.rag import KnowledgeBase

        text = note.content or ""
        if not text.strip():
            return

        raw_chunks = _chunk_content(text)
        source_key = _note_source_key(str(note.id))
        chunks = [
            {
                "content": chunk,
                "source": source_key,
                "source_type": "note",
                "file_name": note.title,
            }
            for chunk in raw_chunks
        ]

        kb = KnowledgeBase(workspace_name, collection_name="notes")
        indexed = await kb.add_chunks(session, chunks)
        logger.debug(f"Embedded {indexed} chunks for note {note.id}.")
    except Exception as e:
        logger.warning(f"Auto-embedding note {note.id} failed (non-fatal): {e}")


async def _delete_note_embeddings(
    session: AsyncSession,
    note_id: str,
    workspace_name: str,
) -> None:
    """Remove all embeddings for a note. Failures are logged, not raised."""
    try:
        from core.knowledge.rag import KnowledgeBase

        kb = KnowledgeBase(workspace_name, collection_name="notes")
        deleted = await kb.delete_source(session, _note_source_key(note_id))
        logger.debug(f"Removed {deleted} embedding chunks for note {note_id}.")
    except Exception as e:
        logger.warning(f"Removing embeddings for note {note_id} failed (non-fatal): {e}")


async def _get_workspace_name(session: AsyncSession, workspace_id: uuid.UUID) -> str | None:
    result = await session.execute(
        select(Workspace.name).where(Workspace.id == workspace_id)
    )
    return result.scalar_one_or_none()


async def _resolve_workspace_id(session: AsyncSession, workspace_name: str) -> uuid.UUID | None:
    result = await session.execute(
        select(Workspace.id).where(Workspace.name == workspace_name)
    )
    return result.scalar_one_or_none()


@router.get("")
async def list_notes(
    workspace: str = Depends(get_current_workspace),
    tags: str | None = Query(None, description="Comma-separated list of tags to filter by"),
    search: str | None = Query(None, description="Search in title and content"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> dict:
    workspace_id = await _resolve_workspace_id(session, workspace)
    if workspace_id is None:
        raise HTTPException(status_code=404, detail=f"Workspace '{workspace}' not found.")

    stmt = select(Note).where(Note.workspace_id == workspace_id)

    # Tags filter — note must contain ALL requested tags
    if tags:
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]
        for tag in tag_list:
            # JSONB contains operator: checks that the array contains the element
            stmt = stmt.where(Note.tags.contains([tag]))

    # Simple text search across title and content
    if search:
        like = f"%{search}%"
        from sqlalchemy import or_
        stmt = stmt.where(
            or_(
                Note.title.ilike(like),
                Note.content.ilike(like),
            )
        )

    stmt = stmt.order_by(Note.updated_at.desc()).offset(offset).limit(limit)
    result = await session.execute(stmt)
    notes = result.scalars().all()

    return {
        "notes": [_note_to_response(n).model_dump() for n in notes],
        "count": len(notes),
    }


@router.post("", status_code=201)
async def create_note(
    body: NoteCreate,
    session: AsyncSession = Depends(get_session),
) -> NoteResponse:
    workspace_id = await _resolve_workspace_id(session, body.workspace)
    if workspace_id is None:
        raise HTTPException(status_code=404, detail=f"Workspace '{body.workspace}' not found.")

    note = Note(
        workspace_id=workspace_id,
        title=body.title,
        content=body.content,
        tags=body.tags,
    )
    session.add(note)
    await session.commit()
    await session.refresh(note)
    logger.info(f"Created note '{note.title}' (id={note.id}) in workspace '{body.workspace}'.")

    await _embed_note(session, note, body.workspace)

    # Enqueue auto-categorization
    try:
        from core.jobs.tasks.contexts import auto_categorize_item
        await auto_categorize_item.defer_async(
            target_type="note",
            target_id=str(note.id),
            workspace_id=str(workspace_id),
        )
    except Exception as exc:
        logger.warning("auto_categorize enqueue failed (non-fatal): %s", exc)

    return _note_to_response(note)


@router.get("/{note_id}")
async def get_note(
    note_id: str,
    workspace: str = Depends(get_current_workspace),
    session: AsyncSession = Depends(get_session),
) -> NoteResponse:
    try:
        uid = uuid.UUID(note_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid note ID format.")

    result = await session.execute(select(Note).where(Note.id == uid))
    note = result.scalar_one_or_none()
    if note is None:
        raise HTTPException(status_code=404, detail=f"Note '{note_id}' not found.")
    ws_id = await _resolve_workspace_id(session, workspace)
    if ws_id is None or note.workspace_id != ws_id:
        raise HTTPException(status_code=404, detail=f"Note '{note_id}' not found.")

    return _note_to_response(note)


@router.put("/{note_id}")
async def update_note(
    note_id: str,
    body: NoteUpdate,
    workspace: str = Depends(get_current_workspace),
    session: AsyncSession = Depends(get_session),
) -> NoteResponse:
    try:
        uid = uuid.UUID(note_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid note ID format.")

    result = await session.execute(select(Note).where(Note.id == uid))
    note = result.scalar_one_or_none()
    if note is None:
        raise HTTPException(status_code=404, detail=f"Note '{note_id}' not found.")
    ws_id = await _resolve_workspace_id(session, workspace)
    if ws_id is None or note.workspace_id != ws_id:
        raise HTTPException(status_code=404, detail=f"Note '{note_id}' not found.")

    if body.title is not None:
        note.title = body.title
    if body.content is not None:
        note.content = body.content
    if body.tags is not None:
        note.tags = body.tags

    await session.commit()
    await session.refresh(note)
    logger.info(f"Updated note '{note.title}' (id={note.id}).")

    await _delete_note_embeddings(session, note_id, workspace)
    await _embed_note(session, note, workspace)

    return _note_to_response(note)


@router.delete("/{note_id}")
async def delete_note(
    note_id: str,
    workspace: str = Depends(get_current_workspace),
    session: AsyncSession = Depends(get_session),
) -> OkResponse:
    try:
        uid = uuid.UUID(note_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid note ID format.")

    result = await session.execute(select(Note).where(Note.id == uid))
    note = result.scalar_one_or_none()
    if note is None:
        raise HTTPException(status_code=404, detail=f"Note '{note_id}' not found.")
    ws_id = await _resolve_workspace_id(session, workspace)
    if ws_id is None or note.workspace_id != ws_id:
        raise HTTPException(status_code=404, detail=f"Note '{note_id}' not found.")

    await session.delete(note)
    await session.commit()
    logger.info(f"Deleted note {note_id}.")

    await _delete_note_embeddings(session, note_id, workspace)

    return OkResponse(message=f"Note '{note_id}' deleted.")

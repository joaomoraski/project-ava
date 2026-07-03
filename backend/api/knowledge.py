"""Knowledge base API endpoints.

GET  /api/knowledge/sources          — list indexed sources per workspace
POST /api/knowledge/upload           — upload a file for indexing
POST /api/knowledge/seed             — seed from URL, directory, or Notion
GET  /api/knowledge/seed/status      — check indexing progress
POST /api/knowledge/search           — semantic search
DELETE /api/knowledge/source         — remove a source
"""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, Query

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.schemas import KnowledgeSeedRequest, KnowledgeSearchRequest, OkResponse
from core.db.engine import get_session, async_session
from core.knowledge.ingestion import ingest_file, ingest_url, ingest_directory
from core.knowledge.rag import KnowledgeBase
from core.db.models import Workspace

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])
logger = logging.getLogger("api.knowledge")

_seed_progress: dict[str, dict] = {}


@router.get("/sources")
async def list_sources(
    workspace: str = Query("personal"),
    session: AsyncSession = Depends(get_session),
) -> dict:
    kb = KnowledgeBase(workspace)
    try:
        sources = await kb.list_sources(session)
        total = await kb.count(session)
        return {"sources": sources, "workspace": workspace, "total_chunks": total}
    except Exception as e:
        logger.error(f"list_sources failed: {e}")
        return {"sources": [], "workspace": workspace, "total_chunks": 0}


@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    workspace: str = Form("personal"),
    collection: str = Form("documents"),
    session: AsyncSession = Depends(get_session),
) -> dict:
    allowed_extensions = {".pdf", ".docx", ".txt", ".md", ".markdown", ".rst"}
    file_ext = os.path.splitext(file.filename or "")[1].lower()

    if file_ext not in allowed_extensions:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported file type: {file_ext}. Allowed: {', '.join(allowed_extensions)}",
        )

    content = await file.read()
    tmp_dir = tempfile.mkdtemp()
    tmp_path = os.path.join(tmp_dir, file.filename or "upload")

    with open(tmp_path, "wb") as f:
        f.write(content)

    # Resolve workspace id for job tracking
    ws_result = await session.execute(select(Workspace.id).where(Workspace.name == workspace))
    ws_id: uuid.UUID | None = ws_result.scalar_one_or_none()

    if ws_id is not None:
        try:
            from core.jobs.tracker import create_job_task
            from core.jobs.tasks.knowledge import ingest_file as ingest_file_task
            task_row = await create_job_task(
                session=session,
                job_type="ingest_file",
                target_type="knowledge_source",
                target_id=None,
                workspace_id=ws_id,
            )
            await ingest_file_task.defer_async(
                file_path=tmp_path,
                workspace=workspace,
                task_id=str(task_row.id),
            )
            return {"ok": True, "message": f"File '{file.filename}' queued for indexing.", "job_id": str(task_row.id)}
        except Exception as exc:
            logger.warning("Failed to enqueue ingest_file — falling back to in-process: %s", exc)

    # Fallback: run inline
    asyncio.create_task(_index_file(tmp_path, workspace, collection))
    return {"ok": True, "message": f"File '{file.filename}' queued for indexing.", "job_id": None}


@router.post("/seed")
async def seed_knowledge(
    body: KnowledgeSeedRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    legacy_task_id = f"{body.workspace}_{body.type}_{body.source[:30]}"
    _seed_progress[legacy_task_id] = {"status": "queued", "progress": 0, "total": 0}

    ws_result = await session.execute(select(Workspace.id).where(Workspace.name == body.workspace))
    ws_id: uuid.UUID | None = ws_result.scalar_one_or_none()

    job_id: str | None = None
    if ws_id is not None:
        try:
            from core.jobs.tracker import create_job_task
            from core.jobs.tasks.knowledge import ingest_url as ingest_url_task, ingest_directory as ingest_dir_task

            if body.type == "url":
                task_row = await create_job_task(
                    session=session,
                    job_type="ingest_url",
                    target_type="knowledge_source",
                    target_id=None,
                    workspace_id=ws_id,
                )
                await ingest_url_task.defer_async(
                    url=body.source,
                    workspace=body.workspace,
                    task_id=str(task_row.id),
                )
                job_id = str(task_row.id)
            elif body.type == "directory":
                task_row = await create_job_task(
                    session=session,
                    job_type="ingest_directory",
                    target_type="knowledge_source",
                    target_id=None,
                    workspace_id=ws_id,
                )
                await ingest_dir_task.defer_async(
                    path=body.source,
                    workspace=body.workspace,
                    task_id=str(task_row.id),
                )
                job_id = str(task_row.id)
        except Exception as exc:
            logger.warning("Failed to enqueue seed task — falling back: %s", exc)

    if job_id is None:
        # Fallback: run inline background task
        asyncio.create_task(_seed_source(body.workspace, body.type, body.source, legacy_task_id))

    return {"ok": True, "message": f"Seeding queued: {body.type} → {body.source[:50]}", "job_id": job_id}


@router.get("/seed/status")
async def seed_status(workspace: str = Query("personal")) -> dict:
    ws_tasks = {k: v for k, v in _seed_progress.items() if k.startswith(workspace)}
    if not ws_tasks:
        return {"status": "idle", "workspace": workspace}
    latest = list(ws_tasks.items())[-1]
    return {"status": latest[1]["status"], "workspace": workspace, **latest[1]}


@router.post("/search")
async def search_knowledge(
    body: KnowledgeSearchRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    if not body.query.strip():
        raise HTTPException(status_code=422, detail="Query cannot be empty.")

    kb = KnowledgeBase(body.workspace)
    try:
        results = await kb.search(session, body.query, top_k=body.limit)
        return {
            "results": results,
            "count": len(results),
            "query": body.query,
            "workspace": body.workspace,
        }
    except Exception as e:
        logger.error(f"Knowledge search failed: {e}")
        raise HTTPException(status_code=500, detail=f"Search failed: {e}")


@router.get("/chunks")
async def list_chunks(
    source: str = Query(...),
    workspace: str = Query("personal"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """List actual indexed chunks for a given source."""
    from sqlalchemy import select, func
    from core.db.models import KnowledgeChunk, Workspace

    ws_result = await session.execute(
        select(Workspace.id).where(Workspace.name == workspace)
    )
    ws_id = ws_result.scalar_one_or_none()
    if ws_id is None:
        return {"chunks": [], "count": 0, "total": 0}

    # Total count
    count_result = await session.execute(
        select(func.count()).select_from(KnowledgeChunk).where(
            KnowledgeChunk.workspace_id == ws_id,
            KnowledgeChunk.source == source,
        )
    )
    total = count_result.scalar() or 0

    # Fetch chunks
    result = await session.execute(
        select(KnowledgeChunk)
        .where(KnowledgeChunk.workspace_id == ws_id, KnowledgeChunk.source == source)
        .order_by(KnowledgeChunk.created_at)
        .offset(offset)
        .limit(limit)
    )
    chunks = result.scalars().all()

    return {
        "chunks": [
            {
                "id": str(c.id),
                "content": c.content,
                "source_type": c.source_type,
                "file_name": c.file_name,
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in chunks
        ],
        "count": len(chunks),
        "total": total,
        "source": source,
        "workspace": workspace,
    }


@router.delete("/source")
async def delete_source(
    source: str = Query(...),
    workspace: str = Query("personal"),
    session: AsyncSession = Depends(get_session),
) -> OkResponse:
    kb = KnowledgeBase(workspace)
    try:
        deleted = await kb.delete_source(session, source)
        return OkResponse(message=f"Deleted {deleted} chunks from source: {source}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Delete failed: {e}")


# ─── Background tasks ────────────────────────────────────────────────────────

async def _index_file(path: str, workspace: str, collection: str) -> None:
    try:
        loop = asyncio.get_event_loop()
        chunks = await loop.run_in_executor(None, ingest_file, path)
        if chunks:
            async with async_session() as session:
                kb = KnowledgeBase(workspace, collection)
                await kb.add_chunks(session, chunks)
                logger.info(f"Indexed file: {path} → {len(chunks)} chunks")
    except Exception as e:
        logger.error(f"File indexing failed for {path}: {e}")
    finally:
        try:
            os.unlink(path)
            os.rmdir(os.path.dirname(path))
        except Exception:
            pass


async def _seed_source(workspace: str, source_type: str, source: str, task_id: str) -> None:
    _seed_progress[task_id] = {"status": "running", "progress": 0, "total": 0}

    try:
        loop = asyncio.get_event_loop()

        if source_type == "url":
            chunks = await loop.run_in_executor(None, ingest_url, source)
        elif source_type == "directory":
            chunks = await loop.run_in_executor(None, ingest_directory, source)
        elif source_type == "notion_db":
            logger.warning("Notion DB seeding requires notion MCP — not yet available")
            chunks = []
        else:
            logger.error(f"Unknown seed type: {source_type}")
            _seed_progress[task_id]["status"] = "error"
            return

        _seed_progress[task_id]["total"] = len(chunks)

        if chunks:
            async with async_session() as session:
                kb = KnowledgeBase(workspace)
                await kb.add_chunks(session, chunks)

        _seed_progress[task_id]["status"] = "done"
        _seed_progress[task_id]["progress"] = len(chunks)
        logger.info(f"Seeding complete: {source_type}={source} → {len(chunks)} chunks in {workspace}")

    except Exception as e:
        logger.error(f"Seeding failed for {source_type}={source}: {e}")
        _seed_progress[task_id]["status"] = "error"

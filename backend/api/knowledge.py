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

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile, Query
from fastapi.responses import JSONResponse

from api.schemas import KnowledgeSeedRequest, KnowledgeSearchRequest, OkResponse
from core.knowledge.ingestion import ingest_file, ingest_url, ingest_directory
from core.knowledge.rag import KnowledgeBase

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])
logger = logging.getLogger("api.knowledge")

# Simple in-memory progress tracker
_seed_progress: dict[str, dict] = {}


@router.get("/sources")
async def list_sources(workspace: str = Query("personal")) -> dict:
    """List all indexed sources for a workspace."""
    kb = KnowledgeBase(workspace)
    try:
        sources = kb.list_sources()
        return {"sources": sources, "workspace": workspace, "total_chunks": kb.count()}
    except Exception as e:
        logger.error(f"list_sources failed: {e}")
        return {"sources": [], "workspace": workspace, "total_chunks": 0}


@router.post("/upload")
async def upload_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    workspace: str = Form("personal"),
    collection: str = Form("documents"),
) -> OkResponse:
    """Upload a file and index it into the knowledge base."""
    allowed_extensions = {".pdf", ".docx", ".txt", ".md", ".markdown", ".rst"}
    file_ext = os.path.splitext(file.filename or "")[1].lower()

    if file_ext not in allowed_extensions:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported file type: {file_ext}. Allowed: {', '.join(allowed_extensions)}",
        )

    # Save to temp file, then index in background
    content = await file.read()
    tmp_dir = tempfile.mkdtemp()
    tmp_path = os.path.join(tmp_dir, file.filename or "upload")

    with open(tmp_path, "wb") as f:
        f.write(content)

    background_tasks.add_task(_index_file, tmp_path, workspace, collection)
    return OkResponse(message=f"File '{file.filename}' queued for indexing.")


@router.post("/seed")
async def seed_knowledge(
    body: KnowledgeSeedRequest,
    background_tasks: BackgroundTasks,
) -> OkResponse:
    """Seed workspace knowledge from a URL, directory, or Notion database."""
    task_id = f"{body.workspace}_{body.type}_{body.source[:30]}"
    _seed_progress[task_id] = {"status": "queued", "progress": 0, "total": 0}
    background_tasks.add_task(_seed_source, body.workspace, body.type, body.source, task_id)
    return OkResponse(message=f"Seeding queued: {body.type} → {body.source[:50]}")


@router.get("/seed/status")
async def seed_status(workspace: str = Query("personal")) -> dict:
    """Check indexing progress for a workspace."""
    # Find latest task for this workspace
    ws_tasks = {k: v for k, v in _seed_progress.items() if k.startswith(workspace)}
    if not ws_tasks:
        return {"status": "idle", "workspace": workspace}

    # Return the most recently added task status
    latest = list(ws_tasks.items())[-1]
    return {"status": latest[1]["status"], "workspace": workspace, **latest[1]}


@router.post("/search")
async def search_knowledge(body: KnowledgeSearchRequest) -> dict:
    """Semantic search across workspace knowledge base."""
    if not body.query.strip():
        raise HTTPException(status_code=422, detail="Query cannot be empty.")

    kb = KnowledgeBase(body.workspace)
    try:
        results = kb.search(body.query, top_k=body.limit)
        return {
            "results": results,
            "count": len(results),
            "query": body.query,
            "workspace": body.workspace,
        }
    except Exception as e:
        logger.error(f"Knowledge search failed: {e}")
        raise HTTPException(status_code=500, detail=f"Search failed: {e}")


@router.delete("/source")
async def delete_source(
    source: str = Query(...),
    workspace: str = Query("personal"),
) -> OkResponse:
    """Remove all chunks from a specific source."""
    kb = KnowledgeBase(workspace)
    try:
        deleted = kb.delete_source(source)
        return OkResponse(message=f"Deleted {deleted} chunks from source: {source}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Delete failed: {e}")


# ─── Background tasks ────────────────────────────────────────────────────────

async def _index_file(path: str, workspace: str, collection: str) -> None:
    """Background task: ingest file and add to ChromaDB."""
    try:
        loop = asyncio.get_event_loop()
        chunks = await loop.run_in_executor(None, ingest_file, path)
        if chunks:
            kb = KnowledgeBase(workspace, collection)
            kb.add_chunks(chunks)
            logger.info(f"Indexed file: {path} → {len(chunks)} chunks")
    except Exception as e:
        logger.error(f"File indexing failed for {path}: {e}")
    finally:
        # Clean up temp file
        try:
            os.unlink(path)
            os.rmdir(os.path.dirname(path))
        except Exception:
            pass


async def _seed_source(workspace: str, source_type: str, source: str, task_id: str) -> None:
    """Background task: seed from URL, directory, or Notion."""
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
            kb = KnowledgeBase(workspace)
            kb.add_chunks(chunks)

        _seed_progress[task_id]["status"] = "done"
        _seed_progress[task_id]["progress"] = len(chunks)
        logger.info(f"Seeding complete: {source_type}={source} → {len(chunks)} chunks in {workspace}")

    except Exception as e:
        logger.error(f"Seeding failed for {source_type}={source}: {e}")
        _seed_progress[task_id]["status"] = "error"

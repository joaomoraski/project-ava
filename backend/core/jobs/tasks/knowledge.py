"""Procrastinate tasks: ingest URL, directory, and single file into knowledge base."""
from __future__ import annotations

import logging
import os
import uuid

from procrastinate import RetryStrategy

from core.jobs.app import procrastinate_app
from core.jobs.tracker import mark_started, mark_completed, mark_failed, update_progress

logger = logging.getLogger("jobs.tasks.knowledge")

_RETRY = RetryStrategy(max_attempts=2, exponential_wait=5)


@procrastinate_app.task(name="ingest_url", retry=_RETRY)
async def ingest_url(url: str, workspace: str, *, task_id: str) -> None:
    """Fetch a URL and embed its content into the knowledge base."""
    tid = uuid.UUID(task_id)
    await mark_started(tid)

    try:
        import asyncio
        from core.db.engine import async_session
        from core.knowledge.ingestion import ingest_url as _ingest_url
        from core.knowledge.rag import KnowledgeBase

        await update_progress(tid, 0.1, "Fetching URL")
        loop = asyncio.get_event_loop()
        chunks = await loop.run_in_executor(None, _ingest_url, url)

        await update_progress(tid, 0.4, "Extracting content")

        if not chunks:
            await mark_completed(tid, result={"url": url, "chunks": 0})
            return

        await update_progress(tid, 0.7, "Chunking")
        await update_progress(tid, 0.95, "Embedding")

        async with async_session() as session:
            kb = KnowledgeBase(workspace)
            indexed = await kb.add_chunks(session, chunks)

        await mark_completed(tid, result={"url": url, "chunks": indexed})
        logger.info("ingest_url done: %s → %d chunks in %s", url, indexed, workspace)

    except Exception as exc:
        logger.error("ingest_url failed for %s: %s", url, exc)
        await mark_failed(tid, str(exc))
        raise


@procrastinate_app.task(name="ingest_directory", retry=_RETRY)
async def ingest_directory(path: str, workspace: str, *, task_id: str) -> None:
    """Walk a directory and embed all supported files into the knowledge base."""
    tid = uuid.UUID(task_id)
    await mark_started(tid)

    try:
        import asyncio
        from core.db.engine import async_session
        from core.knowledge.ingestion import iter_directory_files, ingest_file
        from core.knowledge.rag import KnowledgeBase

        await update_progress(tid, 0.1, "Scanning directory")
        loop = asyncio.get_event_loop()
        file_paths = list(iter_directory_files(path))

        if not file_paths:
            await mark_completed(tid, result={"path": path, "files": 0, "chunks": 0})
            return

        total = len(file_paths)
        all_chunks: list[dict] = []

        for i, fp in enumerate(file_paths):
            chunks = await loop.run_in_executor(None, ingest_file, fp)
            all_chunks.extend(chunks)
            progress = 0.1 + 0.75 * ((i + 1) / total)
            await update_progress(tid, progress, f"Ingesting file {i + 1}/{total}")

        await update_progress(tid, 0.95, "Embedding")

        indexed = 0
        if all_chunks:
            async with async_session() as session:
                kb = KnowledgeBase(workspace)
                indexed = await kb.add_chunks(session, all_chunks)

        await mark_completed(tid, result={"path": path, "files": total, "chunks": indexed})
        logger.info("ingest_directory done: %s → %d files, %d chunks", path, total, indexed)

    except Exception as exc:
        logger.error("ingest_directory failed for %s: %s", path, exc)
        await mark_failed(tid, str(exc))
        raise


@procrastinate_app.task(name="ingest_file", retry=_RETRY)
async def ingest_file(file_path: str, workspace: str, *, task_id: str) -> None:
    """Ingest a single file and embed its chunks into the knowledge base."""
    tid = uuid.UUID(task_id)
    await mark_started(tid)

    try:
        import asyncio
        from core.db.engine import async_session
        from core.knowledge.ingestion import ingest_file as _ingest_file
        from core.knowledge.rag import KnowledgeBase

        await update_progress(tid, 0.1, "Reading file")
        loop = asyncio.get_event_loop()
        chunks = await loop.run_in_executor(None, _ingest_file, file_path)

        if not chunks:
            # Clean up temp file
            _try_remove(file_path)
            await mark_completed(tid, result={"file": file_path, "chunks": 0})
            return

        await update_progress(tid, 0.7, "Chunking")
        await update_progress(tid, 0.95, "Embedding")

        async with async_session() as session:
            kb = KnowledgeBase(workspace)
            indexed = await kb.add_chunks(session, chunks)

        _try_remove(file_path)
        await mark_completed(tid, result={"file": file_path, "chunks": indexed})
        logger.info("ingest_file done: %s → %d chunks in %s", file_path, indexed, workspace)

    except Exception as exc:
        logger.error("ingest_file failed for %s: %s", file_path, exc)
        _try_remove(file_path)
        await mark_failed(tid, str(exc))
        raise


def _try_remove(path: str) -> None:
    """Best-effort cleanup of temp file and its parent dir."""
    try:
        os.unlink(path)
        parent = os.path.dirname(path)
        if parent and os.path.isdir(parent):
            try:
                os.rmdir(parent)
            except OSError:
                pass
    except Exception:
        pass

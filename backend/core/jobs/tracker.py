"""CRUD helpers for JobTask — track background job state and broadcast WS events."""
from __future__ import annotations

import logging
import uuid as _uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.engine import async_session
from core.db.models import JobTask

logger = logging.getLogger("jobs.tracker")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _task_to_dict(t: JobTask) -> dict[str, Any]:
    return {
        "id": str(t.id),
        "job_type": t.job_type,
        "target_type": t.target_type,
        "target_id": str(t.target_id) if t.target_id else None,
        "workspace_id": str(t.workspace_id),
        "status": t.status,
        "progress": t.progress,
        "progress_message": t.progress_message,
        "procrastinate_job_id": t.procrastinate_job_id,
        "error": t.error,
        "result_payload": t.result_payload,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "started_at": t.started_at.isoformat() if t.started_at else None,
        "completed_at": t.completed_at.isoformat() if t.completed_at else None,
    }


async def _broadcast(event: dict[str, Any]) -> None:
    """Broadcast a WS event if the manager is available."""
    try:
        from api.ws import ws_manager
        await ws_manager.broadcast(event)
    except Exception as exc:
        logger.debug(f"WS broadcast skipped: {exc}")


# ─── Public helpers ──────────────────────────────────────────────────────────

async def create_job_task(
    *,
    session: AsyncSession,
    job_type: str,
    target_type: str | None,
    target_id: _uuid.UUID | None,
    workspace_id: _uuid.UUID,
) -> JobTask:
    task = JobTask(
        job_type=job_type,
        target_type=target_type,
        target_id=target_id,
        workspace_id=workspace_id,
        status="queued",
        progress=0.0,
    )
    session.add(task)
    await session.commit()
    await session.refresh(task)
    return task


async def update_progress(
    task_id: _uuid.UUID,
    progress: float,
    message: str | None = None,
) -> None:
    async with async_session() as session:
        row = await session.get(JobTask, task_id)
        if not row:
            return
        row.progress = max(0.0, min(1.0, progress))
        if message is not None:
            row.progress_message = message
        await session.commit()

    await _broadcast({
        "type": "job_progress",
        "id": str(task_id),
        "target_type": row.target_type,
        "target_id": str(row.target_id) if row.target_id else None,
        "status": row.status,
        "progress": row.progress,
        "message": row.progress_message,
    })


async def mark_started(task_id: _uuid.UUID) -> None:
    async with async_session() as session:
        row = await session.get(JobTask, task_id)
        if not row:
            return
        row.status = "running"
        row.started_at = _now()
        await session.commit()

    await _broadcast({
        "type": "job_progress",
        "id": str(task_id),
        "target_type": row.target_type,
        "target_id": str(row.target_id) if row.target_id else None,
        "status": "running",
        "progress": row.progress,
        "message": row.progress_message,
    })


async def mark_completed(
    task_id: _uuid.UUID,
    result: dict[str, Any] | None = None,
) -> None:
    async with async_session() as session:
        row = await session.get(JobTask, task_id)
        if not row:
            return
        row.status = "completed"
        row.progress = 1.0
        row.completed_at = _now()
        if result is not None:
            row.result_payload = result
        await session.commit()

    await _broadcast({
        "type": "job_complete",
        "id": str(task_id),
        "target_type": row.target_type,
        "target_id": str(row.target_id) if row.target_id else None,
        "result": result,
    })


async def mark_failed(task_id: _uuid.UUID, error: str) -> None:
    async with async_session() as session:
        row = await session.get(JobTask, task_id)
        if not row:
            return
        row.status = "failed"
        row.error = error
        row.completed_at = _now()
        await session.commit()

    await _broadcast({
        "type": "job_failed",
        "id": str(task_id),
        "target_type": row.target_type,
        "target_id": str(row.target_id) if row.target_id else None,
        "error": error,
    })


async def list_jobs(
    *,
    session: AsyncSession,
    workspace_id: _uuid.UUID,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[JobTask]:
    q = select(JobTask).where(JobTask.workspace_id == workspace_id)
    if status:
        q = q.where(JobTask.status == status)
    q = q.order_by(JobTask.created_at.desc()).limit(limit).offset(offset)
    result = await session.execute(q)
    return list(result.scalars().all())


async def get_job(
    *,
    session: AsyncSession,
    task_id: _uuid.UUID,
) -> JobTask | None:
    return await session.get(JobTask, task_id)


async def get_job_by_target(
    *,
    session: AsyncSession,
    target_type: str,
    target_id: _uuid.UUID,
) -> JobTask | None:
    q = (
        select(JobTask)
        .where(JobTask.target_type == target_type, JobTask.target_id == target_id)
        .order_by(JobTask.created_at.desc())
        .limit(1)
    )
    result = await session.execute(q)
    return result.scalars().first()

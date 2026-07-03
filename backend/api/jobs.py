"""Jobs API — track background task state per workspace."""
from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_workspace, resolve_workspace_id
from api.schemas import JobTaskResponse, JobsListResponse, OkResponse
from core.db.engine import get_session
from core.db.models import JobTask, Workspace
from core.jobs.tracker import get_job, get_job_by_target, list_jobs

logger = logging.getLogger("api.jobs")
router = APIRouter(prefix="/api/jobs", tags=["jobs"])


def _task_to_response(t: JobTask) -> JobTaskResponse:
    return JobTaskResponse(
        id=str(t.id),
        job_type=t.job_type,
        target_type=t.target_type,
        target_id=str(t.target_id) if t.target_id else None,
        workspace_id=str(t.workspace_id),
        status=t.status,
        progress=t.progress,
        progress_message=t.progress_message,
        procrastinate_job_id=t.procrastinate_job_id,
        error=t.error,
        result_payload=t.result_payload,
        created_at=t.created_at.isoformat() if t.created_at else None,
        started_at=t.started_at.isoformat() if t.started_at else None,
        completed_at=t.completed_at.isoformat() if t.completed_at else None,
    )


async def _get_workspace_id(session: AsyncSession, workspace_name: str) -> uuid.UUID:
    result = await session.execute(
        select(Workspace.id).where(Workspace.name == workspace_name)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Workspace '{workspace_name}' not found.")
    return row


# ─── List ─────────────────────────────────────────────────────────────────────

@router.get("", response_model=JobsListResponse)
async def list_jobs_endpoint(
    workspace: str = Depends(get_current_workspace),
    status: str | None = Query(None),
    type: str | None = Query(None, alias="type"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> JobsListResponse:
    workspace_id = await _get_workspace_id(session, workspace)
    tasks = await list_jobs(
        session=session,
        workspace_id=workspace_id,
        status=status,
        limit=limit,
        offset=offset,
    )
    # Optional filter by job_type
    if type:
        tasks = [t for t in tasks if t.job_type == type]
    return JobsListResponse(jobs=[_task_to_response(t) for t in tasks], count=len(tasks))


# ─── By target ────────────────────────────────────────────────────────────────

@router.get("/by-target/{target_type}/{target_id}", response_model=JobTaskResponse | None)
async def get_job_by_target_endpoint(
    target_type: str,
    target_id: str,
    workspace: str = Depends(get_current_workspace),
    session: AsyncSession = Depends(get_session),
) -> JobTaskResponse | None:
    try:
        tid = uuid.UUID(target_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="target_id must be a valid UUID")

    task = await get_job_by_target(session=session, target_type=target_type, target_id=tid)
    if not task:
        return None
    ws_id = await resolve_workspace_id(session, workspace)
    if task.workspace_id != ws_id:
        return None
    return _task_to_response(task)


# ─── Detail ───────────────────────────────────────────────────────────────────

@router.get("/{id}", response_model=JobTaskResponse)
async def get_job_endpoint(
    id: str,
    workspace: str = Depends(get_current_workspace),
    session: AsyncSession = Depends(get_session),
) -> JobTaskResponse:
    try:
        task_id = uuid.UUID(id)
    except ValueError:
        raise HTTPException(status_code=422, detail="id must be a valid UUID")

    task = await get_job(session=session, task_id=task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Job not found")
    ws_id = await resolve_workspace_id(session, workspace)
    if task.workspace_id != ws_id:
        raise HTTPException(status_code=404, detail="Job not found")
    return _task_to_response(task)


# ─── Cancel ───────────────────────────────────────────────────────────────────

@router.delete("/{id}", response_model=OkResponse)
async def cancel_job_endpoint(
    id: str,
    workspace: str = Depends(get_current_workspace),
    session: AsyncSession = Depends(get_session),
) -> OkResponse:
    try:
        task_id = uuid.UUID(id)
    except ValueError:
        raise HTTPException(status_code=422, detail="id must be a valid UUID")

    task = await session.get(JobTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Job not found")
    ws_id = await resolve_workspace_id(session, workspace)
    if task.workspace_id != ws_id:
        raise HTTPException(status_code=404, detail="Job not found")

    if task.status in ("completed", "failed", "cancelled"):
        return OkResponse(ok=True, message=f"Job already in terminal state: {task.status}")

    # Cancel in procrastinate if we have the procrastinate job id
    if task.procrastinate_job_id:
        try:
            from core.jobs.app import procrastinate_app
            await procrastinate_app.job_manager.cancel_job_by_id_async(
                task.procrastinate_job_id
            )
        except Exception as exc:
            logger.warning(f"Procrastinate cancel failed (non-fatal): {exc}")

    task.status = "cancelled"
    await session.commit()

    return OkResponse(ok=True, message="Job cancelled")

"""Todos CRUD API endpoints.

GET    /api/todos              — list todos (filter by workspace, status, priority)
POST   /api/todos              — create todo
GET    /api/todos/{todo_id}    — get one todo
PUT    /api/todos/{todo_id}    — update todo
DELETE /api/todos/{todo_id}    — delete todo
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_workspace
from api.schemas import TodoCreate, TodoUpdate, TodoResponse, OkResponse
from core.db.engine import get_session
from core.db.models import Todo, Workspace

router = APIRouter(prefix="/api/todos", tags=["todos"])
logger = logging.getLogger("api.todos")


async def _resolve_workspace_id(session: AsyncSession, workspace_name: str) -> uuid.UUID | None:
    result = await session.execute(
        select(Workspace.id).where(Workspace.name == workspace_name)
    )
    return result.scalar_one_or_none()


def _todo_to_response(todo: Todo) -> TodoResponse:
    return TodoResponse(
        id=str(todo.id),
        workspace_id=str(todo.workspace_id) if todo.workspace_id else None,
        title=todo.title,
        description=todo.description or "",
        priority=todo.priority or "medium",
        status=todo.status or "pending",
        due_date=todo.due_date.isoformat() if todo.due_date else None,
        completed_at=todo.completed_at.isoformat() if todo.completed_at else None,
        created_at=todo.created_at.isoformat() if todo.created_at else "",
        updated_at=todo.updated_at.isoformat() if todo.updated_at else "",
    )


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid datetime format: '{value}'. Use ISO 8601.")


@router.get("")
async def list_todos(
    workspace: str = Depends(get_current_workspace),
    status: str | None = Query(None, description="pending or done"),
    priority: str | None = Query(None, description="low/medium/high/urgent"),
    limit: int = Query(50, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
) -> dict:
    workspace_id = await _resolve_workspace_id(session, workspace)
    if workspace_id is None:
        raise HTTPException(status_code=404, detail=f"Workspace '{workspace}' not found.")

    stmt = select(Todo).where(Todo.workspace_id == workspace_id)

    if status:
        stmt = stmt.where(Todo.status == status)
    if priority:
        stmt = stmt.where(Todo.priority == priority)

    stmt = stmt.order_by(Todo.created_at.desc()).limit(limit)
    result = await session.execute(stmt)
    todos = result.scalars().all()

    return {
        "todos": [_todo_to_response(t).model_dump() for t in todos],
        "count": len(todos),
    }


@router.post("", status_code=201)
async def create_todo(
    body: TodoCreate,
    session: AsyncSession = Depends(get_session),
) -> TodoResponse:
    workspace_id = await _resolve_workspace_id(session, body.workspace)
    if workspace_id is None:
        raise HTTPException(status_code=404, detail=f"Workspace '{body.workspace}' not found.")

    todo = Todo(
        workspace_id=workspace_id,
        title=body.title,
        description=body.description,
        priority=body.priority,
        due_date=_parse_datetime(body.due_date),
    )
    session.add(todo)
    await session.commit()
    await session.refresh(todo)
    logger.info(f"Created todo '{todo.title}' (id={todo.id}) in workspace '{body.workspace}'.")

    # Enqueue auto-categorization
    try:
        from core.jobs.tasks.contexts import auto_categorize_item
        await auto_categorize_item.defer_async(
            target_type="todo",
            target_id=str(todo.id),
            workspace_id=str(workspace_id),
        )
    except Exception as exc:
        logger.warning("auto_categorize enqueue failed (non-fatal): %s", exc)

    return _todo_to_response(todo)


@router.get("/{todo_id}")
async def get_todo(
    todo_id: str,
    workspace: str = Depends(get_current_workspace),
    session: AsyncSession = Depends(get_session),
) -> TodoResponse:
    try:
        uid = uuid.UUID(todo_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid todo ID format.")

    result = await session.execute(select(Todo).where(Todo.id == uid))
    todo = result.scalar_one_or_none()
    if todo is None:
        raise HTTPException(status_code=404, detail=f"Todo '{todo_id}' not found.")
    ws_id = await _resolve_workspace_id(session, workspace)
    if ws_id is None or todo.workspace_id != ws_id:
        raise HTTPException(status_code=404, detail=f"Todo '{todo_id}' not found.")
    return _todo_to_response(todo)


@router.put("/{todo_id}")
async def update_todo(
    todo_id: str,
    body: TodoUpdate,
    workspace: str = Depends(get_current_workspace),
    session: AsyncSession = Depends(get_session),
) -> TodoResponse:
    try:
        uid = uuid.UUID(todo_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid todo ID format.")

    result = await session.execute(select(Todo).where(Todo.id == uid))
    todo = result.scalar_one_or_none()
    if todo is None:
        raise HTTPException(status_code=404, detail=f"Todo '{todo_id}' not found.")
    ws_id = await _resolve_workspace_id(session, workspace)
    if ws_id is None or todo.workspace_id != ws_id:
        raise HTTPException(status_code=404, detail=f"Todo '{todo_id}' not found.")

    if body.title is not None:
        todo.title = body.title
    if body.description is not None:
        todo.description = body.description
    if body.priority is not None:
        todo.priority = body.priority
    if body.due_date is not None:
        todo.due_date = _parse_datetime(body.due_date)
    if body.status is not None:
        todo.status = body.status
        if body.status == "done" and todo.completed_at is None:
            todo.completed_at = datetime.now(timezone.utc)
        elif body.status == "pending":
            todo.completed_at = None

    await session.commit()
    await session.refresh(todo)
    logger.info(f"Updated todo '{todo.title}' (id={todo.id}) — status={todo.status}.")
    return _todo_to_response(todo)


@router.delete("/{todo_id}")
async def delete_todo(
    todo_id: str,
    workspace: str = Depends(get_current_workspace),
    session: AsyncSession = Depends(get_session),
) -> OkResponse:
    try:
        uid = uuid.UUID(todo_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid todo ID format.")

    result = await session.execute(select(Todo).where(Todo.id == uid))
    todo = result.scalar_one_or_none()
    if todo is None:
        raise HTTPException(status_code=404, detail=f"Todo '{todo_id}' not found.")
    ws_id = await _resolve_workspace_id(session, workspace)
    if ws_id is None or todo.workspace_id != ws_id:
        raise HTTPException(status_code=404, detail=f"Todo '{todo_id}' not found.")

    await session.delete(todo)
    await session.commit()
    logger.info(f"Deleted todo {todo_id}.")
    return OkResponse(message=f"Todo '{todo_id}' deleted.")

"""Shared FastAPI dependencies."""
from __future__ import annotations

import uuid

from fastapi import Header, Query, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# TODO(strict): switch to require_workspace once all known clients send X-Workspace.


def get_current_workspace(
    x_workspace: str | None = Header(None, alias="X-Workspace"),
    workspace: str | None = Query(None),
) -> str:
    """Resolve current workspace: header first, then query param, default 'personal'."""
    if x_workspace and x_workspace.strip():
        return x_workspace.strip()
    if workspace and workspace.strip():
        return workspace.strip()
    return "personal"


def require_workspace(
    x_workspace: str | None = Header(None, alias="X-Workspace"),
    workspace: str | None = Query(None),
) -> str:
    """Same as get_current_workspace but 400 if neither provided."""
    if x_workspace and x_workspace.strip():
        return x_workspace.strip()
    if workspace and workspace.strip():
        return workspace.strip()
    raise HTTPException(status_code=400, detail="X-Workspace header required")


async def resolve_workspace_id(session: AsyncSession, workspace_name: str) -> uuid.UUID:
    """Resolve a workspace name to its UUID, raising HTTP 404 if the workspace does not exist.

    Shared helper for all routers that need to enforce workspace ownership on
    single-resource GET / PUT / DELETE operations.  LIST and CREATE endpoints
    that already have module-local helpers may continue to use those; this
    function is the canonical implementation.
    """
    from core.db.models import Workspace  # local import to avoid circular deps at module level

    result = await session.execute(
        select(Workspace.id).where(Workspace.name == workspace_name)
    )
    ws_id = result.scalar_one_or_none()
    if ws_id is None:
        raise HTTPException(status_code=404, detail=f"Workspace '{workspace_name}' not found.")
    return ws_id

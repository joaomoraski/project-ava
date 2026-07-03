"""Workspace CRUD API endpoints.

GET    /api/workspaces              — list all workspaces
POST   /api/workspaces              — create workspace
GET    /api/workspaces/{name}       — get workspace config
PUT    /api/workspaces/{name}       — update workspace config
DELETE /api/workspaces/{name}       — delete workspace (with confirm flag)
POST   /api/workspaces/{name}/tools — update enabled tools
POST   /api/workspaces/{name}/plugins — update enabled plugins
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.schemas import WorkspaceCreate, OkResponse
from core.db.engine import get_session
from core.workspace import (
    create_workspace,
    load_config,
    save_config,
    list_workspaces as _list_workspaces,
    workspace_exists,
    delete_workspace,
)

router = APIRouter(prefix="/api/workspaces", tags=["workspaces"])
logger = logging.getLogger("api.workspaces")


@router.get("")
async def list_workspaces(session: AsyncSession = Depends(get_session)) -> dict:
    workspaces = []
    for name in await _list_workspaces(session):
        try:
            cfg = await load_config(session, name)
            workspaces.append({
                "name": name,
                "system_prompt_preview": cfg.get("system_prompt", "")[:100],
                "stt_gate_mode": cfg.get("stt_gate_mode", "smart"),
                "plugins_enabled": cfg.get("plugins_enabled", []),
            })
        except Exception as e:
            logger.warning(f"Could not load workspace '{name}': {e}")
    return {"workspaces": workspaces, "count": len(workspaces)}


@router.post("")
async def create_workspace_endpoint(
    body: WorkspaceCreate,
    session: AsyncSession = Depends(get_session),
) -> dict:
    try:
        config = await create_workspace(
            session,
            name=body.name,
            config={
                "system_prompt": body.system_prompt,
                "stt_gate_mode": body.stt_gate_mode.value,
            },
        )
        return {"ok": True, "workspace": config}
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.get("/{name}")
async def get_workspace(
    name: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    if not await workspace_exists(session, name):
        raise HTTPException(status_code=404, detail=f"Workspace '{name}' not found.")
    try:
        return await load_config(session, name)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load workspace: {e}")


@router.put("/{name}")
async def update_workspace(
    name: str,
    body: dict,
    session: AsyncSession = Depends(get_session),
) -> OkResponse:
    if not await workspace_exists(session, name):
        raise HTTPException(status_code=404, detail=f"Workspace '{name}' not found.")
    try:
        config = await load_config(session, name)
        protected = {"name"}
        for key, value in body.items():
            if key not in protected:
                config[key] = value
        config["name"] = name
        await save_config(session, name, config)
        return OkResponse(message=f"Workspace '{name}' updated.")
    except Exception as e:
        logger.error(f"Failed to update workspace '{name}': {e}")
        raise HTTPException(status_code=500, detail=f"Update failed: {e}")


@router.delete("/{name}")
async def delete_workspace_endpoint(
    name: str,
    confirm: bool = Query(False),
    session: AsyncSession = Depends(get_session),
) -> OkResponse:
    if not await workspace_exists(session, name):
        raise HTTPException(status_code=404, detail=f"Workspace '{name}' not found.")
    if name == "personal":
        raise HTTPException(status_code=400, detail="Cannot delete the default 'personal' workspace.")
    if not confirm:
        raise HTTPException(
            status_code=400,
            detail="Add ?confirm=true to confirm deletion. This action is irreversible.",
        )
    try:
        await delete_workspace(session, name)
        return OkResponse(message=f"Workspace '{name}' deleted.")
    except Exception as e:
        logger.error(f"Failed to delete workspace '{name}': {e}")
        raise HTTPException(status_code=500, detail=f"Deletion failed: {e}")


@router.post("/{name}/plugins")
async def set_workspace_plugins(
    name: str,
    body: dict,
    session: AsyncSession = Depends(get_session),
) -> OkResponse:
    if not await workspace_exists(session, name):
        raise HTTPException(status_code=404, detail=f"Workspace '{name}' not found.")
    plugins = body.get("plugins_enabled")
    if not isinstance(plugins, list):
        raise HTTPException(status_code=422, detail="plugins_enabled must be a list of strings.")
    await save_config(session, name, {"plugins_enabled": [str(p) for p in plugins]})
    return OkResponse(message=f"Plugins updated for workspace '{name}'.")


@router.post("/{name}/tools")
async def set_workspace_tools(
    name: str,
    body: dict,
    session: AsyncSession = Depends(get_session),
) -> OkResponse:
    if not await workspace_exists(session, name):
        raise HTTPException(status_code=404, detail=f"Workspace '{name}' not found.")
    tools = body.get("tools_enabled")
    if not isinstance(tools, list):
        raise HTTPException(status_code=422, detail="tools_enabled must be a list of strings.")
    await save_config(session, name, {"tools_enabled": [str(t) for t in tools]})
    return OkResponse(message=f"Tools updated for workspace '{name}'.")

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
import shutil
import os

from fastapi import APIRouter, HTTPException, Query

from api.schemas import WorkspaceCreate, WorkspaceConfig, WorkspaceSummary, OkResponse
from core.workspace import (
    create_workspace,
    load_config,
    save_config,
    list_workspaces as _list_workspaces,
    workspace_exists,
    validate_name,
    WORKSPACES_DIR,
)

router = APIRouter(prefix="/api/workspaces", tags=["workspaces"])
logger = logging.getLogger("api.workspaces")


@router.get("")
async def list_workspaces() -> dict:
    """List all workspaces with summary info."""
    workspaces = []
    for name in _list_workspaces():
        try:
            cfg = load_config(name)
            prompt_preview = cfg.get("system_prompt", "")[:100]
            workspaces.append({
                "name": name,
                "system_prompt_preview": prompt_preview,
                "stt_gate_mode": cfg.get("stt_gate_mode", "smart"),
                "plugins_enabled": cfg.get("plugins_enabled", []),
            })
        except Exception as e:
            logger.warning(f"Could not load workspace '{name}': {e}")
    return {"workspaces": workspaces, "count": len(workspaces)}


@router.post("")
async def create_workspace_endpoint(body: WorkspaceCreate) -> dict:
    """Create a new workspace."""
    try:
        config = create_workspace(
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
async def get_workspace(name: str) -> dict:
    """Get full workspace configuration."""
    if not workspace_exists(name):
        raise HTTPException(status_code=404, detail=f"Workspace '{name}' not found.")
    try:
        config = load_config(name)
        return config
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load workspace: {e}")


@router.put("/{name}")
async def update_workspace(name: str, body: dict) -> OkResponse:
    """Update workspace configuration (partial update — only provided fields are changed)."""
    if not workspace_exists(name):
        raise HTTPException(status_code=404, detail=f"Workspace '{name}' not found.")

    try:
        config = load_config(name)
        # Merge: only update provided fields
        protected = {"name"}  # name cannot be changed via PUT
        for key, value in body.items():
            if key not in protected:
                config[key] = value
        config["name"] = name  # ensure name is always correct
        save_config(name, config)
        return OkResponse(message=f"Workspace '{name}' updated.")
    except Exception as e:
        logger.error(f"Failed to update workspace '{name}': {e}")
        raise HTTPException(status_code=500, detail=f"Update failed: {e}")


@router.delete("/{name}")
async def delete_workspace(
    name: str,
    confirm: bool = Query(False),
) -> OkResponse:
    """Delete a workspace. Requires ?confirm=true to prevent accidents."""
    if not workspace_exists(name):
        raise HTTPException(status_code=404, detail=f"Workspace '{name}' not found.")

    if name == "personal":
        raise HTTPException(status_code=400, detail="Cannot delete the default 'personal' workspace.")

    if not confirm:
        raise HTTPException(
            status_code=400,
            detail="Add ?confirm=true to confirm deletion. This action is irreversible.",
        )

    try:
        workspace_dir = os.path.join(WORKSPACES_DIR, name)
        shutil.rmtree(workspace_dir)
        return OkResponse(message=f"Workspace '{name}' deleted.")
    except Exception as e:
        logger.error(f"Failed to delete workspace '{name}': {e}")
        raise HTTPException(status_code=500, detail=f"Deletion failed: {e}")


@router.post("/{name}/plugins")
async def set_workspace_plugins(name: str, body: dict) -> OkResponse:
    """Update the list of enabled plugins for a workspace.

    Body: {"plugins_enabled": ["todoist", "notion"]}
    """
    if not workspace_exists(name):
        raise HTTPException(status_code=404, detail=f"Workspace '{name}' not found.")

    plugins = body.get("plugins_enabled")
    if not isinstance(plugins, list):
        raise HTTPException(status_code=422, detail="plugins_enabled must be a list of strings.")

    config = load_config(name)
    config["plugins_enabled"] = [str(p) for p in plugins]
    save_config(name, config)
    return OkResponse(message=f"Plugins updated for workspace '{name}'.")


@router.post("/{name}/tools")
async def set_workspace_tools(name: str, body: dict) -> OkResponse:
    """Update the list of enabled tools for a workspace.

    Body: {"tools_enabled": ["web_search", "system_control"]}
    """
    if not workspace_exists(name):
        raise HTTPException(status_code=404, detail=f"Workspace '{name}' not found.")

    tools = body.get("tools_enabled")
    if not isinstance(tools, list):
        raise HTTPException(status_code=422, detail="tools_enabled must be a list of strings.")

    config = load_config(name)
    config["tools_enabled"] = [str(t) for t in tools]
    save_config(name, config)
    return OkResponse(message=f"Tools updated for workspace '{name}'.")

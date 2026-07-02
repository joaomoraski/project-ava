"""Plugin management API endpoints."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from api.schemas import OkResponse, PluginEnableRequest, PluginInstallRequest
from core.db.engine import get_session
from core.plugins.plugin_loader import (
    install_plugin,
    uninstall_plugin,
    list_installed,
    list_available,
    load_manifest,
)
from core.plugins.mcp_manager import mcp_manager
from core.workspace import workspace_exists, load_config, save_config

router = APIRouter(prefix="/api/plugins", tags=["plugins"])
logger = logging.getLogger("api.plugins")


@router.get("")
async def list_plugins() -> dict:
    installed = list_installed()
    for plugin in installed:
        server_status = mcp_manager.get_server_status(plugin["name"])
        plugin["status"] = server_status.get("status", "stopped")
        plugin["tools_count"] = server_status.get("tools_count", 0)
    return {"plugins": installed, "count": len(installed)}


@router.get("/available")
async def list_available_plugins() -> dict:
    return {"presets": list_available()}


@router.post("/install")
async def install_plugin_endpoint(body: PluginInstallRequest) -> dict:
    if not body.name and not body.mcp_config:
        raise HTTPException(status_code=422, detail="Provide either 'name' or 'mcp_config'.")

    plugin_name = body.name or body.mcp_config.get("name", "custom")

    try:
        manifest = install_plugin(
            name=plugin_name,
            custom_config=body.mcp_config,
        )
        return {"ok": True, "plugin": plugin_name, "manifest": manifest}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Plugin install failed for '{plugin_name}': {e}")
        raise HTTPException(status_code=500, detail=f"Install failed: {e}")


@router.delete("/{name}")
async def uninstall_plugin_endpoint(name: str) -> OkResponse:
    uninstall_plugin(name)
    return OkResponse(message=f"Plugin '{name}' uninstalled.")


@router.put("/{name}/config")
async def configure_plugin(name: str, body: dict) -> OkResponse:
    manifest = load_manifest(name)
    if not manifest:
        raise HTTPException(status_code=404, detail=f"Plugin '{name}' not found.")

    if "command" in body or "args" in body or "env" in body:
        mcp_manager.update_server(name, {
            k: body[k] for k in ("command", "args", "env", "description") if k in body
        })

    return OkResponse(message=f"Plugin '{name}' config updated.")


@router.post("/{name}/enable")
async def enable_plugin(
    name: str,
    body: PluginEnableRequest,
    session: AsyncSession = Depends(get_session),
) -> OkResponse:
    if not await workspace_exists(session, body.workspace):
        raise HTTPException(status_code=404, detail=f"Workspace '{body.workspace}' not found.")

    manifest = load_manifest(name)
    if not manifest:
        raise HTTPException(status_code=404, detail=f"Plugin '{name}' not found.")

    config = await load_config(session, body.workspace)
    enabled = config.get("plugins_enabled", [])
    if name not in enabled:
        enabled.append(name)
        await save_config(session, body.workspace, {"plugins_enabled": enabled})

    return OkResponse(message=f"Plugin '{name}' enabled for workspace '{body.workspace}'.")


@router.post("/{name}/disable")
async def disable_plugin(
    name: str,
    body: PluginEnableRequest,
    session: AsyncSession = Depends(get_session),
) -> OkResponse:
    if not await workspace_exists(session, body.workspace):
        raise HTTPException(status_code=404, detail=f"Workspace '{body.workspace}' not found.")

    config = await load_config(session, body.workspace)
    enabled = config.get("plugins_enabled", [])
    if name in enabled:
        enabled.remove(name)
        await save_config(session, body.workspace, {"plugins_enabled": enabled})

    return OkResponse(message=f"Plugin '{name}' disabled for workspace '{body.workspace}'.")


@router.get("/{name}/status")
async def plugin_status(name: str) -> dict:
    manifest = load_manifest(name)
    if not manifest:
        raise HTTPException(status_code=404, detail=f"Plugin '{name}' not found.")

    server_status = mcp_manager.get_server_status(name)
    tools = mcp_manager.get_tools(name)

    return {
        "name": name,
        "description": manifest.get("description", ""),
        "type": manifest.get("type", "mcp"),
        "status": server_status.get("status", "stopped"),
        "uptime_seconds": server_status.get("uptime_seconds"),
        "tools_count": len(tools),
        "tools": [{"name": t.name, "description": t.description} for t in tools],
        "last_error": server_status.get("last_error"),
    }

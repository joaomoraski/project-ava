"""Plugin management API endpoints.

GET    /api/plugins                 — list all plugins (installed + available)
GET    /api/plugins/available       — list known installable presets
POST   /api/plugins/install         — install a plugin
DELETE /api/plugins/{name}          — uninstall plugin
PUT    /api/plugins/{name}/config   — update plugin config/env vars
POST   /api/plugins/{name}/enable   — enable for a workspace
POST   /api/plugins/{name}/disable  — disable for a workspace
GET    /api/plugins/{name}/status   — plugin health + available tools
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from api.schemas import OkResponse, PluginEnableRequest, PluginInstallRequest
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
    """List installed plugins with their status per workspace."""
    installed = list_installed()
    for plugin in installed:
        server_status = mcp_manager.get_server_status(plugin["name"])
        plugin["status"] = server_status.get("status", "stopped")
        plugin["tools_count"] = server_status.get("tools_count", 0)
    return {"plugins": installed, "count": len(installed)}


@router.get("/available")
async def list_available_plugins() -> dict:
    """List all known installable plugin presets."""
    return {"presets": list_available()}


@router.post("/install")
async def install_plugin_endpoint(body: PluginInstallRequest) -> dict:
    """Install a plugin by name (preset) or custom MCP config."""
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
    """Uninstall a plugin and remove its MCP server config."""
    uninstall_plugin(name)
    return OkResponse(message=f"Plugin '{name}' uninstalled.")


@router.put("/{name}/config")
async def configure_plugin(name: str, body: dict) -> OkResponse:
    """Update plugin configuration (env vars, MCP server settings).

    This updates the MCP server config for the plugin.
    Env vars containing secrets should be set via /api/secrets/set instead.
    """
    manifest = load_manifest(name)
    if not manifest:
        raise HTTPException(status_code=404, detail=f"Plugin '{name}' not found.")

    # Update MCP server config if provided
    if "command" in body or "args" in body or "env" in body:
        mcp_manager.update_server(name, {
            k: body[k] for k in ("command", "args", "env", "description") if k in body
        })

    return OkResponse(message=f"Plugin '{name}' config updated.")


@router.post("/{name}/enable")
async def enable_plugin(name: str, body: PluginEnableRequest) -> OkResponse:
    """Enable a plugin for a specific workspace."""
    if not workspace_exists(body.workspace):
        raise HTTPException(status_code=404, detail=f"Workspace '{body.workspace}' not found.")

    manifest = load_manifest(name)
    if not manifest:
        raise HTTPException(status_code=404, detail=f"Plugin '{name}' not found.")

    config = load_config(body.workspace)
    enabled = config.get("plugins_enabled", [])
    if name not in enabled:
        enabled.append(name)
        config["plugins_enabled"] = enabled
        save_config(body.workspace, config)

    return OkResponse(message=f"Plugin '{name}' enabled for workspace '{body.workspace}'.")


@router.post("/{name}/disable")
async def disable_plugin(name: str, body: PluginEnableRequest) -> OkResponse:
    """Disable a plugin for a specific workspace."""
    if not workspace_exists(body.workspace):
        raise HTTPException(status_code=404, detail=f"Workspace '{body.workspace}' not found.")

    config = load_config(body.workspace)
    enabled = config.get("plugins_enabled", [])
    if name in enabled:
        enabled.remove(name)
        config["plugins_enabled"] = enabled
        save_config(body.workspace, config)

    return OkResponse(message=f"Plugin '{name}' disabled for workspace '{body.workspace}'.")


@router.get("/{name}/status")
async def plugin_status(name: str) -> dict:
    """Get plugin health, running status, and available tools."""
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

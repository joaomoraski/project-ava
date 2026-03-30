"""MCP server management API endpoints.

GET    /api/mcp/servers                  — list all registered servers + status
POST   /api/mcp/servers                  — register a new MCP server
PUT    /api/mcp/servers/{name}           — update server config
DELETE /api/mcp/servers/{name}           — unregister server
POST   /api/mcp/servers/{name}/start     — start server
POST   /api/mcp/servers/{name}/stop      — stop server
GET    /api/mcp/servers/{name}/tools     — list tools exposed by server
GET    /api/mcp/servers/{name}/health    — health check + uptime
GET    /api/mcp/servers/{name}/logs      — recent stderr logs
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from api.schemas import McpServerCreate, OkResponse
from core.plugins.mcp_manager import mcp_manager

router = APIRouter(prefix="/api/mcp", tags=["mcp"])
logger = logging.getLogger("api.mcp")


@router.get("/servers")
async def list_servers() -> dict:
    """List all registered MCP servers with their current status."""
    servers = mcp_manager.list_servers()
    return {"servers": servers, "count": len(servers)}


@router.post("/servers")
async def register_server(body: McpServerCreate) -> dict:
    """Register a new MCP server."""
    config = {
        "command": body.command,
        "args": body.args,
        "env": body.env,
        "description": body.description,
        "enabled": True,
        "auto_start": body.auto_start,
    }
    success = mcp_manager.register_server(body.name, config)
    if not success:
        raise HTTPException(status_code=409, detail=f"Server '{body.name}' already registered.")
    return {"ok": True, "name": body.name}


@router.put("/servers/{name}")
async def update_server(name: str, body: dict) -> OkResponse:
    """Update an existing MCP server config."""
    success = mcp_manager.update_server(name, body)
    if not success:
        raise HTTPException(status_code=404, detail=f"Server '{name}' not found.")
    return OkResponse(message=f"Server '{name}' updated.")


@router.delete("/servers/{name}")
async def delete_server(name: str) -> OkResponse:
    """Unregister and stop an MCP server."""
    mcp_manager.unregister_server(name)
    return OkResponse(message=f"Server '{name}' unregistered.")


@router.post("/servers/{name}/start")
async def start_server(name: str) -> dict:
    """Start a registered MCP server."""
    success = mcp_manager.start_server(name)
    if not success:
        status = mcp_manager.get_server_status(name)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to start '{name}': {status.get('last_error', 'unknown error')}",
        )
    status = mcp_manager.get_server_status(name)
    return {"ok": True, "pid": status.get("pid")}


@router.post("/servers/{name}/stop")
async def stop_server(name: str) -> OkResponse:
    """Stop a running MCP server."""
    mcp_manager.stop_server(name)
    return OkResponse(message=f"Server '{name}' stopped.")


@router.get("/servers/{name}/tools")
async def list_server_tools(name: str) -> dict:
    """List all tools exposed by a running MCP server."""
    tools = mcp_manager.get_tools(name)
    return {
        "name": name,
        "tools": [
            {
                "name": t.name,
                "description": t.description,
                "parameters": getattr(t, "args_schema", {}) or {},
            }
            for t in tools
        ],
        "count": len(tools),
    }


@router.get("/servers/{name}/health")
async def server_health(name: str) -> dict:
    """Health check for an MCP server."""
    status = mcp_manager.get_server_status(name)
    if not status:
        raise HTTPException(status_code=404, detail=f"Server '{name}' not registered.")
    return {
        "name": name,
        "status": status.get("status", "unknown"),
        "uptime_seconds": status.get("uptime_seconds"),
        "last_error": status.get("last_error"),
        "pid": status.get("pid"),
    }


@router.get("/servers/{name}/logs")
async def server_logs(name: str) -> dict:
    """Get recent stderr logs from an MCP server."""
    logs = mcp_manager.get_server_logs(name)
    return {"name": name, "lines": logs}

"""MCP server management API endpoints.

GET    /api/mcp/servers                       — list all registered servers + status
POST   /api/mcp/servers                       — register a new MCP server
PUT    /api/mcp/servers/{name}                — update server config
DELETE /api/mcp/servers/{name}                — unregister server
POST   /api/mcp/servers/{name}/start          — start server
POST   /api/mcp/servers/{name}/stop           — stop server
GET    /api/mcp/servers/{name}/tools          — list tools (with error + discovered_at)
GET    /api/mcp/servers/{name}/health         — health check + uptime
GET    /api/mcp/servers/{name}/logs           — recent stderr logs
GET    /api/mcp/servers/{name}/oauth/start    — get OAuth redirect URL
GET    /api/mcp/servers/{name}/oauth/callback — exchange OAuth code for token
"""
from __future__ import annotations

import logging
import urllib.parse

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.schemas import McpServerCreate, OkResponse
from core.db.engine import get_session
from core.plugins.mcp_manager import mcp_manager

router = APIRouter(prefix="/api/mcp", tags=["mcp"])
logger = logging.getLogger("api.mcp")

# Callback base URL — the frontend's OAuth redirect lands here
_CALLBACK_BASE = "http://localhost:8471"


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
        # OAuth fields (optional)
        "oauth_url": body.oauth_url,
        "oauth_client_id": body.oauth_client_id,
        "oauth_scopes": body.oauth_scopes,
        "oauth_client_secret_ref": body.oauth_client_secret_ref,
    }
    # Strip None OAuth fields to keep servers.json clean
    config = {k: v for k, v in config.items() if v is not None or k in ("command", "args", "env", "description", "enabled", "auto_start")}
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
async def start_server(
    name: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Start a registered MCP server (resolves secrets from vault)."""
    success = await mcp_manager.start_server(name, session=session)
    if not success:
        status = mcp_manager.get_server_status(name)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to start '{name}': {status.get('error', 'unknown error')}",
        )
    status = mcp_manager.get_server_status(name)
    return {
        "ok": True,
        "pid": status.get("pid"),
        "tools_count": status.get("tools_count", 0),
        "error": status.get("error"),
    }


@router.post("/servers/{name}/stop")
async def stop_server(name: str) -> OkResponse:
    """Stop a running MCP server."""
    mcp_manager.stop_server(name)
    return OkResponse(message=f"Server '{name}' stopped.")


@router.get("/servers/{name}/tools")
async def list_server_tools(name: str) -> dict:
    """List all tools exposed by an MCP server.

    Includes error details if tool discovery failed, and the timestamp of
    the last successful discovery.
    """
    info = mcp_manager.get_tool_discovery_info(name)
    return {
        "name": name,
        "tools": info["tools"],
        "count": len(info["tools"]),
        "error": info["error"],
        "discovered_at": info["discovered_at"],
    }


@router.get("/servers/{name}/health")
async def server_health(name: str) -> dict:
    """Health check for an MCP server."""
    config = mcp_manager._config.get("servers", {}).get(name)
    if config is None:
        raise HTTPException(status_code=404, detail=f"Server '{name}' not registered.")
    status = mcp_manager.get_server_status(name)
    return {
        "name": name,
        "status": status.get("status", "unknown"),
        "uptime_seconds": status.get("uptime_seconds"),
        "error": status.get("error"),
        "pid": status.get("pid"),
        "tools_count": status.get("tools_count", 0),
        "tools_discovered": status.get("tools_count", 0) > 0,
    }


@router.get("/servers/{name}/logs")
async def server_logs(name: str) -> dict:
    """Get recent stderr logs from an MCP server."""
    logs = mcp_manager.get_server_logs(name)
    return {"name": name, "lines": logs}


# ─── OAuth endpoints ──────────────────────────────────────────────────────────

@router.get("/servers/{name}/oauth/start")
async def oauth_start(name: str) -> dict:
    """Return the OAuth authorization URL for this MCP server.

    The server config must have oauth_url, oauth_client_id, and optionally
    oauth_scopes set.  The redirect_uri is always pointed back to the
    backend callback endpoint so the token exchange can happen server-side.
    """
    config = mcp_manager._config.get("servers", {}).get(name)
    if config is None:
        raise HTTPException(status_code=404, detail=f"Server '{name}' not registered.")

    oauth_url = config.get("oauth_url")
    if not oauth_url:
        raise HTTPException(
            status_code=400,
            detail=f"Server '{name}' has no oauth_url configured.",
        )

    client_id = config.get("oauth_client_id")
    if not client_id:
        raise HTTPException(
            status_code=400,
            detail=f"Server '{name}' has no oauth_client_id configured.",
        )

    callback_url = f"{_CALLBACK_BASE}/api/mcp/servers/{name}/oauth/callback"
    params: dict[str, str] = {
        "client_id": client_id,
        "redirect_uri": callback_url,
        "response_type": "code",
    }
    scopes = config.get("oauth_scopes")
    if scopes:
        params["scope"] = scopes

    redirect_url = oauth_url + "?" + urllib.parse.urlencode(params)
    return {
        "redirect_url": redirect_url,
        "callback_url": callback_url,
        "server": name,
    }


@router.get("/servers/{name}/oauth/callback")
async def oauth_callback(
    name: str,
    code: str = Query(..., description="Authorization code from OAuth provider"),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Exchange the OAuth authorization code for an access token.

    Stores the token as an encrypted secret named `{NAME}_OAUTH_TOKEN`
    so that the MCP server env can reference it as `${NAME_OAUTH_TOKEN}`.
    """
    import httpx

    config = mcp_manager._config.get("servers", {}).get(name)
    if config is None:
        raise HTTPException(status_code=404, detail=f"Server '{name}' not registered.")

    oauth_url = config.get("oauth_url")
    client_id = config.get("oauth_client_id")
    if not oauth_url or not client_id:
        raise HTTPException(
            status_code=400,
            detail=f"Server '{name}' OAuth not fully configured.",
        )

    # Derive the token endpoint — common convention: swap /authorize → /token
    token_url = oauth_url.replace("/authorize", "/token").replace("/auth", "/token")

    # Resolve client secret from vault
    client_secret = ""
    secret_ref = config.get("oauth_client_secret_ref")
    if secret_ref:
        try:
            from core.secrets.vault import SecretsVault
            vault = SecretsVault()
            client_secret = await vault.get_secret(session, secret_ref) or ""
        except Exception as e:
            logger.warning(f"Could not resolve OAuth client secret for '{name}': {e}")

    callback_url = f"{_CALLBACK_BASE}/api/mcp/servers/{name}/oauth/callback"

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(token_url, data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": callback_url,
                "client_id": client_id,
                "client_secret": client_secret,
            })
            resp.raise_for_status()
            token_data = resp.json()
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=502,
            detail=f"Token exchange failed: HTTP {e.response.status_code}",
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Token exchange error: {e}")

    access_token = token_data.get("access_token")
    if not access_token:
        raise HTTPException(
            status_code=502,
            detail=f"Token exchange response missing access_token: {list(token_data.keys())}",
        )

    # Store token as encrypted secret: NAME_OAUTH_TOKEN (uppercase, hyphens→underscores)
    secret_name = f"{name.upper().replace('-', '_')}_OAUTH_TOKEN"
    try:
        from core.secrets.vault import SecretsVault
        vault = SecretsVault()
        await vault.set_secret(session, secret_name, access_token)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to store token: {e}")

    logger.info(f"OAuth token for '{name}' stored as secret '{secret_name}'.")
    return {
        "ok": True,
        "secret_name": secret_name,
        "message": (
            f"Token stored as secret '{secret_name}'. "
            f"Reference it in the server env as ${{{secret_name}}}."
        ),
    }

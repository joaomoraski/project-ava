"""MCP server management endpoints — implemented in B8."""
from fastapi import APIRouter
from api.schemas import OkResponse

router = APIRouter(prefix="/api/mcp", tags=["mcp"])


@router.get("/servers")
async def list_servers() -> dict:
    return {"servers": [], "note": "Full implementation in phase B8"}


@router.post("/servers")
async def register_server(body: dict) -> OkResponse:
    return OkResponse(message="Not yet implemented — see phase B8")


@router.put("/servers/{name}")
async def update_server(name: str, body: dict) -> OkResponse:
    return OkResponse(message="Not yet implemented — see phase B8")


@router.delete("/servers/{name}")
async def delete_server(name: str) -> OkResponse:
    return OkResponse(message="Not yet implemented — see phase B8")


@router.post("/servers/{name}/start")
async def start_server(name: str) -> OkResponse:
    return OkResponse(message="Not yet implemented — see phase B8")


@router.post("/servers/{name}/stop")
async def stop_server(name: str) -> OkResponse:
    return OkResponse(message="Not yet implemented — see phase B8")


@router.get("/servers/{name}/tools")
async def list_server_tools(name: str) -> dict:
    return {"tools": [], "note": "Full implementation in phase B8"}


@router.get("/servers/{name}/health")
async def server_health(name: str) -> dict:
    return {"status": "unknown", "note": "Full implementation in phase B8"}


@router.get("/servers/{name}/logs")
async def server_logs(name: str) -> dict:
    return {"lines": [], "note": "Full implementation in phase B8"}

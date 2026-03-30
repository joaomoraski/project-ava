"""Workspace CRUD endpoints — implemented in B6."""
from fastapi import APIRouter
from api.schemas import OkResponse

router = APIRouter(prefix="/api/workspaces", tags=["workspaces"])


@router.get("")
async def list_workspaces() -> dict:
    """List all workspaces. Full implementation in B6."""
    return {"workspaces": [], "note": "Full implementation in phase B6"}


@router.post("")
async def create_workspace(body: dict) -> OkResponse:
    return OkResponse(message="Not yet implemented — see phase B6")


@router.get("/{name}")
async def get_workspace(name: str) -> dict:
    return {"note": f"Full implementation in phase B6 for workspace '{name}'"}


@router.put("/{name}")
async def update_workspace(name: str, body: dict) -> OkResponse:
    return OkResponse(message="Not yet implemented — see phase B6")


@router.delete("/{name}")
async def delete_workspace(name: str) -> OkResponse:
    return OkResponse(message="Not yet implemented — see phase B6")

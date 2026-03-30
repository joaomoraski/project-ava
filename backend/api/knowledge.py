"""Knowledge base endpoints — implemented in B7."""
from fastapi import APIRouter
from api.schemas import OkResponse

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


@router.get("/sources")
async def list_sources(workspace: str | None = None) -> dict:
    """List indexed sources. Full implementation in B7."""
    return {"sources": [], "note": "Full implementation in phase B7"}


@router.post("/upload")
async def upload_file() -> OkResponse:
    return OkResponse(message="Not yet implemented — see phase B7")


@router.post("/seed")
async def seed_knowledge(body: dict) -> OkResponse:
    return OkResponse(message="Not yet implemented — see phase B7")


@router.get("/seed/status")
async def seed_status() -> dict:
    return {"status": "idle", "note": "Full implementation in phase B7"}


@router.post("/search")
async def search_knowledge(body: dict) -> dict:
    return {"results": [], "note": "Full implementation in phase B7"}

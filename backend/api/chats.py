"""Chat history endpoints — implemented in B5."""
from fastapi import APIRouter
from api.schemas import OkResponse

router = APIRouter(prefix="/api/chats", tags=["chats"])


@router.get("")
async def list_chats(workspace: str | None = None, limit: int = 20) -> dict:
    """List chat sessions. Full implementation in B5."""
    return {"sessions": [], "note": "Full implementation in phase B5"}


@router.get("/{session_id}")
async def get_chat(session_id: str, workspace: str | None = None) -> dict:
    return {"note": f"Full implementation in phase B5 for session '{session_id}'"}


@router.post("/search")
async def search_chats(body: dict) -> dict:
    return {"results": [], "note": "Full implementation in phase B5"}

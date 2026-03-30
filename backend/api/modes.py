"""Mode management endpoints."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter

from api.schemas import ModeEnum, ModeStatus, ModeSwitchRequest, OkResponse
from core.config import settings

logger = logging.getLogger("api.modes")

router = APIRouter(prefix="/api/modes", tags=["modes"])

# Runtime state — will be replaced by state_machine in B11
_current_mode: ModeEnum = ModeEnum(settings.default_mode)
_current_workspace: str = settings.default_workspace
_changed_at: datetime | None = None


@router.get("/current", response_model=ModeStatus)
async def get_current_mode() -> ModeStatus:
    return ModeStatus(
        mode=_current_mode,
        workspace=_current_workspace,
        changed_at=_changed_at,
    )


@router.post("/switch", response_model=OkResponse)
async def switch_mode(request: ModeSwitchRequest) -> OkResponse:
    global _current_mode, _changed_at

    old_mode = _current_mode
    _current_mode = request.mode
    _changed_at = datetime.now(timezone.utc)

    logger.info(f"Mode changed: {old_mode} → {_current_mode}")

    # Broadcast to all WebSocket clients
    try:
        from api.ws import ws_manager
        await ws_manager.broadcast({
            "type": "mode_change",
            "mode": _current_mode.value,
            "workspace": _current_workspace,
            "changed_at": _changed_at.isoformat(),
        })
    except Exception as e:
        logger.warning(f"Failed to broadcast mode change: {e}")

    return OkResponse(message=f"Mode switched to {_current_mode.value}")


def get_current_mode_value() -> ModeEnum:
    return _current_mode


def get_current_workspace() -> str:
    return _current_workspace

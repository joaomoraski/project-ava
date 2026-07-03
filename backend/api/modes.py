"""Mode management endpoints."""
from __future__ import annotations

import logging

from fastapi import APIRouter

from api.schemas import ModeStatus, ModeSwitchRequest, OkResponse
from core.state_machine import state_machine

logger = logging.getLogger("api.modes")

router = APIRouter(prefix="/api/modes", tags=["modes"])


@router.get("/current", response_model=ModeStatus)
async def get_current_mode() -> ModeStatus:
    return ModeStatus(
        mode=state_machine.mode.value,
        workspace=state_machine.workspace,
        changed_at=None,
        meeting_id=state_machine._active_meeting_id,
        pre_meeting_mode=state_machine._pre_meeting_mode,
    )


@router.post("/switch", response_model=dict)
async def switch_mode(request: ModeSwitchRequest) -> dict:
    old_mode = state_machine.mode.value
    target_mode = request.mode.value

    # If leaving meeting mode, restore the mode that was active before meeting
    if old_mode == "meeting" and target_mode != "meeting":
        pre = state_machine._pre_meeting_mode
        if pre and pre != "meeting":
            target_mode = pre
            logger.info(f"Restoring pre-meeting mode: {pre}")

    await state_machine.transition(target_mode)
    logger.info(f"Mode changed: {old_mode} → {target_mode}")
    result: dict = {"ok": True, "message": f"Mode switched to {target_mode}", "mode": target_mode}
    if target_mode == "meeting" and state_machine._active_meeting_id:
        result["meeting_id"] = state_machine._active_meeting_id
    return result

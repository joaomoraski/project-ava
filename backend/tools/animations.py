"""Animation trigger tool — sends animation events to avatar via WebSocket."""
from __future__ import annotations

import asyncio
import logging

from langchain_core.tools import tool

logger = logging.getLogger("tools.animations")


@tool
def trigger_animation(animation_name: str) -> str:
    """Trigger a named animation on the avatar (if connected).

    Available animations: wave, nod, shake, think, celebrate, idle.

    Args:
        animation_name: name of the animation to play
    """
    KNOWN_ANIMATIONS = {"wave", "nod", "shake", "think", "celebrate", "idle", "dance"}

    if animation_name not in KNOWN_ANIMATIONS:
        return f"Unknown animation: {animation_name}. Available: {', '.join(sorted(KNOWN_ANIMATIONS))}"

    try:
        from api.ws import ws_manager
        if not ws_manager.has_avatar_client():
            return f"Animation '{animation_name}' triggered (no avatar connected — skipped)."

        # Run the async broadcast in the event loop
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(
                ws_manager.broadcast(
                    {"type": "animation", "name": animation_name},
                    avatar_only=True,
                )
            )
        return f"Animation '{animation_name}' triggered."
    except Exception as e:
        logger.error(f"trigger_animation failed: {e}")
        return f"Failed to trigger animation: {e}"

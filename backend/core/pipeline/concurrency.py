"""Pipeline concurrency: barge-in handling and mode lock."""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

logger = logging.getLogger("core.pipeline.concurrency")


class PipelineController:
    """Manages concurrent pipeline operations with cancellation support.

    - Barge-in: user speaks while assistant is speaking → cancel LLM task,
      call player.clear_queue() to flush audio queue and stop playback.
    - Mode transitions: protected by asyncio.Lock.
    """

    def __init__(self) -> None:
        self._current_response: asyncio.Task | None = None
        self._mode_lock = asyncio.Lock()
        self._barge_in_lock = asyncio.Lock()
        self._local_player = None  # LocalAudioPlayer, set when TTS is loaded
        self._ws_manager = None   # WebSocketManager, set when WS manager available
        self._vad = None           # SileroVAD, set when VAD is loaded

    def set_local_player(self, player) -> None:
        self._local_player = player

    def set_ws_manager(self, ws_manager) -> None:
        self._ws_manager = ws_manager

    def set_vad(self, vad) -> None:
        self._vad = vad

    async def handle_barge_in(self) -> None:
        """Called when VAD detects user started speaking during response."""
        async with self._barge_in_lock:
            logger.debug("Barge-in detected.")

            # 1. Cancel current LLM+TTS task
            if self._current_response and not self._current_response.done():
                self._current_response.cancel()
                try:
                    await self._current_response
                except asyncio.CancelledError:
                    pass

            # 2. Flush audio queue + stop sounddevice playback
            if self._local_player:
                self._local_player.clear_queue()
                try:
                    import sounddevice as sd
                    sd.stop()
                except Exception:
                    pass

            # 3. Reset VAD internal state so it starts fresh
            if self._vad is not None and hasattr(self._vad, "reset"):
                self._vad.reset()

            logger.debug("Barge-in handled: pipeline reset.")

    async def run_response(self, coro: Callable[[], Awaitable]) -> None:
        """Run a response coroutine, cancelling any previous one."""
        if self._current_response and not self._current_response.done():
            self._current_response.cancel()
            try:
                await self._current_response
            except asyncio.CancelledError:
                pass
        self._current_response = asyncio.create_task(coro())
        try:
            await self._current_response
        except asyncio.CancelledError:
            logger.debug("Response task cancelled.")

    async def change_mode(self, new_mode: str, workspace: str | None = None) -> None:
        """Atomic mode transition."""
        async with self._mode_lock:
            from core.state_machine import state_machine
            await state_machine.transition(new_mode, workspace)
            logger.info(f"Mode transitioned to: {new_mode}")


# Global singleton
pipeline_controller = PipelineController()

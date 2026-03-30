"""Pipeline concurrency: barge-in handling, mode lock, TTS backpressure."""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

import numpy as np

logger = logging.getLogger("core.pipeline.concurrency")

TTS_QUEUE_MAX = 10  # max audio chunks queued for avatar; older ones dropped


class PipelineController:
    """Manages concurrent pipeline operations with cancellation support.

    - Barge-in: user speaks while assistant is speaking → cancel LLM task,
      stop local audio, flush TTS queue, send tts_stop to avatar.
    - Mode transitions: protected by asyncio.Lock.
    - TTS backpressure: queue maxsize=TTS_QUEUE_MAX, oldest dropped on overflow.
    """

    def __init__(self) -> None:
        self._current_response: asyncio.Task | None = None
        self._mode_lock = asyncio.Lock()
        self._tts_queue: asyncio.Queue[np.ndarray] = asyncio.Queue(maxsize=TTS_QUEUE_MAX)
        self._local_player = None  # set when TTS is loaded (B4)
        self._ws_manager = None   # set when WS manager available (B1)

    def set_local_player(self, player) -> None:
        self._local_player = player

    def set_ws_manager(self, ws_manager) -> None:
        self._ws_manager = ws_manager

    async def handle_barge_in(self) -> None:
        """Called when VAD detects user started speaking during response."""
        logger.debug("Barge-in detected.")

        # 1. Cancel current LLM+TTS task
        if self._current_response and not self._current_response.done():
            self._current_response.cancel()
            try:
                await self._current_response
            except asyncio.CancelledError:
                pass

        # 2. Stop local audio playback
        if self._local_player:
            self._local_player.stop()

        # 3. Flush TTS queue
        while not self._tts_queue.empty():
            try:
                self._tts_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        # 4. Notify avatar
        if self._ws_manager and self._ws_manager.has_avatar_client():
            await self._ws_manager.broadcast({"type": "tts_stop"}, avatar_only=True)

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

    async def enqueue_tts_chunk(self, chunk: np.ndarray) -> None:
        """Add TTS audio chunk to queue with backpressure (drops oldest on overflow)."""
        try:
            self._tts_queue.put_nowait(chunk)
        except asyncio.QueueFull:
            try:
                self._tts_queue.get_nowait()  # drop oldest
            except asyncio.QueueEmpty:
                pass
            self._tts_queue.put_nowait(chunk)

    async def change_mode(self, new_mode: str, workspace: str | None = None) -> None:
        """Atomic mode transition."""
        async with self._mode_lock:
            from core.state_machine import state_machine
            await state_machine.transition(new_mode, workspace)
            logger.info(f"Mode transitioned to: {new_mode}")

    @property
    def tts_queue(self) -> asyncio.Queue:
        return self._tts_queue


# Global singleton
pipeline_controller = PipelineController()

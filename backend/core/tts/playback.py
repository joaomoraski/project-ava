"""Local audio playback via sounddevice (system speakers).

Primary audio output for TTS.
Barge-in calls clear_queue() + stop() to cut playback immediately.

Queue-based architecture:
  A background asyncio task drains the queue and runs sd.play()+sd.wait()
  in an executor, so the event loop is never blocked.
  While chunk N plays, chunk N+1 can be synthesized and enqueued in parallel.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

import numpy as np

logger = logging.getLogger("core.tts.playback")

DEFAULT_SAMPLE_RATE = 24000


class LocalAudioPlayer:
    """Queue-based audio player for TTS.

    Usage:
        await player.start()          # launch background loop (VoicePipeline.start)
        await player.enqueue(audio)   # non-blocking, returns immediately
        await player.drain()          # wait for all queued audio to finish
        player.clear_queue()          # flush queue + stop playback (barge-in)
        await player.shutdown()       # stop background loop (VoicePipeline.stop)
        await player.play(audio)      # backward-compat: enqueue + drain
    """

    def __init__(self, sample_rate: int = DEFAULT_SAMPLE_RATE) -> None:
        self.sample_rate = sample_rate
        self._queue: asyncio.Queue[Optional[np.ndarray]] = asyncio.Queue()
        self._playing = False
        self._loop_task: asyncio.Task | None = None
        self._queue_lock: asyncio.Lock = asyncio.Lock()

    async def start(self) -> None:
        """Launch the background playback loop."""
        if self._loop_task and not self._loop_task.done():
            return
        self._loop_task = asyncio.create_task(self._playback_loop())
        logger.debug("Audio playback loop started.")

    async def shutdown(self) -> None:
        """Stop the background playback loop cleanly."""
        if self._loop_task and not self._loop_task.done():
            await self._queue.put(None)  # sentinel to exit loop
            try:
                await asyncio.wait_for(self._loop_task, timeout=5.0)
            except asyncio.TimeoutError:
                self._loop_task.cancel()
        logger.debug("Audio playback loop stopped.")

    async def _playback_loop(self) -> None:
        """Background task: drain queue and play each chunk sequentially."""
        loop = asyncio.get_running_loop()
        while True:
            chunk = await self._queue.get()
            if chunk is None:
                # Shutdown sentinel
                self._queue.task_done()
                break
            self._playing = True
            try:
                await loop.run_in_executor(None, self._play_sync, chunk)
            except Exception as e:
                logger.error(f"Playback error in loop: {e}")
            finally:
                self._playing = False
                self._queue.task_done()

    def _play_sync(self, audio_data: np.ndarray) -> None:
        """Blocking playback — runs in an executor thread."""
        try:
            import sounddevice as sd
            sd.play(audio_data.astype(np.float32), samplerate=self.sample_rate)
            sd.wait()
        except OSError as e:
            logger.error(f"PortAudio error: {e}. Install libportaudio2.")
        except Exception as e:
            logger.error(f"Audio playback error: {e}")

    async def enqueue(self, audio_data: np.ndarray) -> None:
        """Add audio to the playback queue. Returns immediately (non-blocking).

        Auto-starts the background loop if not already running.
        """
        if self._loop_task is None or self._loop_task.done():
            await self.start()
        async with self._queue_lock:
            await self._queue.put(audio_data)

    async def drain(self) -> None:
        """Wait until the queue is empty and all playback has finished."""
        await self._queue.join()

    def clear_queue(self) -> None:
        """Flush the queue and stop current playback immediately (for barge-in).

        Intentionally synchronous — called from barge-in which may not be in an
        async context. The lock is NOT acquired here to avoid deadlock; callers
        must ensure enqueue is not racing (barge-in cancels the response task first).
        """
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except (asyncio.QueueEmpty, ValueError):
                break
        self.stop()

    def stop(self) -> None:
        """Stop current sounddevice playback (for barge-in)."""
        try:
            import sounddevice as sd
            sd.stop()
        except OSError as e:
            logger.debug(f"PortAudio stop error: {e}")
        except Exception as e:
            logger.debug(f"stop() error: {e}")
        finally:
            self._playing = False

    async def play(self, audio_data: np.ndarray) -> None:
        """Backward-compatible: enqueue + drain (blocks until audio finishes)."""
        await self.enqueue(audio_data)
        await self.drain()

    @property
    def playing(self) -> bool:
        return self._playing


# Global singleton
local_player = LocalAudioPlayer()

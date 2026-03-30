"""Local audio playback via sounddevice (system speakers).

Primary audio output for TTS — no avatar required.
Barge-in calls stop() to cut playback immediately.
"""
from __future__ import annotations

import asyncio
import logging

import numpy as np

logger = logging.getLogger("core.tts.playback")

DEFAULT_SAMPLE_RATE = 24000


class LocalAudioPlayer:
    """Plays TTS audio chunks via system speakers using sounddevice.

    Designed to be called from async code.
    Multiple sequential chunks are played without gaps.
    stop() immediately cuts playback (barge-in).
    """

    def __init__(self, sample_rate: int = DEFAULT_SAMPLE_RATE) -> None:
        self.sample_rate = sample_rate
        self._playing = False

    def play_sync(self, audio_data: np.ndarray) -> None:
        """Play audio synchronously. Blocks until playback completes."""
        try:
            import sounddevice as sd
            self._playing = True
            sd.play(audio_data.astype(np.float32), samplerate=self.sample_rate)
            sd.wait()
        except OSError as e:
            logger.error(f"PortAudio error during playback: {e}. Install libportaudio2.")
        except Exception as e:
            logger.error(f"Audio playback error: {e}")
        finally:
            self._playing = False

    async def play(self, audio_data: np.ndarray) -> None:
        """Play audio chunk. Non-blocking — runs in executor."""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self.play_sync, audio_data)

    def stop(self) -> None:
        """Stop playback immediately (for barge-in)."""
        try:
            import sounddevice as sd
            sd.stop()
        except OSError as e:
            logger.debug(f"PortAudio stop error (may not be playing): {e}")
        except Exception as e:
            logger.debug(f"stop() error: {e}")
        finally:
            self._playing = False

    @property
    def playing(self) -> bool:
        return self._playing


# Global singleton
local_player = LocalAudioPlayer()

"""XTTS v2 TTS — voice cloning alternative.

Uses Coqui XTTS v2 (community fork: idiap/coqui-ai-TTS).
Requires a voice sample WAV file for cloning (6s+ recommended).
Install: pip install coqui-tts
"""
from __future__ import annotations

import logging

import numpy as np

from core.config import settings

logger = logging.getLogger("core.tts.xtts")

XTTS_SAMPLE_RATE = 24000


class XTTSS:
    """XTTS v2 TTS wrapper with voice cloning support.

    Slower than Kokoro but allows cloning any voice from a short audio sample.
    """

    def __init__(
        self,
        voice_sample: str | None = None,
        language: str | None = None,
    ) -> None:
        self._voice_sample = voice_sample or settings.xtts_voice_sample
        self._language = language or settings.tts_language
        self._tts = None

    def load(self) -> None:
        """Load XTTS model into memory."""
        try:
            from TTS.api import TTS
        except ImportError:
            logger.error("coqui-tts not installed. Run: pip install coqui-tts")
            raise

        logger.info("Loading XTTS v2 (this may take a moment)...")
        self._tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2")
        logger.info("XTTS v2 loaded.")

    def synthesize(self, text: str) -> np.ndarray | None:
        """Synthesize text using voice cloning. Returns numpy float32 array."""
        if self._tts is None:
            raise RuntimeError("XTTS not loaded. Call load() first.")

        if not text.strip():
            return None

        import os
        if not os.path.exists(self._voice_sample):
            logger.error(f"Voice sample not found: {self._voice_sample}")
            return None

        try:
            wav = self._tts.tts(
                text=text,
                speaker_wav=self._voice_sample,
                language=self._language[:2] if len(self._language) > 2 else self._language,
            )
            return np.array(wav, dtype=np.float32)
        except Exception as e:
            logger.error(f"XTTS synthesis failed: {e}")
            return None

    async def asynthesize(self, text: str) -> np.ndarray | None:
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.synthesize, text)

    @property
    def sample_rate(self) -> int:
        return XTTS_SAMPLE_RATE

    @property
    def loaded(self) -> bool:
        return self._tts is not None

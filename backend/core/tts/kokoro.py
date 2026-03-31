"""Kokoro TTS — streaming synthesis per sentence.

Uses kokoro-onnx (ONNX runtime, numpy 2.x compatible, Python 3.13+).
Apache 2.0 licensed, runs fully offline.
"""
from __future__ import annotations

import logging
from typing import AsyncIterator

import numpy as np

from core.config import settings

logger = logging.getLogger("core.tts.kokoro")

KOKORO_SAMPLE_RATE = 24000


class KokoroTTS:
    """Kokoro TTS wrapper with per-sentence streaming.

    Uses kokoro-onnx backend — compatible with Python 3.13 and numpy 2.x.
    Supports 54 voices across 8 languages.
    """

    def __init__(
        self,
        voice: str | None = None,
        language: str | None = None,
    ) -> None:
        self._voice = voice or settings.tts_voice
        self._language = language or settings.tts_language
        self._model = None

    def load(self) -> None:
        """Load Kokoro ONNX model into memory."""
        try:
            from kokoro_onnx import Kokoro
        except ImportError:
            logger.error("kokoro-onnx not installed. Run: pip install kokoro-onnx")
            raise

        logger.info(f"Loading Kokoro TTS (voice={self._voice}, lang={self._language})...")
        self._model = Kokoro.from_pretrained()
        logger.info("Kokoro TTS loaded.")

    def synthesize(self, text: str) -> np.ndarray | None:
        """Synthesize text to audio. Returns numpy float32 array or None on failure."""
        if self._model is None:
            raise RuntimeError("Kokoro not loaded. Call load() first.")

        if not text.strip():
            return None

        try:
            audio, sample_rate = self._model.create(
                text,
                voice=self._voice,
                speed=1.0,
                lang=self._language,
            )
            return audio.astype(np.float32)
        except Exception as e:
            logger.error(f"Kokoro synthesis failed for text '{text[:50]}': {e}")
            return None

    async def asynthesize(self, text: str) -> np.ndarray | None:
        """Async synthesis — runs in executor to avoid blocking the event loop."""
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.synthesize, text)

    @property
    def sample_rate(self) -> int:
        return KOKORO_SAMPLE_RATE

    @property
    def loaded(self) -> bool:
        return self._model is not None

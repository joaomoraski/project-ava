"""Kokoro TTS — streaming synthesis per sentence.

Kokoro is a lightweight (82M param) TTS model (Apache 2.0).
Streams audio as numpy arrays, one sentence chunk at a time.
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

    Models are loaded lazily on first call to load().
    Supports 54 voices across 8 languages.
    """

    def __init__(
        self,
        voice: str | None = None,
        language: str | None = None,
    ) -> None:
        self._voice = voice or settings.tts_voice
        self._language = language or settings.tts_language
        self._pipeline = None

    def load(self) -> None:
        """Load Kokoro pipeline into memory."""
        try:
            from kokoro import KPipeline
        except ImportError:
            logger.error("kokoro not installed. Run: pip install kokoro")
            raise

        lang_code = self._resolve_lang_code(self._language)
        logger.info(f"Loading Kokoro TTS (voice={self._voice}, lang={lang_code})...")

        self._pipeline = KPipeline(lang_code=lang_code)
        logger.info("Kokoro TTS loaded.")

    def _resolve_lang_code(self, language: str) -> str:
        """Map language name/code to Kokoro lang_code."""
        mapping = {
            "pt": "p",
            "pt-br": "p",
            "en": "a",
            "en-us": "a",
            "en-gb": "b",
            "fr": "f",
            "ja": "j",
            "ko": "k",
            "zh": "z",
            "es": "e",
            "hi": "h",
        }
        return mapping.get(language.lower(), "a")  # default to American English

    def synthesize(self, text: str) -> np.ndarray | None:
        """Synthesize text to audio. Returns numpy float32 array or None on failure."""
        if self._pipeline is None:
            raise RuntimeError("Kokoro not loaded. Call load() first.")

        if not text.strip():
            return None

        try:
            audio_parts = []
            for _, _, audio in self._pipeline(text, voice=self._voice):
                if audio is not None and len(audio) > 0:
                    audio_parts.append(audio)

            if not audio_parts:
                return None

            return np.concatenate(audio_parts).astype(np.float32)
        except Exception as e:
            logger.error(f"Kokoro synthesis failed for text '{text[:50]}...': {e}")
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
        return self._pipeline is not None

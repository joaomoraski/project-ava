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
        import os
        # Auto-accept CPML license (non-commercial) to avoid interactive prompt
        os.environ["COQUI_TOS_AGREED"] = "1"

        try:
            from TTS.api import TTS
        except ImportError:
            logger.error("coqui-tts not installed. Run: pip install coqui-tts")
            raise

        logger.info("Loading XTTS v2 (this may take a moment — first run downloads ~1.8GB)...")
        self._tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2")
        logger.info("XTTS v2 loaded.")

    def synthesize(self, text: str, language: str | None = None) -> np.ndarray | None:
        """Synthesize text using voice cloning. Returns numpy float32 array.

        Args:
            text: Text to synthesize.
            language: Optional language override (e.g. "pt", "en"). If None,
                      falls back to instance config / auto-detect heuristic.
        """
        if self._tts is None:
            raise RuntimeError("XTTS not loaded. Call load() first.")

        if not text.strip():
            return None

        import os
        if not os.path.exists(self._voice_sample):
            logger.error(f"Voice sample not found: {self._voice_sample}")
            return None

        try:
            lang = language if language is not None else self._language
            if lang == "auto":
                lang = self._detect_language(text)
            lang_code = lang[:2] if len(lang) > 2 else lang

            wav = self._tts.tts(
                text=text,
                speaker_wav=self._voice_sample,
                language=lang_code,
            )
            return np.array(wav, dtype=np.float32)
        except Exception as e:
            logger.error(f"XTTS synthesis failed: {e}")
            return None

    @staticmethod
    def _detect_language(text: str) -> str:
        """Simple heuristic to detect pt vs en from text content."""
        pt_markers = {"que", "não", "com", "para", "uma", "como", "isso", "está", "são", "você", "eu", "ele", "ela", "nós"}
        words = set(text.lower().split())
        pt_score = len(words & pt_markers)
        return "pt" if pt_score >= 2 else "en"

    async def asynthesize(self, text: str, language: str | None = None) -> np.ndarray | None:
        import asyncio
        import functools
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, functools.partial(self.synthesize, text, language))

    @property
    def sample_rate(self) -> int:
        return XTTS_SAMPLE_RATE

    @property
    def loaded(self) -> bool:
        return self._tts is not None

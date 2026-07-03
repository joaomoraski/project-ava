"""Kokoro TTS — fast ONNX-based synthesis.

Uses kokoro-onnx v0.5+ (ONNX runtime, numpy 2.x compatible, Python 3.13+).
Apache 2.0 licensed, runs fully offline.

Model files required in backend/voice_models/:
  - kokoro-v1.0.onnx (~310MB)
  - voices-v1.0.bin (~27MB)
"""
from __future__ import annotations

import logging
import os

import numpy as np

from core.config import settings

logger = logging.getLogger("core.tts.kokoro")

KOKORO_SAMPLE_RATE = 24000

# Model file paths (relative to backend/)
MODEL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "voice_models")
MODEL_PATH = os.path.join(MODEL_DIR, "kokoro-v1.0.onnx")
VOICES_PATH = os.path.join(MODEL_DIR, "voices-v1.0.bin")

# Language code mapping for Kokoro
LANG_MAP = {
    "pt": "pt-br",
    "pt-br": "pt-br",
    "en": "en-us",
    "en-us": "en-us",
    "en-gb": "en-gb",
    "es": "es",
    "fr": "fr-fr",
    "ja": "ja",
    "ko": "ko",
    "zh": "cmn",
    "auto": "en-us",  # fallback
}


class KokoroTTS:
    """Kokoro TTS wrapper — fast ONNX synthesis with 54 voices.

    v0.5 API: Kokoro(model_path, voices_path) → model.create(text, voice, lang=...)
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

        if not os.path.exists(MODEL_PATH):
            raise FileNotFoundError(
                f"Kokoro model not found at {MODEL_PATH}. "
                "Download from: https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.0"
            )
        if not os.path.exists(VOICES_PATH):
            raise FileNotFoundError(
                f"Kokoro voices not found at {VOICES_PATH}. "
                "Download from: https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.0"
            )

        logger.info(f"Loading Kokoro TTS (voice={self._voice}, lang={self._language})...")
        self._model = Kokoro(MODEL_PATH, VOICES_PATH)
        logger.info(f"Kokoro TTS loaded. Available voices: {len(self._model.get_voices())}")

    def _resolve_lang(self, text: str | None = None) -> str:
        """Resolve language code to Kokoro format."""
        lang = self._language
        if lang == "auto" and text:
            lang = self._detect_language(text)
        return LANG_MAP.get(lang, LANG_MAP.get(lang[:2] if len(lang) > 2 else lang, "en-us"))

    @staticmethod
    def _detect_language(text: str) -> str:
        """Simple heuristic to detect pt vs en."""
        pt_markers = {"que", "não", "com", "para", "uma", "como", "isso", "está", "são", "você", "eu", "ele", "ela", "nós"}
        words = set(text.lower().split())
        return "pt" if len(words & pt_markers) >= 2 else "en"

    def synthesize(self, text: str, language: str | None = None) -> np.ndarray | None:
        """Synthesize text to audio. Returns numpy float32 array or None on failure.

        Args:
            text: Text to synthesize.
            language: Optional language override (e.g. "pt", "en"). If None,
                      falls back to instance config / auto-detect heuristic.
        """
        if self._model is None:
            raise RuntimeError("Kokoro not loaded. Call load() first.")

        if not text.strip():
            return None

        try:
            if language is not None:
                lang_key = language[:2] if len(language) > 2 else language
                lang_code = LANG_MAP.get(language, LANG_MAP.get(lang_key, "en-us"))
            else:
                lang_code = self._resolve_lang(text)
            audio, sample_rate = self._model.create(
                text,
                voice=self._voice,
                speed=1.0,
                lang=lang_code,
            )
            return audio.astype(np.float32)
        except Exception as e:
            logger.error(f"Kokoro synthesis failed for '{text[:50]}...': {e}")
            return None

    async def asynthesize(self, text: str, language: str | None = None) -> np.ndarray | None:
        """Async synthesis — runs in executor to avoid blocking the event loop."""
        import asyncio
        import functools
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, functools.partial(self.synthesize, text, language))

    @property
    def sample_rate(self) -> int:
        return KOKORO_SAMPLE_RATE

    @property
    def loaded(self) -> bool:
        return self._model is not None

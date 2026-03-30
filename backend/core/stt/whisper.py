"""faster-whisper STT wrapper.

Transcribes audio to text with language detection.
Model is cached in ~/.cache/huggingface/hub/ after first download.
"""
from __future__ import annotations

import logging
from typing import NamedTuple

import numpy as np

from core.config import settings

logger = logging.getLogger("core.stt.whisper")


class TranscriptionResult(NamedTuple):
    text: str
    language: str
    confidence: float  # average segment no_speech_prob inverted


class WhisperSTT:
    """faster-whisper local STT.

    Models: tiny (~40MB), base (~74MB), small (~244MB),
            medium (~769MB), large-v3-turbo (~800MB, recommended)
    """

    def __init__(
        self,
        model_size: str | None = None,
        device: str | None = None,
        compute_type: str = "auto",
    ) -> None:
        self._model_size = model_size or settings.whisper_model
        self._device = device or settings.whisper_device
        self._compute_type = compute_type
        self._model = None

    def load(self) -> None:
        """Load the Whisper model into memory."""
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            logger.error("faster-whisper not installed. Run: pip install faster-whisper")
            raise

        # Fallback: if CUDA not available, use CPU
        device = self._device
        try:
            import torch
            if device == "cuda" and not torch.cuda.is_available():
                logger.warning("CUDA not available, falling back to CPU for Whisper.")
                device = "cpu"
        except ImportError:
            device = "cpu"

        compute_type = "int8" if device == "cpu" else (
            "float16" if self._compute_type == "auto" else self._compute_type
        )

        logger.info(f"Loading Whisper {self._model_size} on {device} ({compute_type})...")
        self._model = WhisperModel(
            self._model_size,
            device=device,
            compute_type=compute_type,
        )
        logger.info("Whisper STT loaded.")

    def transcribe(
        self,
        audio: np.ndarray,
        language: str | None = None,
        initial_prompt: str | None = None,
    ) -> TranscriptionResult:
        """Transcribe a numpy float32 audio array at 16kHz.

        Args:
            audio: float32 array, mono, 16kHz
            language: ISO language code hint (e.g. "pt", "en"). None = auto-detect.
            initial_prompt: optional context to improve transcription.

        Returns:
            TranscriptionResult with text, language, and confidence.
        """
        if self._model is None:
            raise RuntimeError("Whisper model not loaded. Call load() first.")

        segments, info = self._model.transcribe(
            audio,
            language=language,
            initial_prompt=initial_prompt,
            vad_filter=False,  # VAD already done upstream
            word_timestamps=False,
            beam_size=5,
        )

        text_parts: list[str] = []
        no_speech_probs: list[float] = []

        for segment in segments:
            text_parts.append(segment.text.strip())
            if hasattr(segment, "no_speech_prob"):
                no_speech_probs.append(segment.no_speech_prob)

        text = " ".join(text_parts).strip()
        avg_no_speech = sum(no_speech_probs) / len(no_speech_probs) if no_speech_probs else 0.5
        confidence = 1.0 - avg_no_speech

        return TranscriptionResult(
            text=text,
            language=info.language,
            confidence=confidence,
        )

    @property
    def loaded(self) -> bool:
        return self._model is not None

"""Silero VAD wrapper — Voice Activity Detection.

Detects when user starts/stops speaking without running STT continuously.
Model is downloaded from torch.hub on first use (~5MB).
"""
from __future__ import annotations

import logging
import time
from typing import Callable

import numpy as np

logger = logging.getLogger("core.audio.vad")

# VAD parameters
SAMPLE_RATE = 16000
CHUNK_SIZE = 512          # samples per chunk (32ms at 16kHz)
SILENCE_THRESHOLD = 0.5   # seconds of silence before declaring speech ended
SPEECH_THRESHOLD = 0.5    # Silero confidence threshold to consider as speech


class SileroVAD:
    """Wraps Silero VAD model for voice activity detection.

    Usage:
        vad = SileroVAD()
        await vad.load()
        vad.on_speech_start = my_callback
        vad.on_speech_end = my_callback
        vad.process_chunk(audio_chunk)
    """

    def __init__(self) -> None:
        self._model = None
        self._utils = None
        self._is_speaking = False
        self._silence_start: float | None = None
        self._speech_buffer: list[np.ndarray] = []

        # Callbacks
        self.on_speech_start: Callable | None = None
        self.on_speech_end: Callable[[np.ndarray], None] | None = None  # passes audio

    def load(self) -> None:
        """Load Silero VAD model from torch.hub."""
        try:
            import torch
            model, utils = torch.hub.load(
                repo_or_dir="snakers4/silero-vad",
                model="silero_vad",
                force_reload=False,
                onnx=False,
                verbose=False,
            )
            self._model = model
            self._utils = utils
            logger.info("Silero VAD loaded.")
        except Exception as e:
            logger.error(f"Failed to load Silero VAD: {e}")
            raise

    def _get_speech_prob(self, chunk: np.ndarray) -> float:
        """Get speech probability for a 512-sample chunk."""
        if self._model is None:
            return 0.0
        try:
            import torch
            audio_tensor = torch.from_numpy(chunk).float()
            if audio_tensor.dim() == 1:
                audio_tensor = audio_tensor.unsqueeze(0)
            prob = self._model(audio_tensor, SAMPLE_RATE).item()
            return float(prob)
        except Exception as e:
            logger.debug(f"VAD inference error: {e}")
            return 0.0

    def process_chunk(self, chunk: np.ndarray) -> None:
        """Process a 512-sample audio chunk and fire callbacks as needed."""
        prob = self._get_speech_prob(chunk)
        is_speech = prob >= SPEECH_THRESHOLD

        if is_speech:
            self._speech_buffer.append(chunk)
            self._silence_start = None

            if not self._is_speaking:
                self._is_speaking = True
                logger.debug("Speech started.")
                if self.on_speech_start:
                    self.on_speech_start()
        else:
            if self._is_speaking:
                self._speech_buffer.append(chunk)  # include trailing silence
                if self._silence_start is None:
                    self._silence_start = time.monotonic()
                elif time.monotonic() - self._silence_start >= SILENCE_THRESHOLD:
                    self._is_speaking = False
                    audio = np.concatenate(self._speech_buffer)
                    self._speech_buffer = []
                    self._silence_start = None
                    logger.debug(f"Speech ended ({len(audio)/SAMPLE_RATE:.2f}s of audio).")
                    if self.on_speech_end:
                        self.on_speech_end(audio)

    def reset(self) -> None:
        """Reset internal state (e.g. on mode change)."""
        self._is_speaking = False
        self._silence_start = None
        self._speech_buffer = []
        if self._model is not None:
            try:
                self._model.reset_states()
            except Exception:
                pass

    @property
    def is_speaking(self) -> bool:
        return self._is_speaking

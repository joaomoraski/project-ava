"""Configurable STT Gate.

Controls when transcription is triggered:
  - always_on: transcribe everything VAD detects
  - on_demand: only transcribe when explicitly called
  - smart: heuristic-based — high confidence speech, sufficient duration
"""
from __future__ import annotations

import logging
from enum import Enum
from typing import NamedTuple

import numpy as np

logger = logging.getLogger("core.stt.gate")

# Smart mode thresholds
MIN_SPEECH_DURATION_SEC = 0.5   # ignore very short sounds
MIN_AUDIO_ENERGY = 0.005        # ignore very quiet audio (background noise)


class GateMode(str, Enum):
    always_on = "always_on"
    on_demand = "on_demand"
    smart = "smart"


class GateDecision(NamedTuple):
    should_transcribe: bool
    reason: str


class STTGate:
    """Decides whether to pass audio to the STT model.

    Workspace-configurable gate between VAD and Whisper.
    """

    def __init__(self, mode: str = "smart", sample_rate: int = 16000) -> None:
        self._mode = GateMode(mode)
        self._sample_rate = sample_rate
        self._force_next: bool = False  # on_demand trigger flag

    @property
    def mode(self) -> GateMode:
        return self._mode

    @mode.setter
    def mode(self, value: str) -> None:
        self._mode = GateMode(value)
        logger.debug(f"STT Gate mode set to: {value}")

    def trigger(self) -> None:
        """For on_demand mode: signal that the next speech segment should be transcribed."""
        self._force_next = True
        logger.debug("STT Gate triggered (on_demand).")

    def evaluate(self, audio: np.ndarray) -> GateDecision:
        """Evaluate whether to transcribe this audio segment."""
        duration_sec = len(audio) / self._sample_rate

        if self._mode == GateMode.always_on:
            return GateDecision(True, "always_on")

        if self._mode == GateMode.on_demand:
            if self._force_next:
                self._force_next = False
                return GateDecision(True, "on_demand triggered")
            return GateDecision(False, "on_demand: not triggered")

        # smart mode
        if duration_sec < MIN_SPEECH_DURATION_SEC:
            return GateDecision(False, f"too short: {duration_sec:.2f}s < {MIN_SPEECH_DURATION_SEC}s")

        energy = float(np.sqrt(np.mean(audio ** 2)))
        if energy < MIN_AUDIO_ENERGY:
            return GateDecision(False, f"too quiet: energy={energy:.4f}")

        # Force-trigger overrides smart too
        if self._force_next:
            self._force_next = False
            return GateDecision(True, "smart: force triggered")

        return GateDecision(True, f"smart: duration={duration_sec:.2f}s energy={energy:.4f}")

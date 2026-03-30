"""Microphone and loopback audio capture.

Cross-platform support:
  - Windows: sounddevice with WASAPI loopback
  - macOS: sounddevice + BlackHole virtual driver
  - Linux: sounddevice with PulseAudio/PipeWire monitor source
"""
from __future__ import annotations

import asyncio
import logging
import queue
import sys
import threading
from typing import Callable

import numpy as np

logger = logging.getLogger("core.audio.capture")

SAMPLE_RATE = 16000
CHANNELS = 1
CHUNK_SIZE = 512  # 32ms at 16kHz — matches Silero VAD requirements


class MicrophoneCapture:
    """Captures microphone input and feeds chunks to a callback.

    Requires PortAudio (sounddevice). Install libportaudio2 on Linux.
    """

    def __init__(
        self,
        chunk_callback: Callable[[np.ndarray], None] | None = None,
        device: int | str | None = None,
    ) -> None:
        self._callback = chunk_callback
        self._device = device
        self._stream = None
        self._running = False

    def start(self) -> None:
        try:
            import sounddevice as sd
        except OSError as e:
            logger.error(f"PortAudio not available: {e}. Install libportaudio2.")
            raise

        def _audio_callback(indata, frames, time_info, status):
            if status:
                logger.debug(f"sounddevice status: {status}")
            if self._callback:
                chunk = indata[:, 0].copy()  # mono
                self._callback(chunk)

        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="float32",
            blocksize=CHUNK_SIZE,
            device=self._device,
            callback=_audio_callback,
        )
        self._stream.start()
        self._running = True
        logger.info(f"Microphone capture started (device={self._device or 'default'}).")

    def stop(self) -> None:
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        self._running = False
        logger.info("Microphone capture stopped.")

    @property
    def running(self) -> bool:
        return self._running


class LoopbackCapture:
    """Captures system audio output (for meeting mode).

    Linux: Uses PulseAudio/PipeWire monitor device (device name contains 'monitor').
    macOS: Requires BlackHole virtual driver as loopback source.
    Windows: WASAPI loopback via sounddevice (device name contains 'Loopback').
    """

    def __init__(self, chunk_callback: Callable[[np.ndarray], None] | None = None) -> None:
        self._callback = chunk_callback
        self._stream = None
        self._running = False

    @staticmethod
    def find_loopback_device() -> int | None:
        """Find the system loopback/monitor device."""
        try:
            import sounddevice as sd
            devices = sd.query_devices()
            platform = sys.platform

            for i, device in enumerate(devices):
                name = device["name"].lower()
                max_input = device["max_input_channels"]
                if max_input < 1:
                    continue
                if platform == "linux" and "monitor" in name:
                    return i
                if platform == "darwin" and "blackhole" in name:
                    return i
                if platform == "win32" and "loopback" in name:
                    return i
            return None
        except Exception as e:
            logger.warning(f"Could not find loopback device: {e}")
            return None

    def start(self) -> None:
        try:
            import sounddevice as sd
        except OSError as e:
            logger.error(f"PortAudio not available: {e}. Install libportaudio2.")
            raise

        device = self.find_loopback_device()
        if device is None:
            logger.warning(
                "No loopback device found. Meeting mode audio capture will not work. "
                "Linux: ensure PulseAudio/PipeWire monitor is available. "
                "macOS: install BlackHole. Windows: use WASAPI."
            )
            return

        def _audio_callback(indata, frames, time_info, status):
            if self._callback:
                chunk = indata[:, 0].copy()
                self._callback(chunk)

        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            blocksize=CHUNK_SIZE,
            device=device,
            callback=_audio_callback,
        )
        self._stream.start()
        self._running = True
        logger.info(f"Loopback capture started (device {device}).")

    def stop(self) -> None:
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        self._running = False
        logger.info("Loopback capture stopped.")

    @property
    def running(self) -> bool:
        return self._running

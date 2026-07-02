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

    def start(self, retries: int = 3, retry_delay: float = 0.5) -> None:
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

        import time
        last_error = None
        for attempt in range(retries):
            try:
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
                return
            except Exception as e:
                last_error = e
                if attempt < retries - 1:
                    logger.warning(f"Mic open attempt {attempt + 1}/{retries} failed: {e}. Retrying in {retry_delay}s...")
                    time.sleep(retry_delay)
                    retry_delay *= 2  # exponential backoff

        logger.error(f"Failed to open microphone after {retries} attempts: {last_error}")
        raise last_error  # type: ignore[misc]

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

    Linux: Uses `parec` (PulseAudio/PipeWire) to capture monitor source directly.
           This works even when sounddevice can't see PipeWire monitor devices.
    macOS: Requires BlackHole virtual driver as loopback source (sounddevice).
    Windows: WASAPI loopback via sounddevice (device name contains 'Loopback').
    """

    def __init__(self, chunk_callback: Callable[[np.ndarray], None] | None = None) -> None:
        self._callback = chunk_callback
        self._stream = None
        self._process = None
        self._thread = None
        self._running = False

    @staticmethod
    def _find_monitor_source() -> str | None:
        """Find the PulseAudio/PipeWire monitor source name via pactl."""
        import shutil
        import subprocess

        if not shutil.which("pactl"):
            return None

        try:
            result = subprocess.run(
                ["pactl", "list", "short", "sources"],
                capture_output=True, text=True, timeout=5,
            )
            for line in result.stdout.splitlines():
                parts = line.split("\t")
                if len(parts) >= 2 and "monitor" in parts[1].lower():
                    logger.info(f"Found PulseAudio monitor source: {parts[1]}")
                    return parts[1]
        except Exception as e:
            logger.debug(f"pactl list sources failed: {e}")

        return None

    def start(self) -> None:
        import shutil

        if sys.platform == "linux":
            self._start_linux()
        elif sys.platform == "darwin":
            self._start_sounddevice("blackhole")
        elif sys.platform == "win32":
            self._start_sounddevice("loopback")
        else:
            logger.warning(f"Loopback capture not supported on {sys.platform}")

    def _start_linux(self) -> None:
        """Linux: use parec to capture from PipeWire/PulseAudio monitor source."""
        import shutil
        import subprocess

        if not shutil.which("parec"):
            logger.warning(
                "parec not found — loopback capture disabled. "
                "Install: sudo apt install pulseaudio-utils"
            )
            return

        monitor = self._find_monitor_source()
        if not monitor:
            logger.warning(
                "No monitor source found in PulseAudio/PipeWire. "
                "Loopback capture disabled. Check: pactl list short sources"
            )
            return

        # Start parec subprocess: captures monitor source as raw PCM
        cmd = [
            "parec",
            "--device", monitor,
            "--rate", str(SAMPLE_RATE),
            "--channels", "1",
            "--format", "float32le",
            "--latency-msec", "100",
        ]
        logger.info(f"Starting loopback capture via parec: {' '.join(cmd)}")

        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except Exception as e:
            logger.error(f"Failed to start parec: {e}")
            return

        self._running = True

        # Read audio in a background thread
        def _read_loop():
            bytes_per_chunk = CHUNK_SIZE * 4  # float32 = 4 bytes per sample
            while self._running and self._process and self._process.poll() is None:
                raw = self._process.stdout.read(bytes_per_chunk)
                if not raw:
                    break
                chunk = np.frombuffer(raw, dtype=np.float32)
                if self._callback and len(chunk) > 0:
                    self._callback(chunk.copy())

            if self._process and self._process.poll() is None:
                self._process.terminate()

        self._thread = threading.Thread(target=_read_loop, daemon=True)
        self._thread.start()
        logger.info(f"Loopback capture started via parec (monitor={monitor}).")

    def _start_sounddevice(self, keyword: str) -> None:
        """macOS/Windows: use sounddevice to capture from loopback device."""
        try:
            import sounddevice as sd
        except OSError as e:
            logger.error(f"PortAudio not available: {e}")
            return

        devices = sd.query_devices()
        device_id = None
        for i, d in enumerate(devices):
            if d["max_input_channels"] > 0 and keyword in d["name"].lower():
                device_id = i
                break

        if device_id is None:
            logger.warning(f"No loopback device found matching '{keyword}'.")
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
            device=device_id,
            callback=_audio_callback,
        )
        self._stream.start()
        self._running = True
        logger.info(f"Loopback capture started (sounddevice device {device_id}).")

    def stop(self) -> None:
        self._running = False
        if self._process:
            try:
                self._process.terminate()
                self._process.wait(timeout=3)
            except Exception:
                self._process.kill()
            self._process = None
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None
        logger.info("Loopback capture stopped.")

    @property
    def running(self) -> bool:
        return self._running

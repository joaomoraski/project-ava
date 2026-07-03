"""Voice transcription API for the chat UI mic button.

POST /api/voice/transcribe — accepts audio file, returns transcribed text.
"""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Request, UploadFile, HTTPException

router = APIRouter(prefix="/api/voice", tags=["voice"])
logger = logging.getLogger("api.voice")


@router.post("/transcribe")
async def transcribe_audio(request: Request, audio: UploadFile = File(...)) -> dict:
    """Transcribe uploaded audio using WhisperSTT."""
    logger.info(f"Voice transcribe request: filename={audio.filename}, content_type={audio.content_type}")

    # Use the global STT instance (loaded at startup) if available
    stt = getattr(request.app.state, "stt", None)
    logger.info(f"Global STT instance: {type(stt).__name__ if stt else 'None'} (loaded={getattr(stt, 'loaded', 'N/A')})")

    if stt is None:
        logger.warning("Global STT is None, creating new instance...")
        try:
            from core.stt.whisper import WhisperSTT
            stt = WhisperSTT()
            stt.load()
            logger.info("Created and loaded new WhisperSTT instance.")
        except Exception as e:
            logger.error(f"Failed to create WhisperSTT: {e}", exc_info=True)
            raise HTTPException(status_code=503, detail=f"WhisperSTT not available: {e}")

    suffix = Path(audio.filename or "audio.webm").suffix or ".webm"
    logger.info(f"Audio suffix: {suffix}")

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as tmp:
        content = await audio.read()
        logger.info(f"Audio content size: {len(content)} bytes")
        tmp.write(content)
        tmp.flush()

        try:
            import numpy as np
            import subprocess

            logger.info(f"Running ffmpeg on {tmp.name}...")
            result = subprocess.run(
                ["ffmpeg", "-i", tmp.name, "-f", "f32le", "-ar", "16000", "-ac", "1", "-"],
                capture_output=True,
                timeout=30,
            )
            if result.returncode != 0:
                stderr = result.stderr.decode(errors="replace")[:300]
                logger.error(f"ffmpeg failed (rc={result.returncode}): {stderr}")
                raise RuntimeError(f"ffmpeg failed: {stderr}")

            audio_data = np.frombuffer(result.stdout, dtype=np.float32)
            logger.info(f"ffmpeg output: {len(audio_data)} samples ({len(audio_data)/16000:.1f}s)")

            if len(audio_data) < 1600:
                logger.warning(f"Audio too short ({len(audio_data)} samples), returning empty.")
                return {"text": "", "language": None, "confidence": 0.0}

            logger.info("Transcribing audio...")
            transcription = stt.transcribe(audio_data)
            logger.info(f"Transcription result: text='{transcription.text[:80]}', lang={transcription.language}, conf={transcription.confidence}")

            return {
                "text": transcription.text,
                "language": transcription.language,
                "confidence": transcription.confidence,
            }
        except FileNotFoundError:
            logger.error("ffmpeg not found!")
            raise HTTPException(status_code=503, detail="ffmpeg not installed.")
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Transcription failed: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Transcription failed: {e}")

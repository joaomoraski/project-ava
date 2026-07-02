"""Meeting mode — multi-source capture, speaker diarization, transcript generation.

Flow:
  1. Local mic (sounddevice) captures your voice — identified as 'You'
  2. Loopback capture (WASAPI/BlackHole/PipeWire) captures remote participants
  3. pyannote-audio identifies and labels speakers (requires HF_TOKEN)
  4. faster-whisper transcribes each segment with timestamps
  5. LLM generates summary + action items
  6. Output: structured Markdown at ~/meetings/YYYY-MM-DD_title.md

Requires: HF_TOKEN in .env (accept pyannote terms at huggingface.co)
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
from pathlib import Path

import numpy as np

from core.config import settings

logger = logging.getLogger("core.knowledge.meeting")

MEETINGS_DIR = os.path.expanduser("~/meetings")
SAMPLE_RATE = 16000


class MeetingRecorder:
    """Records and transcribes a meeting with speaker diarization.

    Usage:
        recorder = MeetingRecorder()
        await recorder.start()   # begins capture
        ...
        transcript = await recorder.stop()  # stops and transcribes
        await recorder.save_and_index(title="Q4 Planning")
    """

    def __init__(self, stt=None) -> None:
        self._stt = stt
        self._mic_chunks: list[np.ndarray] = []
        self._loopback_chunks: list[np.ndarray] = []
        self._mic_capture = None
        self._loopback_capture = None
        self._running = False
        self._start_time: datetime | None = None

    async def start(self) -> None:
        """Start recording from mic and loopback."""
        try:
            from core.audio.capture import MicrophoneCapture, LoopbackCapture
        except Exception as e:
            logger.error(f"Audio capture unavailable: {e}")
            raise

        self._mic_chunks = []
        self._loopback_chunks = []
        self._start_time = datetime.now()
        self._running = True

        self._mic_capture = MicrophoneCapture(
            chunk_callback=lambda chunk: self._mic_chunks.append(chunk.copy())
        )
        self._loopback_capture = LoopbackCapture(
            chunk_callback=lambda chunk: self._loopback_chunks.append(chunk.copy())
        )

        self._mic_capture.start()
        self._loopback_capture.start()
        logger.info("Meeting recording started.")

    async def stop(self) -> str:
        """Stop recording and transcribe. Returns full transcript text."""
        self._running = False

        if self._mic_capture:
            self._mic_capture.stop()
        if self._loopback_capture:
            self._loopback_capture.stop()

        logger.info("Meeting recording stopped. Transcribing...")

        # Combine and transcribe
        transcript = await self._transcribe()
        logger.info(f"Transcription complete: {len(transcript)} chars")
        return transcript

    async def _transcribe(self) -> str:
        """Transcribe mic and loopback audio, with speaker diarization if available."""
        if not self._stt:
            return "[No STT engine configured]"

        segments = []

        # Transcribe mic audio (identified as 'You')
        if self._mic_chunks:
            mic_audio = np.concatenate(self._mic_chunks)
            try:
                result = await asyncio.get_event_loop().run_in_executor(
                    None, self._stt.transcribe, mic_audio
                )
                if result.text.strip():
                    segments.append(("You", result.text.strip()))
            except Exception as e:
                logger.error(f"Mic transcription failed: {e}")

        # Transcribe loopback audio (attempt diarization)
        if self._loopback_chunks:
            loopback_audio = np.concatenate(self._loopback_chunks)
            diarized = await self._diarize(loopback_audio)
            segments.extend(diarized)

        if not segments:
            return "[No speech detected]"

        lines = [f"**{speaker}**: {text}" for speaker, text in segments]
        return "\n\n".join(lines)

    async def _diarize(self, audio: np.ndarray) -> list[tuple[str, str]]:
        """Attempt speaker diarization with pyannote-audio.

        Falls back to 'Remote' label if pyannote is unavailable or HF_TOKEN not set.
        """
        if not settings.hf_token:
            logger.info("HF_TOKEN not set — skipping diarization, labeling as 'Remote'.")
            if self._stt:
                try:
                    result = await asyncio.get_event_loop().run_in_executor(
                        None, self._stt.transcribe, audio
                    )
                    return [("Remote", result.text.strip())] if result.text.strip() else []
                except Exception:
                    pass
            return []

        try:
            from pyannote.audio import Pipeline
            import torch

            loop = asyncio.get_event_loop()
            pipeline = await loop.run_in_executor(
                None,
                lambda: Pipeline.from_pretrained(
                    "pyannote/speaker-diarization-3.1",
                    use_auth_token=settings.hf_token,
                )
            )

            # Convert numpy array to format pyannote expects
            import tempfile
            import soundfile as sf

            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                sf.write(tmp.name, audio, SAMPLE_RATE)
                tmp_path = tmp.name

            try:
                diarization = await loop.run_in_executor(None, pipeline, tmp_path)

                segments = []
                for turn, _, speaker in diarization.itertracks(yield_label=True):
                    start = int(turn.start * SAMPLE_RATE)
                    end = int(turn.end * SAMPLE_RATE)
                    segment_audio = audio[start:end]
                    if len(segment_audio) < SAMPLE_RATE * 0.5:  # skip < 0.5s
                        continue

                    if self._stt:
                        try:
                            result = await loop.run_in_executor(
                                None, self._stt.transcribe, segment_audio
                            )
                            if result.text.strip():
                                segments.append((speaker, result.text.strip()))
                        except Exception as e:
                            logger.debug(f"Segment transcription failed: {e}")

                return segments
            finally:
                os.unlink(tmp_path)

        except ImportError:
            logger.warning("pyannote-audio not installed — falling back to 'Remote' label.")
        except Exception as e:
            logger.error(f"Diarization failed: {e} — falling back to 'Remote' label.")

        # Fallback: transcribe without diarization
        if self._stt:
            try:
                result = await asyncio.get_event_loop().run_in_executor(
                    None, self._stt.transcribe, audio
                )
                return [("Remote", result.text.strip())] if result.text.strip() else []
            except Exception:
                pass

        return []

    async def save_and_index(
        self,
        transcript: str,
        title: str = "Meeting",
        workspace: str = "personal",
        generate_summary: bool = True,
    ) -> str:
        """Save transcript to Markdown and index into knowledge base.

        Returns the path to the saved Markdown file.
        """
        date_str = (self._start_time or datetime.now()).strftime("%Y-%m-%d")
        safe_title = title.replace(" ", "_").replace("/", "-")[:50]
        filename = f"{date_str}_{safe_title}.md"

        os.makedirs(MEETINGS_DIR, exist_ok=True)
        file_path = os.path.join(MEETINGS_DIR, filename)

        # Build Markdown content
        content = f"# {title}\n\n"
        content += f"**Date:** {date_str}\n"
        content += f"**Workspace:** {workspace}\n\n"
        content += "---\n\n"
        content += "## Transcript\n\n"
        content += transcript + "\n\n"

        # Generate summary if LLM available
        if generate_summary:
            summary = await self._generate_summary(transcript)
            if summary:
                content += "---\n\n"
                content += "## Summary\n\n"
                content += summary + "\n"

        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)

        logger.info(f"Meeting saved: {file_path}")

        # Index into knowledge base — chunk transcript only (not LLM meta-commentary)
        try:
            from core.knowledge.rag import KnowledgeBase
            from core.knowledge.ingestion import chunk_text
            kb = KnowledgeBase(workspace, "meetings")
            # Chunk the raw transcript, not the full content (which includes LLM summary)
            transcript_header = f"# {title}\n\nDate: {date_str}\nWorkspace: {workspace}\n\n"
            chunks = chunk_text(transcript_header + transcript)
            kb.add_chunks([
                {"content": c, "source": file_path, "source_type": "meeting", "file_name": filename}
                for c in chunks
            ])
        except Exception as e:
            logger.warning(f"Failed to index meeting into knowledge base: {e}")

        return file_path

    async def _generate_summary(self, transcript: str) -> str:
        """Generate meeting summary and action items via LLM."""
        try:
            from core.llm.provider import get_llm
            from langchain_core.messages import HumanMessage

            llm = get_llm(streaming=False)
            prompt = (
                "Summarize this meeting transcript concisely. Output ONLY the summary content — "
                "no preamble, no meta-commentary, no 'Here is...' prefix.\n\n"
                "Format:\n"
                "### Key Decisions\n- decision 1\n- decision 2\n\n"
                "### Action Items\n- [ ] action (owner)\n\n"
                "### Topics Discussed\n- topic 1\n- topic 2\n\n"
                "### Summary\nBrief paragraph.\n\n"
                f"Transcript:\n{transcript[:3000]}"
            )

            result = await llm.ainvoke([HumanMessage(content=prompt)])
            return result.content if hasattr(result, "content") else str(result)
        except Exception as e:
            logger.error(f"Summary generation failed: {e}")
            return ""

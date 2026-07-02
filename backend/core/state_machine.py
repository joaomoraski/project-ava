"""Mode state machine with full pipeline integration.

Manages transitions between:
  - companion: VAD active, TTS active
  - meeting: VAD active, TTS disabled, loopback active
  - background: VAD stopped, TTS disabled
  - autonomous: no continuous STT, autonomous loop active

All transitions are atomic (asyncio.Lock).
State is persisted to PostgreSQL app_state table on every transition.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Callable

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

logger = logging.getLogger("core.state_machine")

# How long (seconds) of silence before a speech bubble is closed and emitted as final.
# A bubble is one continuous speech burst from one speaker; until this threshold
# elapses, new words land in the SAME bubble (segment_id stays stable).
BUBBLE_CLOSE_THRESHOLD = 15.0


class Mode(str, Enum):
    companion = "companion"
    meeting = "meeting"
    background = "background"


class StateMachine:
    """Manages mode transitions with asyncio.Lock for thread safety.

    Components (VAD, TTS, pipeline) are injected via set_* methods.
    This avoids circular imports while allowing full pipeline integration.
    """

    def __init__(self, initial_mode: str | None = None) -> None:
        from core.config import settings
        mode = initial_mode or settings.default_mode
        self._mode = Mode(mode)
        self._workspace: str = "personal"
        self._lock = asyncio.Lock()
        self._changed_at: datetime | None = None
        self._listeners: list[Callable] = []

        # Injected components (set after init to avoid circular imports)
        self._vad = None
        self._mic_capture = None
        self._loopback_capture = None
        self._tts_player = None
        self._ws_manager = None

        # Voice pipeline + meeting state
        self._voice_pipeline = None
        self._active_meeting_recorder = None
        self._active_meeting_id: str | None = None
        self._stt = None
        self._agent = None
        self._pre_meeting_mode: str | None = None  # mode before entering meeting

    def set_components(
        self,
        vad=None,
        mic_capture=None,
        loopback_capture=None,
        tts_player=None,
        ws_manager=None,
    ) -> None:
        if vad is not None:
            self._vad = vad
        if mic_capture is not None:
            self._mic_capture = mic_capture
        if loopback_capture is not None:
            self._loopback_capture = loopback_capture
        if tts_player is not None:
            self._tts_player = tts_player
        if ws_manager is not None:
            self._ws_manager = ws_manager

    def set_pipeline(self, voice_pipeline) -> None:
        self._voice_pipeline = voice_pipeline

    def set_stt(self, stt) -> None:
        self._stt = stt

    def set_agent(self, agent) -> None:
        self._agent = agent

    async def load_persisted_state(self) -> None:
        """Load non-mode state from PostgreSQL. Called during startup.

        Mode is NOT restored from DB — DEFAULT_MODE in .env always controls
        the startup mode. Only workspace and pre_meeting_mode are restored.
        """
        try:
            from core.db.engine import async_session
            from core.db.models import AppState

            async with async_session() as session:
                result = await session.execute(select(AppState))
                rows = {row.key: row.value for row in result.scalars().all()}
                if "workspace" in rows:
                    self._workspace = rows["workspace"]
                if rows.get("pre_meeting_mode"):
                    self._pre_meeting_mode = rows["pre_meeting_mode"]
        except Exception as e:
            logger.debug(f"Could not load persisted state: {e}")

    async def _persist_state(self) -> None:
        """Persist current state to PostgreSQL."""
        try:
            from core.db.engine import async_session
            from core.db.models import AppState

            async with async_session() as session:
                for key, value in [
                    ("mode", self._mode.value),
                    ("workspace", self._workspace),
                    ("changed_at", self._changed_at.isoformat() if self._changed_at else ""),
                    ("pre_meeting_mode", self._pre_meeting_mode or ""),
                ]:
                    stmt = pg_insert(AppState).values(key=key, value=value)
                    stmt = stmt.on_conflict_do_update(
                        index_elements=["key"],
                        set_={"value": value},
                    )
                    await session.execute(stmt)
                await session.commit()
        except Exception as e:
            logger.warning(f"Failed to persist state: {e}")

    @property
    def mode(self) -> Mode:
        return self._mode

    @property
    def workspace(self) -> str:
        return self._workspace

    async def transition(self, new_mode: str, workspace: str | None = None) -> None:
        """Atomic mode transition with pipeline effects."""
        async with self._lock:
            old_mode = self._mode
            # Remember mode before meeting so we can restore it
            if new_mode == "meeting":
                self._pre_meeting_mode = old_mode.value
            self._mode = Mode(new_mode)
            if workspace:
                self._workspace = workspace
            self._changed_at = datetime.now(timezone.utc)
            await self._persist_state()
            logger.info(f"Mode: {old_mode.value} → {self._mode.value}")

            await self._apply_mode_effects(old_mode, self._mode)

            for listener in self._listeners:
                try:
                    if asyncio.iscoroutinefunction(listener):
                        await listener(old_mode, self._mode)
                    else:
                        listener(old_mode, self._mode)
                except Exception as e:
                    logger.warning(f"State listener error: {e}")

    async def _apply_mode_effects(self, old_mode: Mode, new_mode: Mode) -> None:
        """Apply pipeline effects for mode transition."""

        # --- Stop whatever was running in old mode ---
        if old_mode == Mode.meeting and self._active_meeting_recorder:
            try:
                transcript = await self._active_meeting_recorder.stop()
                if self._active_meeting_id:
                    await self._finalize_meeting(self._active_meeting_id, transcript)
            except Exception as e:
                logger.error(f"Failed to stop meeting recorder: {e}")
            self._active_meeting_recorder = None
            self._active_meeting_id = None

        if old_mode == Mode.companion and self._voice_pipeline:
            # Finalize companion session before stopping the pipeline
            try:
                await self._voice_pipeline.finalize_companion_session()
            except Exception as e:
                logger.warning(f"Failed to finalize companion session: {e}")
            try:
                await self._voice_pipeline.stop()
            except Exception as e:
                logger.warning(f"Failed to stop voice pipeline: {e}")

        # Brief pause to let PortAudio fully release devices before reopening
        if old_mode != new_mode:
            await asyncio.sleep(0.5)

        # --- Start what's needed for new mode ---
        if new_mode == Mode.companion:
            if self._voice_pipeline:
                try:
                    await self._voice_pipeline.start()
                    logger.info("Voice pipeline started for companion mode.")
                except Exception as e:
                    logger.error(f"Failed to start voice pipeline: {e}")
                try:
                    await self._voice_pipeline.start_companion_session(self._workspace)
                except Exception as e:
                    logger.warning(f"Failed to start companion session: {e}")
            elif self._mic_capture and not getattr(self._mic_capture, "running", False):
                try:
                    self._mic_capture.start()
                except Exception as e:
                    logger.warning(f"Mic capture start failed: {e}")

        elif new_mode == Mode.meeting:
            if self._tts_player:
                self._tts_player.stop()
            try:
                meeting_id = await self._start_meeting_recording()
                self._active_meeting_id = meeting_id
            except Exception as e:
                logger.error(f"Failed to start meeting: {e}")

        elif new_mode == Mode.background:
            if self._tts_player:
                self._tts_player.stop()
            if self._mic_capture and getattr(self._mic_capture, "running", False):
                try:
                    self._mic_capture.stop()
                except Exception as e:
                    logger.warning(f"Mic capture stop failed: {e}")
            if self._loopback_capture and getattr(self._loopback_capture, "running", False):
                try:
                    self._loopback_capture.stop()
                except Exception as e:
                    logger.warning(f"Loopback capture stop failed: {e}")

        # Always broadcast mode_change
        if self._ws_manager:
            payload: dict = {
                "type": "mode_change",
                "mode": new_mode.value,
                "workspace": self._workspace,
            }
            if new_mode == Mode.meeting and self._active_meeting_id:
                payload["meeting_id"] = self._active_meeting_id
            try:
                await self._ws_manager.broadcast(payload)
            except Exception as e:
                logger.debug(f"WS mode_change broadcast failed: {e}")

    async def _start_meeting_recording(self) -> str:
        """Create a Meeting record and start the recorder. Returns meeting_id."""
        from core.db.engine import async_session
        from core.db.models import Meeting, Workspace
        from sqlalchemy import select as sa_select

        async with async_session() as session:
            ws_result = await session.execute(
                sa_select(Workspace).where(Workspace.name == self._workspace)
            )
            workspace_obj = ws_result.scalar_one_or_none()
            workspace_id = workspace_obj.id if workspace_obj else None

            title, participants = await self._detect_calendar_meeting()

            meeting = Meeting(
                workspace_id=workspace_id,
                title=title or f"Meeting {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}",
                participants=participants or [],
                transcript="",
                status="recording",
            )
            session.add(meeting)
            await session.commit()
            await session.refresh(meeting)
            meeting_id = str(meeting.id)

        if self._stt:
            from core.knowledge.meeting import MeetingRecorder
            recorder = MeetingRecorder(stt=self._stt)
            await recorder.start()
            self._active_meeting_recorder = recorder

            asyncio.create_task(
                self._stream_transcription(meeting_id, recorder)
            )

        if self._ws_manager:
            await self._ws_manager.broadcast({
                "type": "meeting_started",
                "meeting_id": meeting_id,
                "title": title or f"Meeting {datetime.now(timezone.utc).strftime('%H:%M')}",
                "started_at": datetime.now(timezone.utc).isoformat(),
                "participants": participants or [],
            })

        logger.info(f"Meeting started: {meeting_id}")
        return meeting_id

    async def _detect_calendar_meeting(self) -> tuple[str | None, list[str]]:
        """Check Google Calendar for a meeting happening now. Returns (title, participants)."""
        try:
            from core.db.engine import async_session
            from core.db.models import CalendarEvent
            from sqlalchemy import select as sa_select, and_

            now = datetime.now(timezone.utc)

            async with async_session() as session:
                result = await session.execute(
                    sa_select(CalendarEvent).where(
                        and_(
                            CalendarEvent.start_time <= now,
                            CalendarEvent.end_time >= now,
                        )
                    ).limit(1)
                )
                event = result.scalar_one_or_none()
                if event:
                    attendees = event.attendees if isinstance(event.attendees, list) else []
                    return event.title, attendees
        except Exception as e:
            logger.debug(f"Calendar detection failed (non-fatal): {e}")
        return None, []

    # Known Whisper hallucination phrases (emitted on silence/noise)
    _HALLUCINATION_FILTER = {
        "thank you", "thanks", "you", "bye", "the end", "thanks for watching",
        "thank you for watching", "so", "yeah", "okay", "ok", "right",
        "um", "uh", "hmm", "hm", "obrigado", "obrigada", "tchau",
    }

    @staticmethod
    def _is_hallucination(result_or_text) -> bool:
        """Filter out Whisper hallucinations via confidence fields then wordlist.

        Accepts either a TranscriptionResult (preferred) or a plain str.
        """
        from core.stt.whisper import TranscriptionResult
        if isinstance(result_or_text, TranscriptionResult):
            # Tier 1: no-speech probability (silence/noise)
            if result_or_text.no_speech_prob > 0.6:
                return True
            # Tier 2: model log-probability (low confidence output)
            if result_or_text.avg_logprob < -1.0:
                return True
            text = result_or_text.text
        else:
            text = result_or_text
        # Tier 3: known hallucination wordlist
        cleaned = text.strip().rstrip(".!?,").lower()
        return cleaned in StateMachine._HALLUCINATION_FILTER or len(cleaned) < 3

    @staticmethod
    def _audio_has_speech(audio, threshold: float = 0.01) -> bool:
        """Check if audio chunk has enough energy to contain speech."""
        import numpy as np
        rms = np.sqrt(np.mean(audio ** 2))
        return rms > threshold

    async def _stream_transcription(self, meeting_id: str, recorder) -> None:
        """Stream transcription as live captions using segment-commit architecture.

        Design:
        - Every COMMIT_INTERVAL_SAMPLES of accumulated speech, the current mic/loopback
          buffer is transcribed and committed permanently (committed_text). A new buffer
          starts for the next segment. The polling loop NEVER awaits Whisper while chunks
          are being drained — segments are only committed when the buffer is full or when
          silence is detected.
        - Live display = committed_text + " " + live_of_current_segment. Text only grows.
        - On silence: treat last segment as committed immediately (no extra Whisper call).
          A background refinement task re-transcribes the full audio and updates the DB
          entry if the result differs, but the polling loop is never blocked by it.
        """
        import numpy as np

        POLL_INTERVAL = 0.5
        SILENCE_THRESHOLD = 2.0
        COMMIT_INTERVAL_SAMPLES = 16000 * 3   # commit every ~3s of accumulated speech
        MAX_SEGMENT_SAMPLES = 16000 * 30      # force commit after 30s of continuous speech
        LIVE_WINDOW_SAMPLES = 16000 * 3       # window for live preview (fast inference)

        # Per-source state: segment buffer + committed text accumulator
        mic_seg_buf: list[np.ndarray] = []
        mic_committed: str = ""
        mic_live: str = ""
        mic_silence: float = 0.0
        mic_full_buf: list[np.ndarray] = []   # all audio since last finalize (for refine)
        mic_segment_id: str | None = None
        mic_last_speech: float = 0.0
        mic_bubble_finalized: bool = False    # guards against double-emit at meeting end
        mic_silence_committed: bool = False   # SILENCE_THRESHOLD soft-commit fired once

        lb_seg_buf: list[np.ndarray] = []
        lb_committed: str = ""
        lb_live: str = ""
        lb_silence: float = 0.0
        lb_full_buf: list[np.ndarray] = []
        lb_segment_id: str | None = None
        lb_last_speech: float = 0.0
        lb_bubble_finalized: bool = False
        lb_silence_committed: bool = False

        recent_texts: list[str] = []

        import time as _time

        logger.info(f"Transcription streaming started for meeting {meeting_id}")

        async def _commit_segment(
            seg_buf: list[np.ndarray],
            committed: str,
            speaker: str,
            full_buf: list[np.ndarray],
            segment_id: str | None,
        ) -> tuple[str, str]:
            """Transcribe seg_buf, append to committed, return (new_committed, new_live)."""
            if not seg_buf:
                return committed, ""
            audio = np.concatenate(seg_buf)
            if not self._audio_has_speech(audio) or len(audio) < 8000:
                return committed, ""
            result = await self._quick_transcribe_result(recorder._stt, audio)
            if result is None or self._is_hallucination(result):
                return committed, ""
            seg_text = result.text.strip()
            if not seg_text:
                return committed, ""
            new_committed = (committed + " " + seg_text).strip()
            # Live broadcast: show full growing transcript
            if self._ws_manager:
                await self._ws_manager.broadcast({
                    "type": "transcript_live",
                    "meeting_id": meeting_id,
                    "speaker": speaker,
                    "text": new_committed,
                    "segment_id": segment_id,
                    "final": False,
                })
            return new_committed, ""

        async def _finalize_source(
            committed: str,
            live: str,
            speaker: str,
            full_buf: list[np.ndarray],
            seg_buf: list[np.ndarray],
            segment_id: str | None,
        ) -> None:
            """On silence: commit last live segment immediately; fire background refine."""
            final_text = (committed + " " + live).strip() if live else committed
            if not final_text:
                return
            await self._finalize_transcript_entry(
                meeting_id, speaker, final_text, recent_texts, segment_id=segment_id
            )
            # Background high-quality refine: does NOT block the poll loop
            if full_buf and len(np.concatenate(full_buf)) > LIVE_WINDOW_SAMPLES:
                all_audio = np.concatenate(full_buf)
                asyncio.create_task(
                    self._refine_transcript(meeting_id, speaker, all_audio, final_text, recent_texts)
                )

        while self._active_meeting_id == meeting_id and getattr(recorder, "_running", False):
            await asyncio.sleep(POLL_INTERVAL)
            now = _time.monotonic()

            try:
                # ── Mic ──────────────────────────────────────────────────────
                if recorder._mic_chunks:
                    new_chunks = list(recorder._mic_chunks)
                    recorder._mic_chunks.clear()
                    mic_seg_buf.extend(new_chunks)
                    mic_full_buf.extend(new_chunks)
                    mic_silence = 0.0
                    mic_last_speech = now
                    mic_bubble_finalized = False
                    mic_silence_committed = False
                    # Mint a segment ID when speech resumes after silence
                    if mic_segment_id is None:
                        mic_segment_id = str(uuid.uuid4())

                    # Commit segment when buffer reaches COMMIT_INTERVAL_SAMPLES
                    seg_samples = sum(len(c) for c in mic_seg_buf)
                    if seg_samples >= COMMIT_INTERVAL_SAMPLES:
                        mic_committed, mic_live = await _commit_segment(
                            mic_seg_buf, mic_committed, "You", mic_full_buf, mic_segment_id
                        )
                        mic_seg_buf.clear()
                    else:
                        # Live preview from current window — fast, doesn't block
                        all_seg = np.concatenate(mic_seg_buf)
                        live_audio = (
                            all_seg[-LIVE_WINDOW_SAMPLES:]
                            if len(all_seg) > LIVE_WINDOW_SAMPLES else all_seg
                        )
                        if self._audio_has_speech(live_audio) and len(live_audio) >= 8000:
                            result = await self._quick_transcribe_result(recorder._stt, live_audio)
                            if result and not self._is_hallucination(result):
                                mic_live = result.text.strip()
                                display = (mic_committed + " " + mic_live).strip()
                                if self._ws_manager:
                                    await self._ws_manager.broadcast({
                                        "type": "transcript_live",
                                        "meeting_id": meeting_id,
                                        "speaker": "You",
                                        "text": display,
                                        "segment_id": mic_segment_id,
                                        "final": False,
                                    })

                    # Force commit on very long segments
                    if sum(len(c) for c in mic_seg_buf) > MAX_SEGMENT_SAMPLES:
                        mic_committed, mic_live = await _commit_segment(
                            mic_seg_buf, mic_committed, "You", mic_full_buf, mic_segment_id
                        )
                        mic_seg_buf.clear()
                else:
                    mic_silence += POLL_INTERVAL

                # SILENCE_THRESHOLD: soft-commit pending audio buffer, but DO NOT close bubble.
                # The bubble stays open with the same segment_id so new speech accumulates here.
                # Only fires once per silence stretch (mic_silence_committed flag).
                if (
                    not mic_silence_committed
                    and mic_silence >= SILENCE_THRESHOLD
                    and mic_segment_id is not None
                    and mic_seg_buf
                ):
                    mic_committed, mic_live = await _commit_segment(
                        mic_seg_buf, mic_committed, "You", mic_full_buf, mic_segment_id
                    )
                    mic_seg_buf.clear()
                    mic_silence_committed = True

                # BUBBLE_CLOSE_THRESHOLD: actually close the bubble.
                # Drain remaining buffer first, then write DB + emit final=True chunk.
                if (
                    not mic_bubble_finalized
                    and mic_segment_id is not None
                    and mic_last_speech > 0.0
                    and (now - mic_last_speech) >= BUBBLE_CLOSE_THRESHOLD
                    and (mic_committed or mic_live or mic_seg_buf)
                ):
                    if mic_seg_buf:
                        mic_committed, mic_live = await _commit_segment(
                            mic_seg_buf, mic_committed, "You", mic_full_buf, mic_segment_id
                        )
                        mic_seg_buf.clear()
                    await _finalize_source(mic_committed, mic_live, "You", mic_full_buf, mic_seg_buf, mic_segment_id)
                    mic_committed = ""
                    mic_live = ""
                    mic_full_buf.clear()
                    mic_segment_id = None
                    mic_bubble_finalized = True
                    mic_silence_committed = False

                # ── Loopback ─────────────────────────────────────────────────
                if recorder._loopback_chunks:
                    new_chunks = list(recorder._loopback_chunks)
                    recorder._loopback_chunks.clear()
                    new_audio = np.concatenate(new_chunks)
                    if self._audio_has_speech(new_audio, threshold=0.02):
                        lb_seg_buf.extend(new_chunks)
                        lb_full_buf.extend(new_chunks)
                        lb_silence = 0.0
                        lb_last_speech = now
                        lb_bubble_finalized = False
                        lb_silence_committed = False
                        if lb_segment_id is None:
                            lb_segment_id = str(uuid.uuid4())

                        seg_samples = sum(len(c) for c in lb_seg_buf)
                        if seg_samples >= COMMIT_INTERVAL_SAMPLES:
                            lb_committed, lb_live = await _commit_segment(
                                lb_seg_buf, lb_committed, "Remote", lb_full_buf, lb_segment_id
                            )
                            lb_seg_buf.clear()
                        else:
                            all_seg = np.concatenate(lb_seg_buf)
                            live_audio = (
                                all_seg[-LIVE_WINDOW_SAMPLES:]
                                if len(all_seg) > LIVE_WINDOW_SAMPLES else all_seg
                            )
                            if self._audio_has_speech(live_audio) and len(live_audio) >= 8000:
                                result = await self._quick_transcribe_result(recorder._stt, live_audio)
                                if result and not self._is_hallucination(result):
                                    lb_live = result.text.strip()
                                    display = (lb_committed + " " + lb_live).strip()
                                    if self._ws_manager:
                                        await self._ws_manager.broadcast({
                                            "type": "transcript_live",
                                            "meeting_id": meeting_id,
                                            "speaker": "Remote",
                                            "text": display,
                                            "segment_id": lb_segment_id,
                                            "final": False,
                                        })

                        if sum(len(c) for c in lb_seg_buf) > MAX_SEGMENT_SAMPLES:
                            lb_committed, lb_live = await _commit_segment(
                                lb_seg_buf, lb_committed, "Remote", lb_full_buf, lb_segment_id
                            )
                            lb_seg_buf.clear()
                    else:
                        lb_silence += POLL_INTERVAL
                else:
                    lb_silence += POLL_INTERVAL

                # SILENCE_THRESHOLD: soft-commit pending audio, keep bubble open.
                if (
                    not lb_silence_committed
                    and lb_silence >= SILENCE_THRESHOLD
                    and lb_segment_id is not None
                    and lb_seg_buf
                ):
                    lb_committed, lb_live = await _commit_segment(
                        lb_seg_buf, lb_committed, "Remote", lb_full_buf, lb_segment_id
                    )
                    lb_seg_buf.clear()
                    lb_silence_committed = True

                # BUBBLE_CLOSE_THRESHOLD: actually close the loopback bubble.
                if (
                    not lb_bubble_finalized
                    and lb_segment_id is not None
                    and lb_last_speech > 0.0
                    and (now - lb_last_speech) >= BUBBLE_CLOSE_THRESHOLD
                    and (lb_committed or lb_live or lb_seg_buf)
                ):
                    if lb_seg_buf:
                        lb_committed, lb_live = await _commit_segment(
                            lb_seg_buf, lb_committed, "Remote", lb_full_buf, lb_segment_id
                        )
                        lb_seg_buf.clear()
                    await _finalize_source(lb_committed, lb_live, "Remote", lb_full_buf, lb_seg_buf, lb_segment_id)
                    lb_committed = ""
                    lb_live = ""
                    lb_full_buf.clear()
                    lb_segment_id = None
                    lb_bubble_finalized = True
                    lb_silence_committed = False

            except Exception as e:
                logger.error(f"Transcription streaming error: {e}", exc_info=True)

        # Flush any remaining text on loop exit
        if mic_committed or mic_live:
            final = (mic_committed + " " + mic_live).strip()
            if not mic_bubble_finalized:
                await self._finalize_transcript_entry(
                    meeting_id, "You", final, recent_texts, segment_id=mic_segment_id
                )
        if lb_committed or lb_live:
            final = (lb_committed + " " + lb_live).strip()
            if not lb_bubble_finalized:
                await self._finalize_transcript_entry(
                    meeting_id, "Remote", final, recent_texts, segment_id=lb_segment_id
                )

        logger.info(f"Transcription streaming ended for meeting {meeting_id}")

    async def _refine_transcript(
        self,
        meeting_id: str,
        speaker: str,
        audio,
        original_text: str,
        recent_texts: list[str],
    ) -> None:
        """Background task: re-transcribe full segment buffer for higher quality.

        Updates the DB entry if the refined text differs meaningfully from the
        original. Never awaited by the polling loop.
        """
        try:
            stt = self._stt
            if stt is None:
                return
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, lambda: stt.transcribe(audio, language=None))
            if self._is_hallucination(result):
                return
            refined = result.text.strip()
            if not refined or refined == original_text:
                return
            # Avoid re-adding to recent_texts — this is a refinement, not a new entry
            logger.debug(f"Refined transcript for {speaker}: '{original_text}' → '{refined}'")
            # Update DB: append refined entry only if it's meaningfully different
            from core.db.engine import async_session
            from core.db.models import Meeting
            async with async_session() as session:
                meeting = await session.get(Meeting, __import__("uuid").UUID(meeting_id))
                if meeting and meeting.transcript:
                    old_entry = f"**{speaker}**: {original_text}"
                    new_entry = f"**{speaker}**: {refined}"
                    if old_entry in meeting.transcript:
                        meeting.transcript = meeting.transcript.replace(old_entry, new_entry, 1)
                        await session.commit()
        except Exception as e:
            logger.debug(f"Background transcript refinement failed (non-fatal): {e}")

    async def _quick_transcribe(self, stt, audio) -> str:
        """Transcribe audio in executor thread. Returns text or empty string."""
        result = await self._quick_transcribe_result(stt, audio)
        return result.text.strip() if result else ""

    async def _quick_transcribe_result(self, stt, audio):
        """Transcribe audio in executor thread. Returns TranscriptionResult or None."""
        try:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                None, lambda: stt.transcribe(audio, language=None)
            )
        except Exception as e:
            logger.error(f"Transcription failed: {e}")
            return None

    async def _finalize_transcript_entry(
        self,
        meeting_id: str,
        speaker: str,
        text: str,
        recent_texts: list[str],
        segment_id: str | None = None,
    ) -> None:
        """Save finalized transcript entry to DB and broadcast."""
        if not text or self._is_hallucination(text):
            return

        # Dedup
        is_dup = any(
            text == prev or
            (len(text) > 10 and len(prev) > 10 and (text in prev or prev in text))
            for prev in recent_texts
        )
        if is_dup:
            return

        recent_texts.append(text)
        if len(recent_texts) > 10:
            recent_texts.pop(0)

        timestamp = datetime.now(timezone.utc).isoformat()

        # Save to DB
        from core.db.engine import async_session
        from core.db.models import Meeting
        async with async_session() as session:
            meeting = await session.get(Meeting, uuid.UUID(meeting_id))
            if meeting:
                existing = meeting.transcript or ""
                meeting.transcript = (
                    existing + f"\n**{speaker}**: {text}" if existing else f"**{speaker}**: {text}"
                )
                await session.commit()

        # Broadcast final entry
        if self._ws_manager:
            await self._ws_manager.broadcast({
                "type": "transcript_chunk",
                "meeting_id": meeting_id,
                "speaker": speaker,
                "text": text,
                "timestamp": timestamp,
                "segment_id": segment_id,
                "final": True,
            })

    async def _transcribe_source(
        self,
        chunk_list: list,
        stt,
        meeting_id: str,
        speaker: str,
        recent_texts: list[str],
    ) -> None:
        """Transcribe accumulated audio chunks from one source (mic or loopback)."""
        import numpy as np
        from core.db.engine import async_session
        from core.db.models import Meeting

        if not chunk_list or not stt:
            return

        snapshot = list(chunk_list)
        chunk_list.clear()

        audio = np.concatenate(snapshot)

        # Skip if audio is too quiet (no speech, just noise)
        if not self._audio_has_speech(audio):
            return

        # Skip very short audio (< 0.5s) — usually just noise
        if len(audio) < 8000:  # 0.5s at 16kHz
            return

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, lambda: stt.transcribe(audio, language=None)
        )

        text = result.text.strip()

        # Filter hallucinations — pass full result for confidence-based tiers
        if not text or self._is_hallucination(result):
            if text:
                logger.debug(f"Filtered hallucination from {speaker}: '{text}'")
            return

        # Cross-source dedup
        is_dup = any(
            text == prev or
            (len(text) > 10 and len(prev) > 10 and (text in prev or prev in text))
            for prev in recent_texts
        )
        if is_dup:
            return

        recent_texts.append(text)
        if len(recent_texts) > 10:
            recent_texts.pop(0)

        timestamp = datetime.now(timezone.utc).isoformat()

        # Save to DB
        async with async_session() as session:
            meeting = await session.get(Meeting, uuid.UUID(meeting_id))
            if meeting:
                existing = meeting.transcript or ""
                meeting.transcript = (
                    existing + f"\n**{speaker}**: {text}" if existing else f"**{speaker}**: {text}"
                )
                await session.commit()

        # Broadcast to frontend
        if self._ws_manager:
            await self._ws_manager.broadcast({
                "type": "transcript_chunk",
                "meeting_id": meeting_id,
                "speaker": speaker,
                "text": text,
                "timestamp": timestamp,
                "segment_id": None,
                "final": True,
            })

    async def _finalize_meeting(self, meeting_id: str, transcript: str) -> None:
        """After meeting stops: update DB, broadcast stopped, trigger doc generation."""
        from core.db.engine import async_session
        from core.db.models import Meeting

        async with async_session() as session:
            meeting = await session.get(Meeting, uuid.UUID(meeting_id))
            if meeting:
                meeting.status = "completed"
                meeting.ended_at = datetime.now(timezone.utc)
                # DON'T overwrite the real-time transcript — it's more accurate
                # (per-chunk language detection) than bulk re-transcription
                if not meeting.transcript and transcript:
                    meeting.transcript = transcript
                await session.commit()

        if self._ws_manager:
            await self._ws_manager.broadcast({
                "type": "meeting_stopped",
                "meeting_id": meeting_id,
            })

        try:
            asyncio.create_task(self._generate_meeting_doc(meeting_id))
        except Exception as e:
            logger.warning(f"Could not schedule doc generation: {e}")

    async def _generate_meeting_doc(self, meeting_id: str) -> None:
        """Background: generate summary and embed meeting doc."""
        try:
            from core.db.engine import async_session
            from core.db.models import Meeting, Workspace
            from core.knowledge.meeting_doc import generate_meeting_document, embed_meeting_document

            async with async_session() as session:
                meeting = await session.get(Meeting, uuid.UUID(meeting_id))
                if not meeting:
                    return
                workspace_result = await session.get(Workspace, meeting.workspace_id)
                workspace_name = workspace_result.name if workspace_result else "personal"
                await generate_meeting_document(meeting_id, session)
                await embed_meeting_document(meeting_id, workspace_name, session)

            logger.info(f"Meeting document generated and embedded: {meeting_id}")
        except Exception as e:
            logger.error(f"Meeting doc generation failed: {e}")

    def on_transition(self, callback: Callable) -> None:
        self._listeners.append(callback)

    def to_dict(self) -> dict:
        return {
            "mode": self._mode.value,
            "workspace": self._workspace,
            "changed_at": self._changed_at.isoformat() if self._changed_at else None,
        }


# Global singleton
state_machine = StateMachine()

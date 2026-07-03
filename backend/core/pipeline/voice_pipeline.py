"""Full voice pipeline: VAD → STT Gate → STT → LLM (stream) → SentenceBuffer → TTS → speakers.

This is the core of the real-time voice interaction.
Audio is played locally via sounddevice.

Barge-in: user speaking mid-response triggers PipelineController.handle_barge_in(),
which cancels the LLM task, stops local playback, and flushes TTS queue.

# TODO: midnight rollover not yet implemented, finalized only on mode/workspace change.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any

import numpy as np

from core.pipeline.sentence_buffer import stream_sentences, SentenceChunk
from core.pipeline.concurrency import pipeline_controller
from core.stt.gate import STTGate

logger = logging.getLogger("core.pipeline.voice_pipeline")


class VoicePipeline:
    """Orchestrates the full voice interaction pipeline.

    Components are injected on construction (or set after init).
    The pipeline itself is stateless — all state lives in PipelineController.
    """

    def __init__(
        self,
        vad=None,
        stt=None,
        stt_gate: STTGate | None = None,
        agent=None,
        tts=None,
        player=None,
        ws_manager=None,
        workspace_config: dict[str, Any] | None = None,
        mic_device: int | str | None = None,
    ) -> None:
        self._vad = vad
        self._stt = stt
        self._stt_gate = stt_gate or STTGate()
        self._agent = agent
        self._tts = tts
        self._player = player
        self._ws_manager = ws_manager
        self._workspace_config = workspace_config or {}
        self._running = False
        self._mic_device = mic_device
        self._mic_capture = None
        self._loop: asyncio.AbstractEventLoop | None = None

        # Companion session state
        self._companion_session = None  # ChatSession | None
        self._companion_workspace: str | None = None
        self._companion_manager = None  # ChatManager | None

        if player:
            pipeline_controller.set_local_player(player)
        if ws_manager:
            pipeline_controller.set_ws_manager(ws_manager)

    def update_workspace(self, config: dict[str, Any]) -> None:
        """Update workspace config (STT gate mode, system prompt, etc.)."""
        self._workspace_config = config
        gate_mode = config.get("stt_gate_mode", "smart")
        self._stt_gate.mode = gate_mode

    async def start(self) -> None:
        """Start the VAD and audio capture loop."""
        if self._running:
            logger.debug("VoicePipeline already running, skip.")
            return

        if not self._vad:
            logger.warning("No VAD configured — voice pipeline idle.")
            return

        # Safety net: ensure STT model is loaded before starting
        if self._stt and not self._stt.loaded:
            logger.info("STT model not loaded — loading now before pipeline start.")
            self._stt.load()

        # Safety net: ensure TTS model is loaded before starting
        if self._tts and hasattr(self._tts, "loaded") and not self._tts.loaded:
            logger.info("TTS model not loaded — loading now before pipeline start.")
            self._tts.load()
        elif self._tts and hasattr(self._tts, "_model") and self._tts._model is None:
            logger.info("TTS model not loaded — loading now before pipeline start.")
            self._tts.load()

        self._running = True

        self._loop = asyncio.get_running_loop()

        self._vad.on_speech_start = self._on_speech_start
        self._vad.on_speech_end = self._on_speech_end
        self._vad.load()

        from core.audio.capture import MicrophoneCapture
        self._mic_capture = MicrophoneCapture(
            chunk_callback=self._vad.process_chunk,
            device=self._mic_device,
        )
        self._mic_capture.start()

        # Start background audio playback loop
        if self._player:
            await self._player.start()

        logger.info("Voice pipeline started (mic → VAD → STT → LLM → TTS).")

    async def stop(self) -> None:
        """Stop the pipeline and cancel any running response."""
        self._running = False
        if self._mic_capture and self._mic_capture.running:
            self._mic_capture.stop()
            self._mic_capture = None
        await pipeline_controller.handle_barge_in()
        if self._player:
            await self._player.shutdown()
        logger.info("Voice pipeline stopped.")

    def _on_speech_start(self) -> None:
        """Called by VAD when speech begins (from PortAudio thread)."""
        if self._player and self._player.playing and self._loop:
            asyncio.run_coroutine_threadsafe(self._trigger_barge_in(), self._loop)
        logger.debug("Speech start detected.")

    def _on_speech_end(self, audio: np.ndarray) -> None:
        """Called by VAD when user finishes speaking (from PortAudio thread)."""
        if not self._running or not self._loop:
            return
        asyncio.run_coroutine_threadsafe(self._process_speech(audio), self._loop)

    async def _trigger_barge_in(self) -> None:
        """Handle barge-in asynchronously."""
        logger.info("Barge-in triggered.")
        await pipeline_controller.handle_barge_in()

    async def _process_speech(self, audio: np.ndarray) -> None:
        """Full pipeline: audio → STT → LLM → TTS → speakers."""
        # 1. STT Gate
        if not self._stt:
            return

        decision = self._stt_gate.evaluate(audio)
        if not decision.should_transcribe:
            logger.debug(f"STT Gate rejected: {decision.reason}")
            return

        logger.debug(f"STT Gate accepted: {decision.reason}")

        # 2. STT transcription
        try:
            from core.config import settings
            language = self._workspace_config.get("language") or getattr(settings, "tts_language", None)
            if language == "auto":
                language = None
            result = self._stt.transcribe(audio, language=language)
        except Exception as e:
            logger.error(f"STT transcription failed: {e}")
            return

        if not result.text.strip():
            logger.debug("Empty transcription — skipping.")
            return

        # 3. Hallucination filter — drop before LLM dispatch or any recording
        from core.state_machine import StateMachine
        if StateMachine._is_hallucination(result):
            logger.debug("Hallucination filtered: '%s'", result.text)
            return

        logger.info(f"Transcribed: '{result.text}' (lang={result.language}, conf={result.confidence:.2f})")

        # 4. Record user turn for companion mode
        await self._record_user_turn(result)

        # Notify WebSocket clients of status
        await self._broadcast_status("thinking")

        # 5. Run LLM response as a cancellable task
        detected_language = result.language if result.language else None
        await pipeline_controller.run_response(
            lambda: self._llm_tts_response(result.text, detected_language)
        )

    async def _llm_tts_response(self, user_text: str, detected_language: str | None = None) -> None:
        """LLM streaming → SentenceBuffer → overlapping TTS → playback queue.

        While sentence N plays, sentence N+1 is already being synthesized,
        eliminating inter-sentence gaps.
        """
        if not self._agent:
            logger.warning("No agent configured.")
            return

        system_prompt = self._workspace_config.get("system_prompt")
        if system_prompt:
            self._agent.update_system_prompt(system_prompt)

        async def token_stream():
            async for token in self._agent.astream_tokens(user_text):
                yield token

        full_response: list[str] = []
        await self._broadcast_status("speaking")

        if not self._tts:
            logger.warning("TTS is None — voice output disabled.")
        if not self._player:
            logger.warning("Audio player is None — cannot play audio.")

        try:
            prev_tts_task: asyncio.Task | None = None

            async for chunk in stream_sentences(token_stream(), language=detected_language):
                if not chunk.text.strip():
                    continue

                full_response.append(chunk.text)
                logger.debug(f"Sentence ready for TTS: '{chunk.text[:60]}'")

                # Wait for the previous synthesis task (preserves playback order)
                if prev_tts_task:
                    await prev_tts_task

                # Synthesize this sentence in the background while the previous plays
                if self._tts:
                    prev_tts_task = asyncio.create_task(
                        self._synthesize_and_enqueue(chunk.text, chunk.language)
                    )
                else:
                    prev_tts_task = None
                    logger.debug("Skipping TTS (no engine loaded).")

            # Wait for the last synthesis task
            if prev_tts_task:
                await prev_tts_task

            # Wait for all enqueued audio to finish playing
            if self._player:
                await self._player.drain()

        except asyncio.CancelledError:
            logger.debug("LLM/TTS response cancelled (barge-in).")
            raise

        finally:
            await self._broadcast_status("listening")

        response_text = " ".join(full_response)
        logger.info(f"Response complete: '{response_text[:100]}'")

        # Record assistant turn for companion mode
        await self._record_assistant_turn(response_text)

        if self._ws_manager:
            await self._ws_manager.broadcast({
                "type": "text_complete",
                "text": response_text,
            })

    async def start_companion_session(self, workspace: str) -> None:
        """Initialize (or restore) today's companion chat session."""
        try:
            from core.memory.chat_history import ChatManager
            from core.db.engine import async_session

            self._companion_manager = ChatManager(workspace=workspace)
            self._companion_workspace = workspace
            async with async_session() as session:
                self._companion_session = await self._companion_manager.get_or_create_daily_companion_session(
                    session, date.today()
                )
            logger.info("Companion session ready: %s", self._companion_session.session_id)
        except Exception as e:
            logger.error("Failed to start companion session: %s", e)
            self._companion_session = None
            self._companion_manager = None

    async def finalize_companion_session(self) -> None:
        """Finalize the active companion session (called on mode/workspace change)."""
        if not self._companion_session or not self._companion_manager:
            return
        session_id = self._companion_session.session_id
        manager = self._companion_manager
        try:
            from core.db.engine import async_session
            llm = getattr(self._agent, "_llm", None) if self._agent else None
            async with async_session() as session:
                await manager.finalize_companion_session(session, session_id, llm=llm)
            logger.info("Companion session finalized: %s", session_id)
        except Exception as e:
            logger.error("Failed to finalize companion session: %s", e)
        finally:
            self._companion_session = None
            self._companion_workspace = None
            self._companion_manager = None

    async def _record_user_turn(self, result) -> None:
        """Append user turn to companion session (non-fatal)."""
        if not self._companion_session:
            return
        try:
            from core.db.engine import async_session
            async with async_session() as session:
                await self._companion_session.append(session, role="user", content=result.text)
        except Exception as e:
            logger.warning("Could not record user turn: %s", e)

    async def _record_assistant_turn(self, response_text: str) -> None:
        """Append assistant turn to companion session (non-fatal)."""
        if not self._companion_session or not response_text.strip():
            return
        try:
            from core.db.engine import async_session
            async with async_session() as session:
                await self._companion_session.append(session, role="assistant", content=response_text)
        except Exception as e:
            logger.warning("Could not record assistant turn: %s", e)

    async def _synthesize_and_enqueue(self, text: str, language: str | None) -> None:
        """Synthesize one sentence and enqueue it for playback."""
        try:
            logger.debug(f"TTS synthesizing ({type(self._tts).__name__}): '{text[:40]}'")
            audio = await self._tts.asynthesize(text, language=language)
            if audio is not None and len(audio) > 0:
                logger.debug(f"TTS produced {len(audio)} samples, enqueuing...")
                if self._player:
                    await self._player.enqueue(audio)
            else:
                logger.warning(f"TTS returned empty audio for: '{text[:40]}'")
        except Exception as e:
            logger.error(f"TTS synthesis failed: {e}", exc_info=True)

    async def _broadcast_status(self, status: str) -> None:
        """Broadcast system status to all WebSocket clients."""
        if self._ws_manager:
            try:
                await self._ws_manager.broadcast({"type": "status", "status": status})
            except Exception:
                pass  # WS errors don't affect voice pipeline

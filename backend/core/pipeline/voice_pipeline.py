"""Full voice pipeline: VAD → STT Gate → STT → LLM (stream) → SentenceBuffer → TTS → speakers.

This is the core of the real-time voice interaction.
Audio is played locally via sounddevice.
If an avatar WebSocket client is connected, audio chunks are also sent there.

Barge-in: user speaking mid-response triggers PipelineController.handle_barge_in(),
which cancels the LLM task, stops local playback, and flushes TTS queue.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import numpy as np

from core.pipeline.sentence_buffer import stream_sentences
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

        # Wire player and ws_manager into pipeline_controller
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
        if not self._vad:
            logger.warning("No VAD configured — voice pipeline idle.")
            return

        self._running = True
        logger.info("Voice pipeline started.")

        # Set up VAD callbacks
        self._vad.on_speech_start = self._on_speech_start
        self._vad.on_speech_end = self._on_speech_end
        self._vad.load()

    async def stop(self) -> None:
        """Stop the pipeline and cancel any running response."""
        self._running = False
        await pipeline_controller.handle_barge_in()
        logger.info("Voice pipeline stopped.")

    def _on_speech_start(self) -> None:
        """Called by VAD when speech begins."""
        # If assistant is speaking, trigger barge-in
        if self._player and self._player.playing:
            asyncio.ensure_future(self._trigger_barge_in())
        logger.debug("Speech start detected.")

    def _on_speech_end(self, audio: np.ndarray) -> None:
        """Called by VAD when user finishes speaking."""
        if not self._running:
            return
        asyncio.ensure_future(self._process_speech(audio))

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
            language = self._workspace_config.get("language")
            result = self._stt.transcribe(audio, language=language)
        except Exception as e:
            logger.error(f"STT transcription failed: {e}")
            return

        if not result.text.strip():
            logger.debug("Empty transcription — skipping.")
            return

        logger.info(f"Transcribed: '{result.text}' (lang={result.language}, conf={result.confidence:.2f})")

        # Notify WebSocket clients of status
        await self._broadcast_status("thinking")

        # 3. Run LLM response as a cancellable task
        await pipeline_controller.run_response(
            lambda: self._llm_tts_response(result.text)
        )

    async def _llm_tts_response(self, user_text: str) -> None:
        """LLM streaming → SentenceBuffer → TTS → playback."""
        if not self._agent:
            logger.warning("No agent configured.")
            return

        system_prompt = self._workspace_config.get("system_prompt")

        async def token_stream():
            async for token in self._agent.astream_tokens(user_text, system_prompt=system_prompt):
                yield token

        full_response = []

        await self._broadcast_status("speaking")

        try:
            async for sentence in stream_sentences(token_stream()):
                if not sentence.strip():
                    continue

                full_response.append(sentence)

                # Synthesize and play
                if self._tts:
                    audio = await self._tts.asynthesize(sentence)
                    if audio is not None and len(audio) > 0:
                        await self._play_and_stream(audio)

        except asyncio.CancelledError:
            logger.debug("LLM/TTS response cancelled (barge-in).")
            raise

        finally:
            await self._broadcast_status("listening")

        response_text = " ".join(full_response)
        logger.info(f"Response complete: '{response_text[:100]}...'")

        # Broadcast text_complete to WebSocket clients
        if self._ws_manager:
            await self._ws_manager.broadcast({
                "type": "text_complete",
                "text": response_text,
            })

    async def _play_and_stream(self, audio: np.ndarray) -> None:
        """Play audio locally AND send to avatar if connected."""
        tasks = []

        # Local playback (primary)
        if self._player:
            tasks.append(self._player.play(audio))

        # Avatar stream (secondary — only if avatar connected)
        if self._ws_manager and self._ws_manager.has_avatar_client():
            import base64
            audio_b64 = base64.b64encode(audio.tobytes()).decode()
            tasks.append(
                self._ws_manager.broadcast(
                    {"type": "audio_chunk", "data": audio_b64, "sample_rate": self._tts.sample_rate},
                    avatar_only=True,
                )
            )

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _broadcast_status(self, status: str) -> None:
        """Broadcast system status to all WebSocket clients."""
        if self._ws_manager:
            try:
                await self._ws_manager.broadcast({"type": "status", "status": status})
            except Exception:
                pass  # WS errors don't affect voice pipeline

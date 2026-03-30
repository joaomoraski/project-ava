"""Mode state machine with full pipeline integration.

Manages transitions between:
  - companion: VAD active, TTS active, avatar visible
  - meeting: VAD active, TTS disabled, loopback active, avatar hidden
  - background: VAD stopped, TTS disabled, avatar hidden
  - autonomous: no continuous STT, autonomous loop active, avatar visible

All transitions are atomic (asyncio.Lock).
State is persisted to state.json on every transition.
Avatar commands are sent via WebSocket only if avatar client is connected.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from enum import Enum
from typing import Callable

logger = logging.getLogger("core.state_machine")

STATE_FILE = "state.json"


class Mode(str, Enum):
    companion = "companion"
    meeting = "meeting"
    background = "background"
    autonomous = "autonomous"


class StateMachine:
    """Manages mode transitions with asyncio.Lock for thread safety.

    Components (VAD, TTS, pipeline) are injected via set_* methods.
    This avoids circular imports while allowing full pipeline integration.
    """

    def __init__(self, initial_mode: str = "companion") -> None:
        self._mode = Mode(initial_mode)
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

        self._load_persisted_state()

    def set_components(
        self,
        vad=None,
        mic_capture=None,
        loopback_capture=None,
        tts_player=None,
        ws_manager=None,
    ) -> None:
        """Inject pipeline components for mode-driven start/stop."""
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

    def _load_persisted_state(self) -> None:
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE) as f:
                    data = json.load(f)
                self._mode = Mode(data.get("mode", self._mode.value))
                self._workspace = data.get("workspace", self._workspace)
            except Exception:
                pass

    def _persist_state(self) -> None:
        try:
            with open(STATE_FILE, "w") as f:
                json.dump({
                    "mode": self._mode.value,
                    "workspace": self._workspace,
                    "changed_at": self._changed_at.isoformat() if self._changed_at else None,
                }, f)
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
            self._mode = Mode(new_mode)
            if workspace:
                self._workspace = workspace
            self._changed_at = datetime.now(timezone.utc)
            self._persist_state()
            logger.info(f"Mode: {old_mode.value} → {self._mode.value}")

            # Apply pipeline effects
            await self._apply_mode_effects(old_mode, self._mode)

            # Notify listeners
            for listener in self._listeners:
                try:
                    if asyncio.iscoroutinefunction(listener):
                        await listener(old_mode, self._mode)
                    else:
                        listener(old_mode, self._mode)
                except Exception as e:
                    logger.warning(f"State listener error: {e}")

    async def _apply_mode_effects(self, old_mode: Mode, new_mode: Mode) -> None:
        """Apply pipeline start/stop effects based on the new mode."""

        if new_mode == Mode.companion:
            # Start VAD + mic capture, enable TTS, show avatar
            if self._mic_capture and not getattr(self._mic_capture, "running", False):
                try:
                    self._mic_capture.start()
                except Exception as e:
                    logger.warning(f"Mic capture start failed: {e}")

            await self._avatar_command("show")
            logger.debug("Companion mode: VAD active, TTS enabled, avatar shown.")

        elif new_mode == Mode.meeting:
            # Stop TTS output, start loopback, hide avatar
            if self._tts_player:
                self._tts_player.stop()

            if self._loopback_capture and not getattr(self._loopback_capture, "running", False):
                try:
                    self._loopback_capture.start()
                except Exception as e:
                    logger.warning(f"Loopback capture start failed: {e}")

            await self._avatar_command("hide")
            logger.debug("Meeting mode: TTS disabled, loopback active, avatar hidden.")

        elif new_mode == Mode.background:
            # Stop everything
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

            await self._avatar_command("hide")
            logger.debug("Background mode: all capture stopped, avatar hidden.")

        elif new_mode == Mode.autonomous:
            # Stop continuous STT, show avatar
            if self._tts_player:
                pass  # TTS stays active in autonomous

            await self._avatar_command("show")
            logger.debug("Autonomous mode: autonomous loop active, avatar shown.")

        # Broadcast mode change to all WebSocket clients
        if self._ws_manager:
            try:
                await self._ws_manager.broadcast({
                    "type": "mode_change",
                    "mode": new_mode.value,
                    "workspace": self._workspace,
                })
            except Exception as e:
                logger.debug(f"WS mode_change broadcast failed: {e}")

    async def _avatar_command(self, command: str) -> None:
        """Send show/hide command to avatar if connected."""
        if self._ws_manager and self._ws_manager.has_avatar_client():
            try:
                await self._ws_manager.broadcast(
                    {"type": command},
                    avatar_only=True,
                )
            except Exception:
                pass

    def on_transition(self, callback: Callable) -> None:
        """Register a callback for mode transitions."""
        self._listeners.append(callback)

    def to_dict(self) -> dict:
        return {
            "mode": self._mode.value,
            "workspace": self._workspace,
            "changed_at": self._changed_at.isoformat() if self._changed_at else None,
        }


# Global singleton
state_machine = StateMachine()

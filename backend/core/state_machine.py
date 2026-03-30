"""Mode state machine — fully implemented in B11.
Skeleton: tracks current mode and provides transition interface."""
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

    Full pipeline integration (VAD start/stop, TTS enable/disable,
    avatar show/hide) implemented in B11.
    """

    def __init__(self, initial_mode: str = "companion") -> None:
        self._mode = Mode(initial_mode)
        self._workspace: str = "personal"
        self._lock = asyncio.Lock()
        self._changed_at: datetime | None = None
        self._listeners: list[Callable] = []
        self._load_persisted_state()

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
        """Atomic mode transition."""
        async with self._lock:
            old_mode = self._mode
            self._mode = Mode(new_mode)
            if workspace:
                self._workspace = workspace
            self._changed_at = datetime.now(timezone.utc)
            self._persist_state()
            logger.info(f"Mode: {old_mode.value} → {self._mode.value}")

            for listener in self._listeners:
                try:
                    if asyncio.iscoroutinefunction(listener):
                        await listener(old_mode, self._mode)
                    else:
                        listener(old_mode, self._mode)
                except Exception as e:
                    logger.warning(f"State listener error: {e}")

    def on_transition(self, callback: Callable) -> None:
        """Register a callback for mode transitions."""
        self._listeners.append(callback)

    def to_dict(self) -> dict:
        return {
            "mode": self._mode.value,
            "workspace": self._workspace,
            "changed_at": self._changed_at.isoformat() if self._changed_at else None,
        }


# Global singleton — will be integrated with pipeline in B11
state_machine = StateMachine()

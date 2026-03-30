"""Autonomous mode — independent task management without continuous audio.

The assistant acts according to the workspace system prompt + proactivity config.
Can take initiative: reminders, scheduled checks, proactive suggestions.
Notifies user via TTS (if TTS is enabled) or OS notification.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("core.autonomous")

# How often the autonomous loop checks for pending tasks (seconds)
DEFAULT_CHECK_INTERVAL = 60


class AutonomousAgent:
    """Runs independent tasks in autonomous mode.

    Operates without waiting for user speech.
    Uses the workspace system prompt + tools to manage tasks proactively.
    """

    def __init__(
        self,
        workspace: str = "personal",
        workspace_config: dict[str, Any] | None = None,
        agent=None,
        tts_player=None,
        ws_manager=None,
    ) -> None:
        self._workspace = workspace
        self._workspace_config = workspace_config or {}
        self._agent = agent
        self._tts_player = tts_player
        self._ws_manager = ws_manager
        self._running = False
        self._check_interval = DEFAULT_CHECK_INTERVAL
        self._task: asyncio.Task | None = None

    def set_agent(self, agent) -> None:
        self._agent = agent

    def set_player(self, player) -> None:
        self._tts_player = player

    def set_ws_manager(self, ws_manager) -> None:
        self._ws_manager = ws_manager

    async def start(self) -> None:
        """Start the autonomous loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info(f"Autonomous agent started for workspace: {self._workspace}")

    async def stop(self) -> None:
        """Stop the autonomous loop."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Autonomous agent stopped.")

    async def _run_loop(self) -> None:
        """Main autonomous loop — checks for pending actions periodically."""
        proactivity = self._workspace_config.get("proactivity", "medium")
        interval = self._get_interval(proactivity)

        while self._running:
            try:
                await self._check_and_act()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Autonomous loop error: {e}")

            await asyncio.sleep(interval)

    def _get_interval(self, proactivity: str) -> float:
        """Map proactivity level to check interval in seconds."""
        return {
            "high": 30,
            "medium": 60,
            "low": 300,
        }.get(proactivity, 60)

    async def _check_and_act(self) -> None:
        """Single autonomous check cycle."""
        if not self._agent:
            return

        system_prompt = self._workspace_config.get("system_prompt", "")
        proactivity = self._workspace_config.get("proactivity", "medium")

        if proactivity == "low":
            return  # low proactivity: only respond when called

        now = datetime.now(timezone.utc)
        context_prompt = (
            f"You are operating autonomously. Current time: {now.strftime('%Y-%m-%d %H:%M UTC')}.\n"
            f"Workspace: {self._workspace}.\n"
            "Check if there is anything important to do or communicate to the user. "
            "If nothing is urgent or noteworthy, respond with exactly: IDLE\n"
            "Otherwise, provide a brief, actionable message to speak aloud."
        )

        try:
            response = await self._agent.invoke(
                context_prompt,
                history=[],
            )
        except Exception as e:
            logger.debug(f"Autonomous check failed: {e}")
            return

        if not response or response.strip().upper() == "IDLE":
            logger.debug("Autonomous check: nothing to do.")
            return

        logger.info(f"Autonomous action: {response[:100]}")
        await self._speak(response)

    async def _speak(self, text: str) -> None:
        """Speak a message (TTS) and/or send OS notification."""
        # Try TTS first
        if self._tts_player:
            try:
                # Synthesize via whichever TTS engine is configured
                from core.config import settings
                audio = None

                if settings.tts_engine == "kokoro":
                    from core.tts.kokoro import KokoroTTS
                    tts = KokoroTTS()
                    if tts.loaded:
                        audio = await tts.asynthesize(text)
                    else:
                        logger.debug("KokoroTTS not loaded — sending OS notification instead.")
                elif settings.tts_engine == "xtts":
                    from core.tts.xtts import XTTSS
                    tts = XTTSS()
                    if tts.loaded:
                        audio = await tts.asynthesize(text)

                if audio is not None:
                    await self._tts_player.play(audio)
                else:
                    await self._os_notification(text)
            except Exception as e:
                logger.warning(f"TTS speak failed in autonomous mode: {e}")
                await self._os_notification(text)
        else:
            await self._os_notification(text)

        # Also send to WebSocket clients
        if self._ws_manager:
            try:
                await self._ws_manager.broadcast({
                    "type": "text_complete",
                    "text": text,
                    "source": "autonomous",
                })
            except Exception:
                pass

    async def _os_notification(self, text: str) -> None:
        """Send OS desktop notification as fallback."""
        try:
            import subprocess, sys
            if sys.platform == "linux":
                subprocess.Popen(
                    ["notify-send", "Ava", text[:200]],
                    start_new_session=True,
                )
            elif sys.platform == "darwin":
                subprocess.Popen(
                    ["osascript", "-e", f'display notification "{text[:200]}" with title "Ava"'],
                    start_new_session=True,
                )
            elif sys.platform == "win32":
                # Windows notifications via plyer or win10toast (optional)
                logger.info(f"[Autonomous notification]: {text[:200]}")
        except Exception as e:
            logger.debug(f"OS notification failed: {e}")
            logger.info(f"[Autonomous]: {text[:200]}")

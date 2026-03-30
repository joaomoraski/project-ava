"""Animation registry and intent detection for avatar animations.

Loads animation definitions from config/animations.json.
Detects animation intent from LLM response text.
Sends WebSocket animation events if avatar client is connected.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

logger = logging.getLogger("core.animations")

ANIMATIONS_CONFIG = "config/animations.json"


class AnimationRegistry:
    """Loads and queries the animation configuration.

    Intent detection: scans response text for trigger keywords and
    returns the matching animation name (first match wins).
    """

    def __init__(self) -> None:
        self._config: dict[str, Any] = {}
        self._animations: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if os.path.exists(ANIMATIONS_CONFIG):
            try:
                with open(ANIMATIONS_CONFIG) as f:
                    self._config = json.load(f)
                self._animations = self._config.get("animations", {})
                logger.debug(f"Loaded {len(self._animations)} animations.")
            except Exception as e:
                logger.error(f"Failed to load animations config: {e}")
        else:
            logger.warning(f"Animations config not found: {ANIMATIONS_CONFIG}")

    def detect_intent(self, text: str) -> str | None:
        """Scan text for animation trigger keywords.

        Returns the animation name if a trigger is found, else None.
        """
        if not self._config.get("intent_detection_enabled", True):
            return None

        text_lower = text.lower()

        for anim_name, anim_config in self._animations.items():
            if anim_name == "idle":
                continue  # idle is only triggered explicitly
            triggers = anim_config.get("triggers", [])
            for trigger in triggers:
                if re.search(r'\b' + re.escape(trigger.lower()) + r'\b', text_lower):
                    return anim_name

        return None

    def list_animations(self) -> list[str]:
        """Return all registered animation names."""
        return list(self._animations.keys())

    def get_default(self) -> str:
        """Return the default (idle) animation name."""
        return self._config.get("default_animation", "idle")

    def is_valid(self, name: str) -> bool:
        return name in self._animations

    def reload(self) -> None:
        """Reload config from disk."""
        self._load()


class AnimationDispatcher:
    """Sends animation events to the avatar via WebSocket.

    Only sends if an avatar WebSocket client is connected.
    """

    def __init__(self, registry: AnimationRegistry, ws_manager=None) -> None:
        self._registry = registry
        self._ws_manager = ws_manager

    def set_ws_manager(self, ws_manager) -> None:
        self._ws_manager = ws_manager

    async def trigger(self, animation_name: str) -> bool:
        """Trigger a named animation. Returns True if sent."""
        if not self._registry.is_valid(animation_name):
            logger.warning(f"Unknown animation: {animation_name}")
            return False

        if not self._ws_manager or not self._ws_manager.has_avatar_client():
            logger.debug(f"Animation '{animation_name}' skipped — no avatar connected.")
            return False

        try:
            await self._ws_manager.broadcast(
                {"type": "animation", "name": animation_name},
                avatar_only=True,
            )
            logger.debug(f"Animation triggered: {animation_name}")
            return True
        except Exception as e:
            logger.error(f"Animation dispatch failed: {e}")
            return False

    async def auto_detect_and_trigger(self, text: str) -> str | None:
        """Detect intent from text and trigger animation if found.

        Returns the triggered animation name or None.
        """
        animation = self._registry.detect_intent(text)
        if animation:
            await self.trigger(animation)
        return animation


# Global singletons
animation_registry = AnimationRegistry()
animation_dispatcher = AnimationDispatcher(animation_registry)

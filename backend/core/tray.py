"""System tray icon and global hotkeys.

Cross-platform system tray via pystray.
Global hotkeys via the keyboard library.

The tray icon works even without the Electron avatar — it's a backend feature.
Avatar-specific functionality (show/hide avatar) is sent via WebSocket if connected.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

from core.config import settings

logger = logging.getLogger("core.tray")

# Mode cycle order for tray menu
MODES = ["companion", "meeting", "background", "autonomous"]


class SystemTray:
    """Cross-platform system tray icon with mode switching and workspace selection.

    Runs in a separate thread (pystray requirement).
    Communicates with the async pipeline via asyncio.create_task().
    """

    def __init__(self, event_loop: asyncio.AbstractEventLoop | None = None) -> None:
        self._icon = None
        self._running = False
        self._loop = event_loop
        self._tray_thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the system tray in a background thread."""
        self._tray_thread = threading.Thread(target=self._run_tray, daemon=True)
        self._tray_thread.start()
        logger.info("System tray started.")

    def stop(self) -> None:
        """Stop the system tray."""
        if self._icon:
            self._icon.stop()
        self._running = False
        logger.info("System tray stopped.")

    def _run_tray(self) -> None:
        """Tray thread — blocks until stop() is called."""
        try:
            import pystray
            from PIL import Image, ImageDraw
        except ImportError:
            logger.warning("pystray or Pillow not installed — system tray unavailable.")
            return

        icon_image = self._create_icon_image()

        menu = pystray.Menu(
            pystray.MenuItem("Companion", lambda: self._switch_mode("companion")),
            pystray.MenuItem("Meeting Mode", lambda: self._switch_mode("meeting")),
            pystray.MenuItem("Background Mode", lambda: self._switch_mode("background")),
            pystray.MenuItem("Autonomous Mode", lambda: self._switch_mode("autonomous")),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Open Dashboard", self._open_dashboard),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self._quit),
        )

        self._icon = pystray.Icon(
            "Ava",
            icon_image,
            "Ava — AI Assistant",
            menu=menu,
        )
        self._running = True

        try:
            self._icon.run()
        except Exception as e:
            logger.error(f"System tray error: {e}")

    def _create_icon_image(self):
        """Create a simple colored circle icon."""
        try:
            from PIL import Image, ImageDraw
            img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            draw.ellipse((4, 4, 60, 60), fill=(88, 101, 242, 255))  # Discord blue
            return img
        except Exception:
            return None

    def _switch_mode(self, mode: str) -> None:
        """Schedule a mode transition on the event loop."""
        if self._loop and not self._loop.is_closed():
            try:
                from core.state_machine import state_machine
                asyncio.run_coroutine_threadsafe(
                    state_machine.transition(mode),
                    self._loop,
                )
                logger.info(f"Tray: switching to {mode} mode.")
            except Exception as e:
                logger.error(f"Tray mode switch failed: {e}")

    def _open_dashboard(self) -> None:
        """Open the Next.js dashboard in the browser."""
        import webbrowser
        webbrowser.open(f"http://localhost:{settings.frontend_port}")

    def _quit(self) -> None:
        """Quit the backend."""
        logger.info("Quit requested from system tray.")
        if self._icon:
            self._icon.stop()
        import os, signal
        os.kill(os.getpid(), signal.SIGTERM)


class HotkeyManager:
    """Registers global hotkeys for mode switching and workspace toggling.

    Uses the keyboard library which requires OS-level access.
    On Linux: may require running as root or adding user to input group.
    """

    def __init__(self, event_loop: asyncio.AbstractEventLoop | None = None) -> None:
        self._loop = event_loop
        self._registered: list[str] = []

    def setup(self) -> None:
        """Register all configured hotkeys."""
        try:
            import keyboard
        except ImportError:
            logger.warning("keyboard library not installed — hotkeys unavailable.")
            return
        except Exception as e:
            logger.warning(f"keyboard library unavailable: {e}")
            return

        try:
            keyboard.add_hotkey(settings.meeting_hotkey, self._on_meeting_hotkey)
            self._registered.append(settings.meeting_hotkey)
            logger.info(f"Hotkey registered: {settings.meeting_hotkey} → meeting mode")
        except Exception as e:
            logger.warning(f"Failed to register hotkey {settings.meeting_hotkey}: {e}")

        try:
            keyboard.add_hotkey(settings.toggle_workspace_hotkey, self._on_workspace_hotkey)
            self._registered.append(settings.toggle_workspace_hotkey)
            logger.info(f"Hotkey registered: {settings.toggle_workspace_hotkey} → toggle workspace")
        except Exception as e:
            logger.warning(f"Failed to register hotkey {settings.toggle_workspace_hotkey}: {e}")

    def teardown(self) -> None:
        """Unregister all hotkeys."""
        try:
            import keyboard
            for hotkey in self._registered:
                try:
                    keyboard.remove_hotkey(hotkey)
                except Exception:
                    pass
            self._registered.clear()
        except ImportError:
            pass

    def _on_meeting_hotkey(self) -> None:
        """Toggle between companion and meeting mode."""
        if not self._loop:
            return
        try:
            from core.state_machine import state_machine
            new_mode = "meeting" if state_machine.mode.value != "meeting" else "companion"
            asyncio.run_coroutine_threadsafe(
                state_machine.transition(new_mode),
                self._loop,
            )
        except Exception as e:
            logger.error(f"Meeting hotkey failed: {e}")

    def _on_workspace_hotkey(self) -> None:
        """Cycle to the next available workspace."""
        try:
            from core.workspace import list_workspaces
            from core.state_machine import state_machine
            workspaces = sorted(list_workspaces())
            if not workspaces:
                return
            current = state_machine.workspace
            try:
                idx = workspaces.index(current)
                next_ws = workspaces[(idx + 1) % len(workspaces)]
            except ValueError:
                next_ws = workspaces[0]

            if self._loop:
                asyncio.run_coroutine_threadsafe(
                    state_machine.transition(state_machine.mode.value, workspace=next_ws),
                    self._loop,
                )
                logger.info(f"Workspace switched to: {next_ws}")
        except Exception as e:
            logger.error(f"Workspace hotkey failed: {e}")

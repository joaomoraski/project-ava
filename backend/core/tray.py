"""System tray icon.

Cross-platform system tray via pystray.
"""
from __future__ import annotations

import asyncio
import logging
import threading

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
            "Ava - AI Assistant",
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

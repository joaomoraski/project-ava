"""System control tool — OS-level actions via pyautogui/psutil."""
from __future__ import annotations

import logging
import subprocess
import sys

from langchain_core.tools import tool

logger = logging.getLogger("tools.system_control")


@tool
def get_system_info() -> str:
    """Get basic system information: CPU usage, memory usage, running processes."""
    try:
        import psutil
        cpu = psutil.cpu_percent(interval=0.5)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        return (
            f"CPU: {cpu}%\n"
            f"Memory: {mem.percent}% used ({mem.used // 1024**3}GB / {mem.total // 1024**3}GB)\n"
            f"Disk: {disk.percent}% used ({disk.used // 1024**3}GB / {disk.total // 1024**3}GB)"
        )
    except ImportError:
        return "system_info unavailable: install psutil (pip install psutil)"
    except Exception as e:
        return f"Error getting system info: {e}"


@tool
def open_application(app_name: str) -> str:
    """Open an application by name.

    Args:
        app_name: application name or path (e.g., 'firefox', 'code', 'terminal')
    """
    try:
        if sys.platform == "linux":
            subprocess.Popen([app_name], start_new_session=True)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-a", app_name])
        elif sys.platform == "win32":
            subprocess.Popen(["start", app_name], shell=True)
        return f"Opened {app_name}"
    except FileNotFoundError:
        return f"Application not found: {app_name}"
    except Exception as e:
        logger.error(f"open_application failed: {e}")
        return f"Failed to open {app_name}: {e}"


@tool
def take_screenshot() -> str:
    """Take a screenshot and save it to ~/Pictures/screenshot_<timestamp>.png.
    Returns the file path."""
    try:
        import pyautogui
        from datetime import datetime
        import os

        pictures = os.path.expanduser("~/Pictures")
        os.makedirs(pictures, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(pictures, f"screenshot_{timestamp}.png")
        img = pyautogui.screenshot()
        img.save(path)
        return f"Screenshot saved: {path}"
    except ImportError:
        return "screenshot unavailable: install pyautogui (pip install pyautogui)"
    except Exception as e:
        logger.error(f"take_screenshot failed: {e}")
        return f"Screenshot failed: {e}"

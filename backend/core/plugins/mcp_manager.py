"""MCP server lifecycle manager.

Starts, stops, and monitors MCP servers as subprocesses.
Discovers their tools dynamically via the MCP protocol over stdio.
Injects secrets from the encrypted vault into subprocess env vars.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool

logger = logging.getLogger("core.plugins.mcp_manager")

MCP_CONFIG_FILE = "mcp/servers.json"
MCP_PID_DIR = "mcp/pids"


class McpServerProcess:
    """Represents a running MCP server subprocess."""

    def __init__(self, name: str, config: dict[str, Any]) -> None:
        self.name = name
        self.config = config
        self.process: subprocess.Popen | None = None
        self.started_at: float | None = None
        self.last_error: str | None = None
        self._tools: list[BaseTool] = []

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    @property
    def uptime_seconds(self) -> float | None:
        if self.started_at and self.running:
            return time.time() - self.started_at
        return None

    def start(self, env_overrides: dict[str, str] | None = None) -> bool:
        """Start the MCP server subprocess."""
        if self.running:
            logger.debug(f"MCP server '{self.name}' already running.")
            return True

        cmd = [self.config.get("command", "npx")] + self.config.get("args", [])
        env = os.environ.copy()

        # Inject resolved secrets
        if env_overrides:
            env.update(env_overrides)

        try:
            self.process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                text=True,
            )
            self.started_at = time.time()
            self.last_error = None
            logger.info(f"MCP server '{self.name}' started (PID {self.process.pid})")
            self._save_pid()
            return True
        except FileNotFoundError as e:
            self.last_error = f"Command not found: {cmd[0]}"
            logger.error(f"MCP server '{self.name}' start failed: {self.last_error}")
            return False
        except Exception as e:
            self.last_error = str(e)
            logger.error(f"MCP server '{self.name}' start failed: {e}")
            return False

    def stop(self) -> None:
        """Stop the MCP server subprocess."""
        if not self.running:
            return
        try:
            self.process.terminate()
            self.process.wait(timeout=5)
            logger.info(f"MCP server '{self.name}' stopped.")
        except subprocess.TimeoutExpired:
            self.process.kill()
            logger.warning(f"MCP server '{self.name}' force-killed.")
        except Exception as e:
            logger.error(f"Error stopping MCP server '{self.name}': {e}")
        finally:
            self.process = None
            self.started_at = None
            self._remove_pid()

    def _save_pid(self) -> None:
        os.makedirs(MCP_PID_DIR, exist_ok=True)
        pid_file = os.path.join(MCP_PID_DIR, f"{self.name}.pid")
        with open(pid_file, "w") as f:
            f.write(str(self.process.pid))

    def _remove_pid(self) -> None:
        pid_file = os.path.join(MCP_PID_DIR, f"{self.name}.pid")
        try:
            os.unlink(pid_file)
        except FileNotFoundError:
            pass

    def get_recent_logs(self, lines: int = 50) -> list[str]:
        """Read recent stderr output from the MCP server."""
        if not self.process or not self.process.stderr:
            return []
        # Non-blocking read
        output = []
        try:
            import select
            if select.select([self.process.stderr], [], [], 0)[0]:
                for line in self.process.stderr:
                    output.append(line.rstrip())
                    if len(output) >= lines:
                        break
        except Exception:
            pass
        return output[-lines:]


class McpManager:
    """Manages all MCP server lifecycles.

    Loads server configs from mcp/servers.json.
    Resolves secrets from the vault before starting servers.
    Exposes tools from running servers to the plugin registry.
    """

    def __init__(self) -> None:
        self._servers: dict[str, McpServerProcess] = {}
        self._config: dict[str, Any] = {"servers": {}}
        self._load_config()

    def _load_config(self) -> None:
        """Load server configs from mcp/servers.json."""
        if os.path.exists(MCP_CONFIG_FILE):
            try:
                with open(MCP_CONFIG_FILE) as f:
                    self._config = json.load(f)
            except Exception as e:
                logger.error(f"Failed to load MCP config: {e}")
        else:
            self._config = {"servers": {}}

    def _save_config(self) -> None:
        os.makedirs("mcp", exist_ok=True)
        with open(MCP_CONFIG_FILE, "w") as f:
            json.dump(self._config, f, indent=2)

    def _resolve_secrets(self, server_config: dict[str, Any]) -> dict[str, str]:
        """Resolve ${SECRET_NAME} references from the encrypted vault."""
        try:
            from core.secrets.vault import SecretsVault
            vault = SecretsVault()
            return vault.get_env_for_mcp(server_config)
        except Exception as e:
            logger.warning(f"Secret resolution failed: {e}")
            return {}

    def register_server(self, name: str, config: dict[str, Any]) -> bool:
        """Register a new MCP server config."""
        servers = self._config.setdefault("servers", {})
        if name in servers:
            return False
        servers[name] = config
        self._save_config()
        logger.info(f"MCP server '{name}' registered.")
        return True

    def update_server(self, name: str, config: dict[str, Any]) -> bool:
        """Update an existing MCP server config."""
        if name not in self._config.get("servers", {}):
            return False
        self._config["servers"][name].update(config)
        self._save_config()
        return True

    def unregister_server(self, name: str) -> bool:
        """Unregister and stop a server."""
        self.stop_server(name)
        if name in self._config.get("servers", {}):
            del self._config["servers"][name]
            self._save_config()
            return True
        return False

    def start_server(self, name: str) -> bool:
        """Start a registered MCP server."""
        server_config = self._config.get("servers", {}).get(name)
        if not server_config:
            logger.error(f"MCP server '{name}' not registered.")
            return False

        if name not in self._servers:
            self._servers[name] = McpServerProcess(name, server_config)

        env_vars = self._resolve_secrets(server_config)
        return self._servers[name].start(env_overrides=env_vars)

    def stop_server(self, name: str) -> None:
        """Stop a running MCP server."""
        if name in self._servers:
            self._servers[name].stop()

    def start_auto_start_servers(self) -> None:
        """Start all servers with auto_start=true."""
        for name, config in self._config.get("servers", {}).items():
            if config.get("auto_start") and config.get("enabled", True):
                self.start_server(name)

    def stop_all(self) -> None:
        """Stop all running MCP servers."""
        for server in self._servers.values():
            if server.running:
                server.stop()

    def get_tools(self, server_name: str) -> list[BaseTool]:
        """Return LangChain tools exposed by a running MCP server.

        Note: Full MCP tool discovery requires MCP protocol implementation.
        This returns the statically registered tools for now.
        """
        if server_name not in self._servers or not self._servers[server_name].running:
            return []
        return self._servers[server_name]._tools

    def get_server_status(self, name: str) -> dict[str, Any]:
        """Get status info for a server."""
        config = self._config.get("servers", {}).get(name, {})
        server = self._servers.get(name)

        status = "stopped"
        uptime = None
        last_error = None
        pid = None

        if server:
            if server.running:
                status = "running"
                uptime = server.uptime_seconds
                pid = server.process.pid if server.process else None
            elif server.last_error:
                status = "error"
                last_error = server.last_error

        return {
            "name": name,
            "status": status,
            "command": config.get("command", ""),
            "args": config.get("args", []),
            "description": config.get("description", ""),
            "enabled": config.get("enabled", True),
            "auto_start": config.get("auto_start", False),
            "uptime_seconds": uptime,
            "last_error": last_error,
            "pid": pid,
            "tools_count": len(self.get_tools(name)),
        }

    def list_servers(self) -> list[dict[str, Any]]:
        """List all registered servers with status."""
        return [
            self.get_server_status(name)
            for name in self._config.get("servers", {})
        ]

    def get_server_logs(self, name: str) -> list[str]:
        """Get recent logs from a running server."""
        if name in self._servers:
            return self._servers[name].get_recent_logs()
        return []


# Global singleton
mcp_manager = McpManager()

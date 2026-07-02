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
from datetime import datetime, timezone
from typing import Any

from langchain_core.tools import BaseTool

logger = logging.getLogger("core.plugins.mcp_manager")

MCP_CONFIG_FILE = "mcp/servers.json"
MCP_PID_DIR = "mcp/pids"

# Total timeout for full tool discovery handshake (initialize + tools/list)
DISCOVERY_TIMEOUT_SECONDS = 30


class McpServerProcess:
    """Represents a running MCP server subprocess."""

    def __init__(self, name: str, config: dict[str, Any]) -> None:
        self.name = name
        self.config = config
        self.process: subprocess.Popen | None = None
        self.started_at: float | None = None
        self.last_error: str | None = None
        self._tools: list[BaseTool] = []
        self._tool_schemas: list[dict[str, Any]] = []
        self._msg_id = 0
        self._discovered_at: str | None = None

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    @property
    def uptime_seconds(self) -> float | None:
        if self.started_at and self.running:
            return time.time() - self.started_at
        return None

    def _next_id(self) -> int:
        self._msg_id += 1
        return self._msg_id

    def _send_jsonrpc(self, method: str, params: dict | None = None, timeout: float = 10.0) -> dict | None:
        """Send a JSON-RPC request and read the response (blocking, with timeout)."""
        if not self.process or not self.process.stdin or not self.process.stdout:
            return None
        msg = {"jsonrpc": "2.0", "method": method, "id": self._next_id()}
        if params:
            msg["params"] = params
        try:
            line = json.dumps(msg) + "\n"
            logger.debug(f"MCP '{self.name}' → {line.rstrip()}")
            self.process.stdin.write(line)
            self.process.stdin.flush()

            import select
            ready, _, _ = select.select([self.process.stdout], [], [], timeout)
            if not ready:
                err = f"timeout waiting for response to {method} after {timeout}s"
                logger.warning(f"MCP '{self.name}': {err}")
                return None

            resp_line = self.process.stdout.readline()
            if not resp_line:
                logger.warning(f"MCP '{self.name}': empty response to {method}")
                return None
            logger.debug(f"MCP '{self.name}' ← {resp_line.rstrip()}")
            return json.loads(resp_line.strip())
        except Exception as e:
            logger.debug(f"MCP '{self.name}' JSON-RPC error for {method}: {e}")
            return None

    def discover_tools(self) -> list[dict[str, Any]]:
        """Discover tools from the MCP server using JSON-RPC over stdio.

        Uses DISCOVERY_TIMEOUT_SECONDS total budget split between initialize
        and tools/list calls.  On any failure, stores the reason in
        self.last_error so callers can surface it.
        """
        half = DISCOVERY_TIMEOUT_SECONDS / 2

        # Step 1: initialize
        resp = self._send_jsonrpc("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "ava-backend", "version": "0.1.0"},
        }, timeout=half)

        if not resp:
            self.last_error = f"discovery timeout: no response to initialize after {half}s"
            logger.warning(f"MCP '{self.name}': {self.last_error}")
            return []

        if "error" in resp:
            err_msg = resp["error"].get("message", str(resp["error"]))
            self.last_error = f"JSON-RPC error on initialize: {err_msg}"
            logger.warning(
                f"MCP '{self.name}': initialize failed — "
                f"request={json.dumps({'method': 'initialize'})} "
                f"response={json.dumps(resp)}"
            )
            return []

        # Step 2: notifications/initialized (fire and forget)
        try:
            if self.process and self.process.stdin:
                notif = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
                self.process.stdin.write(notif)
                self.process.stdin.flush()
        except Exception:
            pass

        # Step 3: tools/list
        resp = self._send_jsonrpc("tools/list", {}, timeout=half)

        if not resp:
            self.last_error = f"discovery timeout: no response to tools/list after {half}s"
            logger.warning(f"MCP '{self.name}': {self.last_error}")
            return []

        if "error" in resp:
            err_msg = resp["error"].get("message", str(resp["error"]))
            self.last_error = f"JSON-RPC error on tools/list: {err_msg}"
            logger.warning(
                f"MCP '{self.name}': tools/list failed — "
                f"request={json.dumps({'method': 'tools/list'})} "
                f"response={json.dumps(resp)}"
            )
            return []

        tools = resp.get("result", {}).get("tools", [])
        self._tool_schemas = tools
        self._discovered_at = datetime.now(timezone.utc).isoformat()
        self.last_error = None  # clear any previous discovery error
        logger.info(f"MCP '{self.name}': discovered {len(tools)} tools")
        return tools

    def start(self, env_overrides: dict[str, str] | None = None) -> bool:
        """Start the MCP server subprocess."""
        if self.running:
            logger.debug(f"MCP server '{self.name}' already running.")
            return True

        cmd = [self.config.get("command", "npx")] + self.config.get("args", [])
        env = os.environ.copy()

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

            try:
                tools = self.discover_tools()
                self._build_langchain_tools(tools)
                if self.last_error:
                    logger.warning(
                        f"MCP '{self.name}': tool discovery error (server still running): "
                        f"{self.last_error}"
                    )
            except Exception as e:
                self.last_error = f"tool discovery exception: {e}"
                logger.warning(
                    f"MCP '{self.name}': tool discovery failed (server still running): {e}"
                )

            return True
        except FileNotFoundError:
            self.last_error = f"Command not found: {cmd[0]}"
            logger.error(f"MCP server '{self.name}' start failed: {self.last_error}")
            return False
        except Exception as e:
            self.last_error = str(e)
            logger.error(f"MCP server '{self.name}' start failed: {e}")
            return False

    def _build_langchain_tools(self, tool_schemas: list[dict[str, Any]]) -> None:
        """Convert MCP tool schemas to LangChain tools that call the server."""
        from langchain_core.tools import StructuredTool

        self._tools = []
        for schema in tool_schemas:
            tool_name = schema.get("name", "unknown")
            tool_desc = schema.get("description", "")

            server_ref = self

            def _make_invoke(tname: str):
                def invoke_tool(**kwargs: Any) -> str:
                    return server_ref._call_tool(tname, kwargs)
                return invoke_tool

            tool = StructuredTool.from_function(
                func=_make_invoke(tool_name),
                name=tool_name,
                description=tool_desc,
            )
            self._tools.append(tool)

    def _call_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Call a tool on the MCP server via JSON-RPC."""
        resp = self._send_jsonrpc("tools/call", {
            "name": tool_name,
            "arguments": arguments,
        })
        if not resp:
            return "Error: no response from MCP server"
        if "error" in resp:
            return f"Error: {resp['error'].get('message', 'unknown')}"
        result = resp.get("result", {})
        content = result.get("content", [])
        texts = [c.get("text", "") for c in content if c.get("type") == "text"]
        return "\n".join(texts) if texts else json.dumps(result)

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

    async def _resolve_secrets(
        self,
        server_config: dict[str, Any],
        session=None,
    ) -> dict[str, str]:
        """Resolve ${SECRET_NAME} references from the encrypted vault.

        If session is None, opens its own session.
        """
        try:
            from core.secrets.vault import SecretsVault
            from core.db.engine import async_session
            vault = SecretsVault()
            env_config = server_config.get("env", {})
            if session is not None:
                return await vault.get_env_for_mcp(session, env_config)
            async with async_session() as sess:
                return await vault.get_env_for_mcp(sess, env_config)
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
        # Recreate server process object so it picks up new config next start
        if name in self._servers and not self._servers[name].running:
            del self._servers[name]
        return True

    def unregister_server(self, name: str) -> bool:
        """Unregister and stop a server."""
        self.stop_server(name)
        if name in self._servers:
            del self._servers[name]
        if name in self._config.get("servers", {}):
            del self._config["servers"][name]
            self._save_config()
            return True
        return False

    async def start_server(self, name: str, session=None) -> bool:
        """Start a registered MCP server, resolving secrets via the vault."""
        server_config = self._config.get("servers", {}).get(name)
        if not server_config:
            logger.error(f"MCP server '{name}' not registered.")
            return False

        if name not in self._servers:
            self._servers[name] = McpServerProcess(name, server_config)

        env_vars = await self._resolve_secrets(server_config, session=session)

        # start() is blocking (subprocess) — run in executor to avoid blocking event loop
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self._servers[name].start, env_vars
        )

    def stop_server(self, name: str) -> None:
        """Stop a running MCP server."""
        if name in self._servers:
            self._servers[name].stop()

    def start_auto_start_servers(self) -> None:
        """Schedule auto-start servers to start (fire-and-forget, called at startup)."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
        for name, config in self._config.get("servers", {}).items():
            if config.get("auto_start") and config.get("enabled", True):
                loop.create_task(self.start_server(name))

    def stop_all(self) -> None:
        """Stop all running MCP servers."""
        for server in self._servers.values():
            if server.running:
                server.stop()

    def get_tools(self, server_name: str) -> list[BaseTool]:
        """Return LangChain tools exposed by a running MCP server."""
        if server_name not in self._servers or not self._servers[server_name].running:
            return []
        return self._servers[server_name]._tools

    def get_tool_schemas(self, server_name: str) -> list[dict[str, Any]]:
        """Return raw MCP tool schemas for a server."""
        if server_name not in self._servers:
            return []
        return self._servers[server_name]._tool_schemas

    def get_server_status(self, name: str) -> dict[str, Any]:
        """Get status info for a server.

        Returns: {name, status, tools_count, error, ...}
        """
        config = self._config.get("servers", {}).get(name, {})
        server = self._servers.get(name)

        status = "stopped"
        uptime = None
        last_error = None
        pid = None
        tools_count = 0

        if server:
            tools_count = len(server._tool_schemas)
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
            "error": last_error,
            "last_error": last_error,  # kept for backward compat
            "pid": pid,
            "tools_count": tools_count,
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

    def get_tool_discovery_info(self, name: str) -> dict[str, Any]:
        """Return tools list with error and discovered_at for API response."""
        server = self._servers.get(name)
        if not server:
            return {
                "tools": [],
                "error": "server not started",
                "discovered_at": None,
            }
        return {
            "tools": [
                {
                    "name": s.get("name", ""),
                    "description": s.get("description", ""),
                    "inputSchema": s.get("inputSchema", {}),
                }
                for s in server._tool_schemas
            ],
            "error": server.last_error,
            "discovered_at": server._discovered_at,
        }


# Global singleton
mcp_manager = McpManager()

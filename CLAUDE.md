# Ava Project — Claude Code Context

Full-stack local AI assistant. Backend in Python/FastAPI (this repo). Frontend in Next.js (Cursor + Gemini).

## Ports

| Service | Port |
|---------|------|
| Backend REST API | 8471 |
| WebSocket | 8472 |
| Frontend Next.js | 4731 |

## Architecture

**Independent components:**
- Backend plays TTS via system speakers (sounddevice).
- If no WebSocket client is connected, the backend skips all WS sends silently.

**Working directory:** Always `backend/` when running Python commands.

**Virtualenv:** `project-ava` (`workon project-ava` or `source ~/.virtualenvs/project-ava/bin/activate`)

## Folder Structure

```
backend/
├── main.py              # FastAPI app, lifespan startup/shutdown
├── cli.py               # CLI: workspace, knowledge, plugin, status commands
├── api/
│   ├── schemas.py       # All Pydantic request/response models
│   ├── ws.py            # WebSocket endpoint + WebSocketManager
│   ├── auth.py          # Google OAuth /auth/google/callback + /status
│   ├── config.py        # GET/PUT /api/config
│   ├── modes.py         # GET /api/modes/current, POST /api/modes/switch
│   ├── workspaces.py    # Full CRUD + /plugins + /tools endpoints
│   ├── chats.py         # List, search, get, context, delete sessions
│   ├── knowledge.py     # Upload, seed, search, sources, seed status
│   ├── plugins.py       # Install, uninstall, enable/disable, status
│   ├── mcp.py           # MCP server lifecycle endpoints
│   └── secrets.py       # Write-only secrets API (never returns full values)
├── core/
│   ├── config.py        # Settings(BaseSettings) singleton — reads .env
│   ├── startup.py       # Preflight checks: Ollama, disk, CUDA, ports, dirs
│   ├── state_machine.py # StateMachine: 4 modes, asyncio.Lock, WS broadcast
│   ├── workspace.py     # validate_name(), create/load/save/list workspace
│   ├── tray.py          # SystemTray (pystray) + HotkeyManager (keyboard)
│   ├── autonomous.py    # AutonomousAgent: proactive loop, TTS + OS notify
│   ├── audio/
│   │   ├── vad.py       # SileroVAD: speech start/end callbacks
│   │   └── capture.py   # MicrophoneCapture + LoopbackCapture (cross-platform)
│   ├── stt/
│   │   ├── whisper.py   # WhisperSTT: faster-whisper, CUDA→CPU fallback
│   │   ├── gate.py      # STTGate: always_on / on_demand / smart
│   │   └── priority.py  # TranscriptionPriority: LLM or heuristic classification
│   ├── tts/
│   │   ├── kokoro.py    # KokoroTTS: per-sentence synthesis, async
│   │   ├── xtts.py      # XTTSS: voice cloning alternative
│   │   └── playback.py  # LocalAudioPlayer: sounddevice, stop() for barge-in
│   ├── llm/
│   │   ├── provider.py  # get_llm(), stream_tokens() — Ollama/OpenAI/Anthropic
│   │   └── agent.py     # AvaAgent: LangGraph streaming + tool calling loop
│   ├── pipeline/
│   │   ├── voice_pipeline.py  # Full VAD→STT→LLM→SentenceBuffer→TTS→speakers
│   │   ├── sentence_buffer.py # SentenceBuffer + stream_sentences() async gen
│   │   └── concurrency.py     # PipelineController: barge-in, mode lock, TTS queue
│   ├── memory/
│   │   ├── chat_history.py    # ChatSession, ChatManager, CrossWorkspaceChatManager
│   │   ├── summarizer.py      # ConversationSummarizer: summary + last-N context
│   │   ├── migrations.py      # SQLite schema versioning (PRAGMA user_version)
│   │   └── long_term.py       # (placeholder — B7 RAG handles this via ChromaDB)
│   ├── knowledge/
│   │   ├── ingestion.py       # PDF/DOCX/TXT/URL/directory extraction + chunking
│   │   ├── rag.py             # KnowledgeBase: ChromaDB + embeddings + reranker
│   │   └── meeting.py         # MeetingRecorder: loopback + diarization + summary
│   ├── plugins/
│   │   ├── registry.py        # PluginRegistry: tool assembly per workspace
│   │   ├── mcp_manager.py     # McpManager: subprocess lifecycle, secret injection
│   │   └── plugin_loader.py   # Known presets, install/uninstall, manifest loading
│   └── secrets/
│       ├── crypto.py          # Fernet AES-256: encrypt/decrypt
│       └── vault.py           # SecretsVault: set/get/preview/delete, env injection
├── tools/
│   ├── web_search.py          # DuckDuckGo (no API key)
│   ├── system_control.py      # get_system_info, open_application, take_screenshot
│   ├── google_calendar.py     # get/create calendar events (requires Google OAuth)
│   ├── gmail.py               # get_recent_emails, send_email
│   └── chat_search.py         # search_chat_history (uses ChatManager)
├── workspaces/
│   └── personal/
│       ├── config.json        # workspace config (system_prompt, tools, plugins, etc.)
│       ├── chroma_db/         # per-workspace vector store
│       └── chat_history/      # *.json session files + sessions.db SQLite index
├── knowledge/
│   ├── meetings/              # *.md meeting transcripts
│   └── chroma_db/             # global shared vector store
├── secrets/
│   ├── master.key             # AES key (0600 perms, gitignored)
│   └── vault.enc              # encrypted secrets (gitignored)
└── tests/
    ├── conftest.py            # pytest fixtures: test_workspace, client
    ├── test_api_health.py     # 8 API smoke tests
    ├── test_secrets_vault.py  # 6 vault tests
    ├── test_stt_gate.py       # 14 STT gate tests
    ├── test_sentence_buffer.py # 20 buffer + stream tests
    ├── test_plugin_registry.py # 13 registry tests
    └── test_workspace_crud.py  # 16 chat history + CLI tests
```

## Key Design Decisions

### Secrets — never in .env
Plugin/MCP secrets go through `/api/secrets/set` → encrypted vault. The API only returns `{name, is_set, preview: "tdst_****7f2a"}`. `vault.get_env_for_mcp()` resolves `${SECRET_NAME}` refs at subprocess start time.

### Local audio output
The voice pipeline plays locally via `LocalAudioPlayer` (sounddevice) unconditionally. If no WebSocket client is connected, WS sends are silently skipped.

### Barge-in
`PipelineController.handle_barge_in()`: cancels the LLM asyncio task, calls `local_player.stop()`, flushes TTS queue, and broadcasts `tts_stop`.

### Mode transitions
`StateMachine.transition()` is protected by `asyncio.Lock`. It applies pipeline effects (start/stop VAD, capture, TTS) and broadcasts `mode_change` to all WebSocket clients.

### Chat history
Messages written immediately on `ChatSession.append()` (crash-safe). Atomic write via `.tmp` → rename. SQLite index (`sessions.db`) for fast listing/search. `CrossWorkspaceChatManager` for cross-workspace queries.

### Tool injection
`PluginRegistry.get_tools_for_workspace(config)` → built-in tools (lazy import) + MCP tools from `McpManager.get_tools()`. Assembled fresh per workspace config when the agent is initialized.

### STT pipeline
VAD detects speech → `STTGate.evaluate(audio)` decides whether to transcribe → `WhisperSTT.transcribe()` → `TranscriptionPriority.classify_and_process()` decides depth → LLM streaming → `SentenceBuffer` → TTS per sentence → `LocalAudioPlayer.play()` + optional WS `audio_chunk`.

## API Endpoints Summary

```
REST (port 8471):
  GET  /                         health root
  GET  /health                   detailed health
  GET  /docs                     Swagger UI
  GET  /api/config               full system config
  PUT  /api/config               update config
  GET  /api/modes/current        current mode + workspace
  POST /api/modes/switch         {mode: "companion"|"meeting"|"background"|"autonomous"}
  GET  /api/workspaces           list all
  POST /api/workspaces           create {name, system_prompt, stt_gate_mode}
  GET  /api/workspaces/{name}    get config
  PUT  /api/workspaces/{name}    partial update
  DELETE /api/workspaces/{name}?confirm=true
  POST /api/workspaces/{name}/plugins  {plugins_enabled: [...]}
  POST /api/workspaces/{name}/tools    {tools_enabled: [...]}
  GET  /api/chats                list sessions
  POST /api/chats/search         {query, workspace?, session_id?, limit}
  GET  /api/chats/{id}           full session with messages
  GET  /api/chats/{id}/context   last-N messages for LLM
  DELETE /api/chats/{id}
  GET  /api/knowledge/sources    list indexed sources
  POST /api/knowledge/upload     multipart file upload
  POST /api/knowledge/seed       {workspace, type, source}
  GET  /api/knowledge/seed/status
  POST /api/knowledge/search     {query, workspace, limit}
  DELETE /api/knowledge/source   ?source=...&workspace=...
  GET  /api/plugins              installed plugins
  GET  /api/plugins/available    known presets
  POST /api/plugins/install      {name} or {mcp_config}
  DELETE /api/plugins/{name}
  PUT  /api/plugins/{name}/config
  POST /api/plugins/{name}/enable   {workspace}
  POST /api/plugins/{name}/disable  {workspace}
  GET  /api/plugins/{name}/status
  GET  /api/mcp/servers          list + status
  POST /api/mcp/servers          register new server
  PUT  /api/mcp/servers/{name}
  DELETE /api/mcp/servers/{name}
  POST /api/mcp/servers/{name}/start
  POST /api/mcp/servers/{name}/stop
  GET  /api/mcp/servers/{name}/tools
  GET  /api/mcp/servers/{name}/health
  GET  /api/mcp/servers/{name}/logs
  POST /api/secrets/set          {name, value}  — write-only
  GET  /api/secrets              list previews
  GET  /api/secrets/{name}       {name, is_set, preview}  — never full value
  DELETE /api/secrets/{name}
  GET  /auth/google/status
  GET  /auth/google/callback     OAuth redirect handler

WebSocket (port 8472): ws://localhost:8472/ws?client_type=dashboard
  ← status          {type, status: "listening"|"thinking"|"speaking"}
  ← mode_change     {type, mode, workspace}
  ← text_delta      {type, delta}
  ← text_complete   {type, text}
  ← tts_stop        {type}
  ← error           {type, service, message}
  ← plugin_event    {type, plugin, event}
```

## Running Tests

```bash
cd backend
source ~/.virtualenvs/project-ava/bin/activate
pytest tests/ -v          # all 77 tests
pytest tests/test_stt_gate.py -v
pytest tests/test_sentence_buffer.py -v
```

**IMPORTANT — run tests safely.** `conftest.py` calls `drop_all()` at the start and end of every session. Always use `make test` (which sets `TEST_DATABASE_URL=ava_test` automatically) or export the variable explicitly before running `pytest` directly. Running bare `pytest` without `TEST_DATABASE_URL` set now defaults to `ava_test` (safe), but `make test` is still preferred. Never point `TEST_DATABASE_URL` at `ava` or any DB name that does not end with `_test` — conftest will abort with an error before any schema changes occur.

## Frontend Coordination

The frontend (Next.js, built in Cursor with Gemini) reads:
- `project-status.json` — which backend phases are done
- `openapi.json` — exact API contract (auto-exported at repo root)
- `frontend-requests.md` — frontend → backend requests

After completing backend changes, update `project-status.json` and re-export:
```bash
cd backend && python dump_openapi.py > ../openapi.json
```

## Code Conventions

- Python 3.11+, type hints everywhere, `from __future__ import annotations`
- Async functions for all I/O (FastAPI routes, pipeline, WS)
- Graceful degradation: if a library isn't installed, log a clear error and return a safe fallback — never crash the whole backend
- Global singletons (`pipeline_controller`, `state_machine`, `mcp_manager`, `plugin_registry`) initialized at module level, components injected after startup to avoid circular imports
- Secrets: never log, never return full values from API, never store in .env
- Tests: use `tmp_path` + `monkeypatch.chdir()` for isolation, never touch the real workspace

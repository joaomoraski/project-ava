# Ava — Local AI Assistant

Voice-first AI assistant that runs entirely on your machine. No cloud required with Ollama.

**Components / processes:**
- **Backend (Python/FastAPI)** — port 8471 (REST) / 8472 (WS) — voice pipeline, LLM, tools. Works standalone.
- **Worker (procrastinate)** — no port — runs background jobs: meeting summarization, embeddings, auto-categorization. **Required** for meetings/notes to be processed and become searchable.
- **PostgreSQL + pgvector** — port 5432 — canonical store + vector embeddings.
- **Ollama** — port 11434 — local LLM + embeddings (skip only if using a cloud provider).
- **Frontend (Next.js)** — port 4731 — dashboard, text chat, meetings, notes, todos, calendar.

> ⚠️ **The worker is easy to forget.** If you start only the backend, meetings will record but never get summarized or indexed, and `search_meetings` will return nothing. Always run the worker too (`make dev-worker`, or use `overmind`/`make dev` which start it for you).

---

## Documentation

In-depth docs live in [`docs/`](docs/):

- [`docs/architecture.md`](docs/architecture.md) — full system architecture, data model, data flows, design decisions, and a glossary of every concept/library. **Start here.**
- [`docs/rag-pipeline.md`](docs/rag-pipeline.md) — the recommended RAG pipeline for Ava (ingestion → chunking → hybrid retrieval → reranking → answer → evaluation).
- [`docs/rag-handbook.md`](docs/rag-handbook.md) — RAG from zero to advanced: every strategy, how to choose, RAG vs fine-tuning, evaluation. Study material.

## Features

- Voice interaction: VAD -> STT (Whisper) -> LLM -> TTS (Kokoro/XTTS/Piper) -> speakers
- Text chat with streaming responses via WebSocket
- Meeting transcription with speaker diarization and auto-summary
- Personal knowledge base with semantic search (RAG via PostgreSQL + pgvector)
- Multiple workspaces with isolated prompts, tools, and chat history
- MCP plugin system (Todoist, Notion, GitHub, Slack, Brave Search, and more)
- Built-in tools: web search, Google Calendar, Gmail, system control
- Encrypted secrets vault — secrets never touch `.env`
- Four operating modes: companion, meeting, background, autonomous
- TODOs, alerts/reminders with OS notifications, Google Calendar sync
- Notes with Markdown support

---

## Requirements

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| RAM | 16 GB | 32 GB |
| GPU | CPU only (slow) | NVIDIA 8 GB VRAM |
| Storage | 20 GB free | 40 GB+ |
| OS | Linux / macOS / Windows | Linux |

**Required software:**
- **Python 3.12** (see `backend/.python-version`) with `virtualenvwrapper` (or plain `venv`)
- **Node.js 20+** and **`pnpm`** (the frontend uses `pnpm-lock.yaml`; `corepack enable` gets you pnpm)
- **Docker + Docker Compose** — to run PostgreSQL + pgvector (recommended path)
- [**Ollama**](https://ollama.com/download) — local LLM + embeddings (skip only if using OpenAI/Anthropic/Google)
- **ffmpeg** — audio conversion for voice transcription
- **PortAudio** — audio I/O library (needed by `sounddevice`)
- (only if not using Docker) **PostgreSQL 15+** with the `pgvector` extension

### System dependencies (Linux — Debian/Ubuntu)

```bash
sudo apt install -y \
  libportaudio2 \
  ffmpeg \
  pulseaudio-utils \
  libnotify-bin
```

| Package | Why |
|---------|-----|
| `libportaudio2` | Audio capture/playback (sounddevice) |
| `ffmpeg` | Audio format conversion (webm → wav for STT) |
| `pulseaudio-utils` | Exposes PipeWire/PulseAudio monitor for meeting mode loopback capture (`pactl`) |
| `libnotify-bin` | Desktop notifications (`notify-send`) — optional but recommended |

### System dependencies (macOS)

```bash
brew install portaudio ffmpeg
brew install blackhole-2ch   # only for meeting mode loopback
```

### System dependencies (Windows)

- ffmpeg: download from https://ffmpeg.org and add to PATH
- PortAudio: bundled with `sounddevice` pip package
- Meeting loopback: works natively via WASAPI

---

## Cross-Platform Support

Ava runs on **Linux, macOS, and Windows**. No NVIDIA GPU is required — all components fall back to CPU automatically.

### Per-platform summary

| Component | Linux | macOS (Intel/Apple Silicon) | Windows |
|---|---|---|---|
| **STT (faster-whisper)** | CUDA or CPU | CPU (auto-fallback) | CUDA or CPU |
| **TTS (Kokoro ONNX)** | CPU | CPU | CPU |
| **VAD (SileroVAD)** | CPU (PyTorch) | CPU (PyTorch) | CPU (PyTorch) |
| **Audio playback** | sounddevice + PulseAudio/PipeWire | sounddevice + CoreAudio | sounddevice + WASAPI |
| **Meeting loopback** | PulseAudio/PipeWire monitor (auto-detected) | Needs [BlackHole](https://github.com/ExistentialAudio/BlackHole) | WASAPI loopback (auto-detected) |
| **Speaker diarization** | CUDA or CPU | CPU (slower) | CUDA or CPU |
| **OS notifications** | `notify-send` (native) | Falls back to WS broadcast | Falls back to WS broadcast |
| **LLM (Ollama)** | Full support | Full support | Full support |

### macOS setup notes

1. **Set `WHISPER_DEVICE=cpu`** in `backend/.env` (no CUDA on Mac).
2. **Whisper model**: Use `small` or `base` instead of `large-v3-turbo` for better performance on CPU:
   ```
   WHISPER_MODEL=small
   WHISPER_DEVICE=cpu
   ```
3. **Meeting mode loopback**: Install BlackHole to capture system audio:
   ```bash
   brew install blackhole-2ch
   ```
   Then set BlackHole as the audio output device during meetings. Without it, meeting mode only captures microphone audio (no remote participants).
4. **PortAudio**: `brew install portaudio`
5. **PostgreSQL**: `brew install postgresql@15` or use Docker.

### Windows setup notes

1. Set `WHISPER_DEVICE=cpu` unless you have an NVIDIA GPU with CUDA installed.
2. Meeting loopback works natively via WASAPI — no extra software needed.
3. OS notifications fall back to WebSocket broadcast (dashboard toasts).
4. PortAudio is bundled with the `sounddevice` pip package on Windows.

### GPU acceleration (optional, NVIDIA only)

CUDA speeds up STT (Whisper) and speaker diarization significantly. Without a GPU, everything runs on CPU (slower but functional).

**Setup:**

1. Install [NVIDIA drivers](https://www.nvidia.com/Download/index.aspx) for your GPU
2. Install [CUDA Toolkit 12.x](https://developer.nvidia.com/cuda-downloads)
3. Install [cuDNN](https://developer.nvidia.com/cudnn) (required by faster-whisper)
4. Install PyTorch with CUDA:
   ```bash
   pip install torch --index-url https://download.pytorch.org/whl/cu121
   ```
5. Set in `backend/.env`:
   ```
   WHISPER_DEVICE=cuda
   WHISPER_MODEL=large-v3-turbo   # larger models benefit most from GPU
   ```

If CUDA fails at runtime (driver mismatch, out of memory), Whisper automatically falls back to CPU.

**No NVIDIA GPU?** Leave `WHISPER_DEVICE=cpu` and use a smaller model (`small` or `base`):
```
WHISPER_DEVICE=cpu
WHISPER_MODEL=small
```

Apple Silicon (M1/M2/M3/M4) does **not** support CUDA. The code automatically detects this and uses CPU.

### LLM model recommendations

| Model | Size | Tool Calling | Notes |
|-------|------|-------------|-------|
| `qwen2.5:7b` | 4.4 GB | Excellent | Best local option for tool calling |
| `llama3.1:8b` | 4.7 GB | Good | Reliable tool support |
| `mistral:7b` | 4.1 GB | Good | Fast, good quality |
| `llama3.2` | 2.0 GB | Poor | Too small for reliable tool calling |
| `gpt-4o-mini` | Cloud | Excellent | OpenAI, requires API key |
| `claude-sonnet-4-6` | Cloud | Excellent | Anthropic, requires API key |

Models smaller than 7B will output raw JSON instead of executing tools. Use 7B+ for voice assistant features.

---

## Quick Start

Fresh-machine setup, in order. Every step is required unless marked optional.

### 0. Install prerequisites

Install the software from [Requirements](#requirements) first: Python 3.12, Node 20+ / pnpm, Docker, Ollama, ffmpeg, PortAudio, and the OS audio packages for your platform (see [System dependencies](#system-dependencies-linux--debianubuntu)).

```bash
corepack enable            # provides pnpm if you don't have it
ollama --version           # verify Ollama is installed and `ollama serve` is running
docker compose version     # verify Docker Compose is available
```

### 1. Clone

```bash
git clone <repo-url> ava
cd ava
```

### 2. Configure environment

```bash
make setup-env             # copies backend/.env.example -> backend/.env
# Edit backend/.env — at minimum set LLM_PROVIDER, and WHISPER_DEVICE=cpu if you have no NVIDIA GPU
```

### 3. Install dependencies

```bash
make install               # backend (creates the `project-ava` virtualenv + pip) AND frontend (pnpm)
# or individually:
#   make install-backend
#   make install-frontend
```

Manual backend install (if you don't use the Makefile):

```bash
python3 -m venv ~/.virtualenvs/project-ava      # or: mkvirtualenv project-ava
source ~/.virtualenvs/project-ava/bin/activate
pip install -r backend/requirements.txt
```

### 4. Start PostgreSQL (pgvector)

```bash
make db                    # docker compose up -d postgres + wait until ready
```

Or point `DATABASE_URL` in `backend/.env` at an existing PostgreSQL 15+ instance that has the `pgvector` extension installed.

### 5. Run database migrations

```bash
make db-migrate            # alembic upgrade head — creates all tables + the procrastinate queue
```

### 6. Pull models (Ollama)

```bash
ollama pull qwen2.5:7b         # LLM — 7B+ required for reliable tool calling
ollama pull nomic-embed-text   # embeddings for RAG / meeting search
```

(Skip this step only if `LLM_PROVIDER` is `openai`/`anthropic`/`google` — but `nomic-embed-text` is still needed for embeddings unless you change `EMBED_MODEL`.)

### 7. Run everything

The recommended way starts **all four processes** (backend + worker + frontend, on top of the DB) with unified logs:

```bash
# Recommended — needs overmind (https://github.com/DarthSim/overmind) or foreman/honcho
overmind start -f Procfile.dev
# or, with the Makefile (runs db + backend + worker + frontend in parallel):
make dev
```

Or start each process in its own terminal:

```bash
make dev-backend    # FastAPI  — REST :8471 / WS :8472   (Swagger: http://localhost:8471/docs)
make dev-worker     # procrastinate worker — background jobs (REQUIRED for meetings/notes)
make dev-frontend   # Next.js dashboard — http://localhost:4731
```

> **Do not skip `make dev-worker`.** Without it, meetings record but are never summarized or indexed, and searching meetings/notes returns nothing.

### Which processes must run?

| Process | Command | Required? | Purpose |
|---|---|---|---|
| PostgreSQL | `make db` | ✅ always | data + vector store |
| Ollama | `ollama serve` | ✅ (local mode) | LLM + embeddings |
| Backend | `make dev-backend` | ✅ always | REST/WS API + voice pipeline |
| Worker | `make dev-worker` | ✅ for meetings/notes | background jobs (summaries, embeddings) |
| Frontend | `make dev-frontend` | optional | dashboard UI |

---

## Architecture

```
Backend (FastAPI :8471)
  -> REST API (config, workspaces, chats, knowledge, plugins, secrets, meetings, todos, alerts, calendar)
  -> WebSocket (:8471/ws) — streaming deltas, status, transcript chunks, alerts

Database: PostgreSQL + pgvector (chat history, app state, knowledge embeddings, meetings, todos, alerts)

Voice pipeline: VAD -> STT -> LLM (LangGraph agent) -> SentenceBuffer -> TTS -> speakers
```

---

## Modes

| Mode | Listening | TTS | Loopback | Description |
|------|-----------|-----|----------|-------------|
| **Companion** | VAD | Yes | No | Interactive voice assistant (default) |
| **Meeting** | VAD | No | Yes | Live transcription + diarization |
| **Background** | No | No | No | Silent — alerts and sync continue |

Switch via dashboard, system tray, or API:

```bash
curl -X POST http://localhost:8471/api/modes/switch \
  -H "Content-Type: application/json" \
  -d '{"mode": "meeting"}'
```

---

## API Overview

Full interactive docs at http://localhost:8471/docs.

| Group | Endpoints |
|-------|-----------|
| Health | `GET /`, `GET /health` |
| Config | `GET/PUT /api/config` |
| Modes | `GET /api/modes/current`, `POST /api/modes/switch` |
| Workspaces | CRUD `/api/workspaces/{name}` + `/plugins` + `/tools` |
| Chats | List, search, get, context, delete — `/api/chats` |
| Meetings | CRUD + stop, prep, decisions, related — `/api/meetings` |
| Knowledge | Upload, seed, search, sources, chunks — `/api/knowledge` |
| TODOs | CRUD — `/api/todos` |
| Alerts | CRUD + dismiss — `/api/alerts` |
| Calendar | Events, sync — `/api/calendar` |
| Notes | CRUD — `/api/notes` |
| Action Items | CRUD — `/api/action-items` |
| Plugins/MCP | Install, enable/disable, MCP lifecycle — `/api/plugins`, `/api/mcp` |
| Secrets | Write-only vault — `/api/secrets` |
| Auth | Google OAuth — `/auth/google` |

**WebSocket** (`ws://localhost:8471/ws`):

Events: `status`, `mode_change`, `text_delta`, `text_complete`, `tts_stop`, `error`, `plugin_event`, `meeting_started`, `meeting_stopped`, `transcript_chunk`, `alert_fired`

---

## Workspaces

Separate contexts for different use cases (personal, work, project-x), each with its own system prompt, chat history, knowledge base, and enabled tools.

```bash
cd backend && workon project-ava

python cli.py workspace list
python cli.py workspace create work
python cli.py workspace stt-gate work always_on   # always_on | on_demand | smart
python cli.py knowledge seed --workspace work --url https://docs.example.com
python cli.py knowledge seed --workspace work --dir ~/projects/docs/
```

---

## TTS (Text-to-Speech)

Ava uses **Kokoro ONNX** by default — runs on CPU, no downloads needed. 54 built-in voices available.

### Changing the voice

Edit `backend/.env`:

```bash
TTS_ENGINE=kokoro
TTS_VOICE=af_heart       # default female voice
TTS_LANGUAGE=auto         # auto | pt | en | ja | ...
```

**Available voices:** See the [Kokoro voices list](https://huggingface.co/hexgrad/Kokoro-82M/blob/main/voices.json). Examples: `af_heart`, `af_bella`, `am_adam`, `bf_emma`.

### Voice cloning (XTTS)

XTTS can clone any voice from a short audio sample. It needs a clean WAV recording of the target voice.

**Step 1 — Prepare a voice sample:**

You need a WAV file with 6-15 seconds of clear speech. You can:

- **Record yourself:** Use any recorder, export as WAV (16kHz mono is ideal).
- **Extract from a video/audio:** Use `ffmpeg` to clip and convert:
  ```bash
  # Extract 10 seconds starting at 00:30, convert to 16kHz mono WAV
  ffmpeg -i source_audio.mp3 -ss 00:00:30 -t 10 -ar 16000 -ac 1 backend/voices/my_voice.wav
  ```
- **Use an existing WAV:** Any clean speech sample works. Avoid background music or multiple speakers.

Tips for a good sample:
- Single speaker only, no background noise or music
- Normal speaking pace (not whispering, not shouting)
- 6-15 seconds is the sweet spot — longer is not better
- WAV format preferred (MP3/OGG also work but may lose quality)

**Step 2 — Save and configure:**

```bash
mkdir -p backend/voices
cp /path/to/sample.wav backend/voices/my_voice.wav
```

Edit `backend/.env`:

```bash
TTS_ENGINE=xtts
XTTS_VOICE_SAMPLE=voices/my_voice.wav
```

**Step 3 — Restart the backend.**

XTTS downloads its model (~1.8 GB) on first run. After that, Ava speaks with the cloned voice.

> **Note:** XTTS is slower than Kokoro (~2-3x) and uses more RAM. If latency matters, stick with Kokoro.

---

## MCP Plugins

Add MCP (Model Context Protocol) servers to give Ava access to external tools and services. Secrets are encrypted — never stored in `.env`.

**Built-in presets:** context7, todoist, notion, github, slack, linear, brave-search, filesystem, postgres, spotify

### Install via Dashboard

1. Go to **Plugins** page in the dashboard
2. Click **Custom MCP** tab
3. Fill in the server details (name, command, args, env)
4. Click **Add Server**
5. Start the server with the play button

### Install via API

**Step 1 — Store secrets (if the server needs API keys):**

```bash
curl -X POST http://localhost:8471/api/secrets/set \
  -H "Content-Type: application/json" \
  -d '{"name": "GITHUB_TOKEN", "value": "ghp_xxxxxxxxxxxx"}'
```

**Step 2 — Register the MCP server:**

```bash
# npx-based server (most common)
curl -X POST http://localhost:8471/api/mcp/servers \
  -H "Content-Type: application/json" \
  -d '{
    "name": "github",
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-github"],
    "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "${GITHUB_TOKEN}"}
  }'
```

The `${GITHUB_TOKEN}` syntax references secrets from the vault — they're injected at server start time.

**Step 3 — Start the server:**

```bash
curl -X POST http://localhost:8471/api/mcp/servers/github/start
```

**Step 4 — Enable in workspace:**

```bash
curl -X POST http://localhost:8471/api/workspaces/personal/plugins \
  -H "Content-Type: application/json" \
  -d '{"plugins_enabled": ["github"]}'
```

### Common MCP servers

| Server | Install command | Required secret |
|--------|----------------|-----------------|
| GitHub | `npx -y @modelcontextprotocol/server-github` | `GITHUB_PERSONAL_ACCESS_TOKEN` |
| Notion | `npx -y @notionhq/notion-mcp-server` | `NOTION_API_KEY` |
| Slack | `npx -y @anthropic/slack-mcp-server` | `SLACK_BOT_TOKEN` |
| Brave Search | `npx -y @anthropic/brave-search-mcp-server` | `BRAVE_API_KEY` |
| Filesystem | `npx -y @modelcontextprotocol/server-filesystem /path/to/dir` | — |
| Context7 | `npx -y @upstash/context7-mcp` | — |

### Check server status

```bash
# List all servers
curl http://localhost:8471/api/mcp/servers

# Health check
curl http://localhost:8471/api/mcp/servers/github/health

# View available tools
curl http://localhost:8471/api/mcp/servers/github/tools

# View logs
curl http://localhost:8471/api/mcp/servers/github/logs
```

---

## Environment Variables

All settings live in `backend/.env`. Copy from `backend/.env.example`.

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_PROVIDER` | `ollama` | `ollama` / `openai` / `anthropic` / `google` |
| `LLM_MODEL` | `qwen2.5:7b` | Model name for the selected provider (7B+ for tool calling) |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server URL |
| `OPENAI_API_KEY` | — | Required if `LLM_PROVIDER=openai` |
| `ANTHROPIC_API_KEY` | — | Required if `LLM_PROVIDER=anthropic` |
| `GOOGLE_API_KEY` | — | Required if `LLM_PROVIDER=google` |
| `EMBED_MODEL` | `nomic-embed-text` | Embedding model (Ollama or sentence-transformers) |
| `WHISPER_MODEL` | `large-v3-turbo` | `tiny` / `base` / `small` / `medium` / `large-v3-turbo` |
| `WHISPER_DEVICE` | `cuda` | `cuda` or `cpu` |
| `TTS_ENGINE` | `kokoro` | `kokoro` / `xtts` / `piper` |
| `TTS_VOICE` | `af_heart` | Voice name for selected engine |
| `TTS_LANGUAGE` | `pt` | Language code |
| `DATABASE_URL` | `postgresql+asyncpg://ava:ava_dev_password@localhost:5432/ava` | PostgreSQL connection |
| `DEFAULT_MODE` | `companion` | Starting mode |
| `DEFAULT_WORKSPACE` | `personal` | Starting workspace |
| `HF_TOKEN` | — | HuggingFace token for speaker diarization |
| `GOOGLE_CLIENT_ID` | — | Google OAuth (Calendar + Gmail) |
| `GOOGLE_CLIENT_SECRET` | — | Google OAuth |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |

---

## Testing

Always run tests via `make test` or with `TEST_DATABASE_URL` explicitly set to a `_test`-suffixed database. The test suite calls `drop_all()` before and after every run — pointing it at the wrong database will wipe your data.

```bash
# Safe — always use this:
make test

# Or manually with the env var set:
cd backend && workon project-ava
TEST_DATABASE_URL=postgresql+asyncpg://ava:ava_dev_password@localhost:5432/ava_test \
  pytest tests/ -v

# Single test files:
TEST_DATABASE_URL=postgresql+asyncpg://ava:ava_dev_password@localhost:5432/ava_test \
  pytest tests/test_stt_gate.py -v
```

`make test` automatically creates the `ava_test` database if it does not exist and sets `TEST_DATABASE_URL` to point at it. The guard in `conftest.py` will abort pytest with a clear error if `TEST_DATABASE_URL` is missing the `_test` suffix.

---

## Project Structure

```
backend/
├── main.py              # FastAPI app + lifespan startup
├── cli.py               # CLI: workspace, knowledge, plugin, status
├── api/                 # Route handlers + Pydantic schemas
├── core/
│   ├── config.py        # Settings singleton (pydantic-settings)
│   ├── state_machine.py # 4 modes, asyncio.Lock, WS broadcast
│   ├── audio/           # VAD + mic/loopback capture
│   ├── stt/             # Whisper + STT gate
│   ├── tts/             # Kokoro / XTTS / Piper + playback
│   ├── llm/             # LangGraph agent + multi-provider support
│   ├── pipeline/        # Voice pipeline + barge-in concurrency
│   ├── memory/          # Chat history + conversation summarizer (PostgreSQL)
│   ├── knowledge/       # RAG ingestion + pgvector KB + meeting recorder
│   ├── jobs/            # procrastinate app + tasks (meeting docs, embeddings, categorize)
│   ├── db/              # SQLAlchemy models + async engine + embeddings
│   ├── plugins/         # MCP manager + plugin registry
│   └── secrets/         # AES-256 vault
├── tools/               # Built-in LangChain tools
├── alembic/             # Database migrations (001 -> 009)
├── worker.py            # procrastinate background worker entry point
├── workspaces/          # Per-workspace config + chat history
└── tests/               # pytest suite
```

---

## Troubleshooting

**Ollama not found:** Run `ollama serve` then `ollama pull llama3.2`.

**PortAudio error (Linux):** `sudo apt install libportaudio2 libportaudio2-dev`

**PortAudio error (macOS):** `brew install portaudio`

**CUDA not available / libcublas.so.12 not found:** Set `WHISPER_DEVICE=cpu` in `.env`. The backend auto-falls back to CPU, but setting it explicitly avoids the warning. For CUDA support: https://pytorch.org/get-started/locally/

**STT too slow on CPU:** Switch to a smaller model: `WHISPER_MODEL=small` or `WHISPER_MODEL=base` in `.env`.

**No loopback for meeting mode (macOS):** `brew install blackhole-2ch` then set BlackHole as system audio output during meetings.

**Microphone device error (PaErrorCode -9985):** Usually happens after switching modes quickly. The backend has retry logic with exponential backoff. If persistent, check your mic device name in `.env` (`MIC_DEVICE=`).

**Hotkeys not working (Linux):** `sudo usermod -aG input $USER` then log out and back in.

**Database connection failed:** `make db` to start PostgreSQL via Docker, then `make db-migrate`.

**Meetings/notes never get summarized or don't show up in search:** the **worker is not running**. Start it with `make dev-worker` (or use `overmind start -f Procfile.dev` / `make dev`, which include it). Background summarization, embeddings, and auto-categorization all run in the worker process, not the backend.

**Disk space:** Whisper large-v3-turbo (~800 MB) + Llama 3.2 3B (~2 GB) + nomic-embed-text (~270 MB). Keep 20 GB+ free.

**Chat agent doesn't use tools:** Check the logs for "React agent created with N tools". If `0 tools`, verify your workspace has `tools_enabled` populated (dashboard -> Workspaces -> edit). The LLM model also matters — `llama3.2` (3B) has limited tool calling. For better results: use `llama3.1:8b`, `qwen2.5:7b`, or a cloud provider (OpenAI/Anthropic/Google).

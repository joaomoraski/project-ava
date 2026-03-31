# Ava Project

Local AI assistant — 100% free, no cloud, no API keys required.

Voice interaction (VAD → STT → LLM → TTS), personal knowledge base, productivity tools, workspaces, and an optional 3D avatar overlay.

**Three independent components:**
- **Backend (Python/FastAPI)** — port 8471 — the brain. Works standalone (voice-only).
- **Frontend (Next.js)** — port 4731 — configuration dashboard. Works without the avatar.
- **Avatar (Electron)** — optional 3D overlay with lip sync. Everything works without it.

---

## Requirements

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| RAM | 16 GB | 32 GB |
| GPU | CPU only (slow) | NVIDIA 8 GB VRAM |
| Storage | 20 GB free | 40 GB+ |
| OS | Linux / macOS / Windows | Linux |

**Required software:**
- [Ollama](https://ollama.com/download) — local LLM server
- Python 3.11+ with `virtualenvwrapper`
- Node.js 20+ and pnpm
- PortAudio (Linux: `sudo apt install libportaudio2`)

---

## Quick Start

### 1. Clone and configure

```bash
git clone <repo-url> ava-project
cd ava-project/backend
cp .env.example .env
# Edit .env — at minimum set LLM_PROVIDER=ollama
```

### 2. Pull models (Ollama)

```bash
ollama pull llama3.2           # LLM
ollama pull nomic-embed-text   # embeddings (for knowledge base)
```

### 3. Backend setup

```bash
cd backend
mkvirtualenv project-ava
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8471
```

Swagger UI: http://localhost:8471/docs

### 4. Frontend setup (optional — for dashboard)

```bash
cd frontend
pnpm install
cp .env.local.example .env.local   # API_BASE=http://localhost:8471
pnpm dev -- --port 4731
```

Dashboard: http://localhost:4731

### 5. Voice-only mode (no frontend needed)

Just run the backend. It plays TTS directly through your system speakers. Speak — it listens.

---

## Configuration (.env)

All config lives in `backend/.env`. Copy from `backend/.env.example`.

### LLM

```env
LLM_PROVIDER=ollama          # ollama | openai | anthropic
LLM_MODEL=llama3.2
OLLAMA_BASE_URL=http://localhost:11434

# Optional cloud providers
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
```

### STT (speech-to-text)

```env
WHISPER_MODEL=large-v3-turbo   # tiny | base | small | medium | large-v3-turbo
WHISPER_DEVICE=cuda            # cuda | cpu
```

### TTS (text-to-speech)

```env
TTS_ENGINE=kokoro              # kokoro | xtts
TTS_VOICE=af_heart             # Kokoro voice name
TTS_LANGUAGE=pt                # pt | en
# For voice cloning (XTTS only):
XTTS_VOICE_SAMPLE=voices/character.wav
```

### Google (optional — Calendar + Gmail)

```bash
# 1. Create OAuth credentials at console.cloud.google.com
# 2. Set redirect URI: http://localhost:8471/auth/google/callback
```
```env
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
```

### Meeting mode diarization (optional)

```bash
# 1. Create free token: https://huggingface.co/settings/tokens
# 2. Accept terms: https://huggingface.co/pyannote/speaker-diarization-3.1
```
```env
HF_TOKEN=
```

### Hotkeys

```env
MEETING_HOTKEY=ctrl+shift+m
TOGGLE_WORKSPACE_HOTKEY=ctrl+shift+w
```

---

## Modes

Switch modes via voice command, hotkey, system tray, or dashboard.

| Mode | VAD | TTS | Loopback | Avatar |
|------|-----|-----|----------|--------|
| **Companion** (default) | ✓ | ✓ | — | visible |
| **Meeting** | ✓ | — | ✓ | hidden |
| **Background** | — | — | — | hidden |
| **Autonomous** | — | ✓ | — | visible |

---

## Workspaces

Separate contexts (personal, work, project-x) with their own:
- System prompt (defines assistant personality)
- Chat history
- Knowledge base (ChromaDB collections)
- Enabled tools and plugins
- STT gate mode

```bash
# CLI
python cli.py workspace list
python cli.py workspace create work
python cli.py workspace stt-gate work always_on
python cli.py workspace show personal

# Add knowledge seeds
python cli.py knowledge seed --workspace work --url https://docs.company.com/api
python cli.py knowledge seed --workspace work --dir ~/work/docs/
```

---

## Plugins & MCP Servers

Install via dashboard or CLI. Secrets are stored encrypted — never in `.env`.

```bash
# CLI
python cli.py plugin list
python cli.py plugin enable todoist --workspace work
python cli.py plugin disable slack --workspace personal
```

**Known presets:** context7, todoist, notion, github, slack, linear, brave-search, filesystem, postgres, spotify

MCP server secrets are configured in the dashboard (Settings → Plugins → Configure). They are encrypted with AES-256 and stored in `backend/secrets/vault.enc`.

---

## Meeting Mode

1. Switch to meeting mode: `ctrl+shift+m` (or voice: "enter meeting mode")
2. Your mic is captured as "You"
3. System audio (Meet/Teams/Zoom) is captured via loopback
4. On exit: transcript + auto-summary saved to `~/meetings/YYYY-MM-DD_title.md`

**Loopback setup:**
- **Linux:** PulseAudio/PipeWire monitor source is detected automatically
- **macOS:** Install [BlackHole](https://existential.audio/blackhole/) (`brew install blackhole-2ch`)
- **Windows:** WASAPI loopback is detected automatically

---

## Knowledge Base

Index documents, URLs, and directories for semantic search.

```bash
# Via CLI
python cli.py knowledge seed --workspace personal --url https://example.com/docs
python cli.py knowledge seed --workspace work --dir ~/projects/myapp/docs/

# Via API
curl -X POST http://localhost:8471/api/knowledge/seed \
  -H "Content-Type: application/json" \
  -d '{"workspace": "work", "type": "url", "source": "https://docs.example.com"}'
```

Supported file types: PDF, DOCX, TXT, Markdown, RST

---

## Ports

| Service | Port | URL |
|---------|------|-----|
| Backend REST API | 8471 | http://localhost:8471 |
| Swagger UI | 8471 | http://localhost:8471/docs |
| WebSocket | 8472 | ws://localhost:8472/ws |
| Frontend Dashboard | 4731 | http://localhost:4731 |

---

## Troubleshooting

**Ollama not found on startup:**
```bash
ollama serve   # start Ollama server
ollama pull llama3.2
```

**No audio (PortAudio error on Linux):**
```bash
sudo apt install libportaudio2 libportaudio2-dev
```

**CUDA not available — Whisper using CPU:**
Install PyTorch with CUDA: https://pytorch.org/get-started/locally/
Or set `WHISPER_DEVICE=cpu` in `.env` to silence the warning.

**Meeting mode: no loopback device found (macOS):**
```bash
brew install blackhole-2ch
# Set BlackHole 2ch as default audio output in System Preferences
```

**Low disk space warning:**
Models need ~20 GB. Whisper large-v3-turbo (~800 MB) + Llama 3.2 3B (~2 GB) + nomic-embed-text (~270 MB).

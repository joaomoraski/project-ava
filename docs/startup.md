# Startup — running Ava

Two scenarios: **first-time setup on a fresh machine**, and **running when everything is already installed**. For the deep "what/why" of each prerequisite, see the [README](../README.md#requirements).

---

## TL;DR — everyday run (already set up)

Four processes. Easiest is one command with unified logs:

```bash
cd /home/moraski/personal/project-ava
overmind start -f Procfile.dev        # starts backend + worker + frontend (db must be up)
# make sure Postgres + Ollama are running first (see below)
```

Or the Makefile (also starts the DB):

```bash
make dev            # db + backend + worker + frontend, in parallel
```

Or each process in its own terminal:

```bash
make db             # 1. PostgreSQL (Docker)
ollama serve        # 2. Ollama (if not already running as a service)
make dev-backend    # 3. API on :8471 / WS :8472
make dev-worker     # 4. background worker  ← REQUIRED for meetings/notes to process
make dev-frontend   # 5. dashboard on :4731 (optional)
```

Health check: `curl localhost:8471/health` · Swagger: http://localhost:8471/docs · Dashboard: http://localhost:4731

> **The #1 gotcha:** if meetings record but never get summarized or found in search, the **worker isn't running**. Start `make dev-worker`.

---

## First-time setup (fresh machine)

Do these in order. Everything except system packages and models is one `make` call.

### 1. System prerequisites

Install these first (see [README System dependencies](../README.md#system-dependencies-linux--debianubuntu) for per-OS commands):

- **Docker + Docker Compose** — runs PostgreSQL + pgvector
- **Python 3.12** (`backend/.python-version`) + `virtualenvwrapper` or `venv`
- **Node 20+ + pnpm** (`corepack enable`)
- **Ollama** — https://ollama.com/download
- **ffmpeg** + **PortAudio** (audio)

Quick check:
```bash
docker compose version && python3 --version && node -v && pnpm -v && ollama --version && ffmpeg -version | head -1
```

### 2. Configure environment

```bash
cd /home/moraski/personal/project-ava
make setup-env      # creates backend/.env from .env.example
```
Edit `backend/.env` — at minimum:
- `LLM_PROVIDER=ollama` (or a cloud provider + its API key)
- `WHISPER_DEVICE=cpu` if you have **no** NVIDIA GPU (and `WHISPER_MODEL=small` for speed)

### 3. Install dependencies

```bash
make install        # backend venv (project-ava) + pip, and frontend pnpm install
```

### 4. Start the database

```bash
make db             # docker compose up -d postgres, waits until ready
```
(Or point `DATABASE_URL` in `backend/.env` at an existing PostgreSQL 15+ with the `pgvector` extension.)

### 5. Create the schema

```bash
make db-migrate     # alembic upgrade head — creates all tables + the procrastinate queue
```

### 6. Pull local models

```bash
ollama pull qwen2.5:7b          # LLM (7B+ needed for tool calling)
ollama pull nomic-embed-text    # embeddings for RAG / search
```
(Skip only if using a cloud LLM — but embeddings still use Ollama unless you change `EMBED_MODEL`.)

### 7. Run everything

```bash
make dev            # db + backend + worker + frontend
# or: overmind start -f Procfile.dev
```

### 8. Verify it works

```bash
curl -s localhost:8471/health          # -> ok
open http://localhost:8471/docs        # Swagger UI
open http://localhost:4731             # dashboard
```

---

## Running in an already-existing environment

If the venv, DB, and models already exist on this machine (e.g. you set it up before):

```bash
cd /home/moraski/personal/project-ava
make db             # ensure Postgres is up (no-op if already running)
make db-migrate     # apply any new migrations (safe to run repeatedly)
make dev            # start backend + worker + frontend
```

If you pulled new code, also refresh deps:
```bash
make install-backend   # picks up requirements.txt changes
cd frontend && pnpm install && cd ..
```

If you switched machines or the DB is remote, just set `DATABASE_URL` and `OLLAMA_BASE_URL` in `backend/.env` and skip `make db`.

---

## Running the tests (safe)

```bash
make test           # 223 tests; auto-creates the ava_test DB, never touches ava
```

> Never run bare `pytest` without `TEST_DATABASE_URL` pointing at a `*_test` database — `conftest.py` calls `drop_all()`. `make test` handles this for you.

---

## Which process does what

| Process | Command | Port | Needed for |
|---|---|---|---|
| PostgreSQL + pgvector | `make db` | 5432 | everything (data + vectors) |
| Ollama | `ollama serve` | 11434 | local LLM + embeddings |
| Backend (API + WS) | `make dev-backend` | 8471 / 8472 | all requests, voice pipeline |
| Worker (procrastinate) | `make dev-worker` | — | meeting summaries, embeddings, auto-categorize |
| Frontend (Next.js) | `make dev-frontend` | 4731 | dashboard UI (optional) |

---

## Troubleshooting quick hits

- **Meetings/notes never processed or unsearchable** → worker not running (`make dev-worker`).
- **`libcublas.so.12` / CUDA errors** → set `WHISPER_DEVICE=cpu` in `.env`.
- **DB connection refused** → `make db`, then `make db-migrate`.
- **Agent ignores tools** → check logs for "React agent created with N tools"; use a 7B+ model.
- **PortAudio error (Linux)** → `sudo apt install libportaudio2`.

Full troubleshooting: [README](../README.md#troubleshooting).

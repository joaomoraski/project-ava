VENV        := project-ava
VENV_PYTHON := $(HOME)/.virtualenvs/$(VENV)/bin/python
VENV_PIP    := $(HOME)/.virtualenvs/$(VENV)/bin/pip
VENV_PYTEST := $(HOME)/.virtualenvs/$(VENV)/bin/pytest
BACKEND_DIR := backend
FRONTEND_DIR := frontend

.PHONY: help install install-backend install-frontend \
        dev dev-backend dev-worker dev-frontend \
        db db-up db-down db-migrate db-reset \
        db-test-up test test-backend lint \
        export-openapi \
        setup-env venv

help:
	@echo "Ava Project — available targets:"
	@echo ""
	@echo "  Setup"
	@echo "    make install          Install all dependencies (backend + frontend)"
	@echo "    make install-backend  Install Python dependencies only"
	@echo "    make install-frontend Install Next.js dependencies only"
	@echo "    make setup-env        Copy .env.example to backend/.env (if missing)"
	@echo ""
	@echo "  Database"
	@echo "    make db               Start PostgreSQL (docker compose up)"
	@echo "    make db-up            Same as db"
	@echo "    make db-down          Stop PostgreSQL"
	@echo "    make db-migrate       Run Alembic migrations"
	@echo "    make db-reset         Drop and recreate database (destructive)"
	@echo "    make db-test-up       Ensure ava_test DB exists (safe, never touches ava)"
	@echo ""
	@echo "  Run"
	@echo "    make dev              Start db + backend + worker + frontend"
	@echo "    make dev-backend      Start FastAPI backend only (ports 8471/8472)"
	@echo "    make dev-worker       Start procrastinate background worker (meetings/notes jobs)"
	@echo "    make dev-frontend     Start Next.js frontend only (port 4731)"
	@echo ""
	@echo "  Test"
	@echo "    make test             Run all backend tests"
	@echo "    make test-backend     Same as test"
	@echo ""
	@echo "  Utils"
	@echo "    make export-openapi   Regenerate openapi.json from live backend"
	@echo "    make lint             Run ruff on backend/"

# ── Setup ────────────────────────────────────────────────────────────────────

venv:
	@if [ ! -d "$(HOME)/.virtualenvs/$(VENV)" ]; then \
		echo "Creating virtualenv $(VENV)..."; \
		python3 -m virtualenv $(HOME)/.virtualenvs/$(VENV); \
	else \
		echo "Virtualenv $(VENV) already exists."; \
	fi

install-backend: venv
	@echo "Installing backend dependencies..."
	$(VENV_PIP) install --quiet -r $(BACKEND_DIR)/requirements.txt

install-frontend:
	@echo "Installing frontend dependencies..."
	cd $(FRONTEND_DIR) && pnpm install

install: install-backend install-frontend
	@echo "All dependencies installed."

setup-env:
	@if [ ! -f "$(BACKEND_DIR)/.env" ]; then \
		if [ -f "$(BACKEND_DIR)/.env.example" ]; then \
			cp $(BACKEND_DIR)/.env.example $(BACKEND_DIR)/.env; \
			echo "Created backend/.env from .env.example — edit it before running."; \
		else \
			echo "No .env.example found. Create backend/.env manually."; \
		fi \
	else \
		echo "backend/.env already exists."; \
	fi

# ── Database ─────────────────────────────────────────────────────────────────

db db-up:
	docker compose up -d postgres
	@echo "Waiting for PostgreSQL..."
	@until docker compose exec postgres pg_isready -U ava -q 2>/dev/null; do sleep 1; done
	@echo "PostgreSQL ready."

db-down:
	docker compose down

db-migrate: db-up
	cd $(BACKEND_DIR) && $(VENV_PYTHON) -m alembic upgrade head

db-reset:
	docker compose down -v
	$(MAKE) db-up
	sleep 2
	$(MAKE) db-migrate

# ── Run ──────────────────────────────────────────────────────────────────────

dev-backend: db-up
	@echo "Starting backend (REST :8471 / WS :8472)..."
	cd $(BACKEND_DIR) && $(VENV_PYTHON) -m uvicorn main:app --host 0.0.0.0 --port 8471 --reload

dev-worker: db-up
	@echo "Starting procrastinate worker (background jobs: meeting docs, embeddings, auto-categorize)..."
	cd $(BACKEND_DIR) && $(VENV_PYTHON) worker.py

dev-frontend:
	@echo "Starting frontend (Next.js :4731)..."
	cd $(FRONTEND_DIR) && pnpm dev

dev:
	@echo "Starting db + backend + worker + frontend..."
	@$(MAKE) db-up
	@$(MAKE) -j3 dev-backend dev-worker dev-frontend

# ── Test ─────────────────────────────────────────────────────────────────────

# Ensure the test DB exists (creates ava_test if absent; never touches ava).
db-test-up: db-up
	@docker exec ava-postgres psql -U ava -d postgres -tc \
		"SELECT 1 FROM pg_database WHERE datname='ava_test'" | grep -q 1 || \
		docker exec ava-postgres psql -U ava -d postgres -c 'CREATE DATABASE ava_test'
	@echo "ava_test DB ready."

test test-backend: db-test-up
	@cd $(BACKEND_DIR) && \
		TEST_DATABASE_URL=$${TEST_DATABASE_URL:-postgresql+asyncpg://ava:ava_dev_password@localhost:5432/ava_test} \
		$(VENV_PYTEST) tests/ -v

# ── Utils ─────────────────────────────────────────────────────────────────────

export-openapi:
	@echo "Exporting openapi.json..."
	cd $(BACKEND_DIR) && $(VENV_PYTHON) dump_openapi.py && cp openapi.json ../openapi.json
	@echo "openapi.json updated."

lint:
	@$(VENV_PYTHON) -m ruff check $(BACKEND_DIR)/ || true

VENV        := project-ava
VENV_PYTHON := $(HOME)/.virtualenvs/$(VENV)/bin/python
VENV_PIP    := $(HOME)/.virtualenvs/$(VENV)/bin/pip
VENV_PYTEST := $(HOME)/.virtualenvs/$(VENV)/bin/pytest
BACKEND_DIR := backend
FRONTEND_DIR := frontend

.PHONY: help install install-backend install-frontend \
        dev dev-backend dev-frontend \
        start stop \
        test test-backend lint \
        export-openapi \
        setup-env venv

help:
	@echo "Ava Project — available targets:"
	@echo ""
	@echo "  Setup"
	@echo "    make install          Install all dependencies (backend + frontend)"
	@echo "    make install-backend  Install Python dependencies only"
	@echo "    make install-frontend Install Node dependencies only"
	@echo "    make setup-env        Copy .env.example to backend/.env (if missing)"
	@echo ""
	@echo "  Run"
	@echo "    make dev              Start backend + frontend in parallel"
	@echo "    make dev-backend      Start FastAPI backend only (ports 8471/8472)"
	@echo "    make dev-frontend     Start Next.js frontend only (port 4731)"
	@echo ""
	@echo "  Test"
	@echo "    make test             Run all 77 backend tests"
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
	cd $(FRONTEND_DIR) && npm install

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

# ── Run ──────────────────────────────────────────────────────────────────────

dev-backend:
	@echo "Starting backend (REST :8471 / WS :8472)..."
	cd $(BACKEND_DIR) && $(VENV_PYTHON) -m uvicorn main:app --host 0.0.0.0 --port 8471 --reload

dev-frontend:
	@echo "Starting frontend (Next.js :4731)..."
	cd $(FRONTEND_DIR) && npm run dev -- --port 4731

dev:
	@echo "Starting backend + frontend..."
	@$(MAKE) -j2 dev-backend dev-frontend

# ── Test ─────────────────────────────────────────────────────────────────────

test test-backend:
	cd $(BACKEND_DIR) && $(VENV_PYTEST) tests/ -v

# ── Utils ─────────────────────────────────────────────────────────────────────

export-openapi:
	@echo "Exporting openapi.json..."
	cd $(BACKEND_DIR) && $(VENV_PYTHON) dump_openapi.py > ../openapi.json
	@echo "openapi.json updated."

lint:
	@$(VENV_PYTHON) -m ruff check $(BACKEND_DIR)/ || true

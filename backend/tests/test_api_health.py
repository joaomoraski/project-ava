"""Tests for health and config endpoints."""
import pytest


def test_root(client):
    resp = client.get("/")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "running"
    assert "version" in data


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_config_get(client):
    resp = client.get("/api/config")
    assert resp.status_code == 200
    data = resp.json()
    assert "llm_provider" in data
    assert "tts_engine" in data


def test_mode_get(client):
    resp = client.get("/api/modes/current")
    assert resp.status_code == 200
    data = resp.json()
    assert "mode" in data
    assert "workspace" in data


def test_mode_switch(client):
    resp = client.post("/api/modes/switch", json={"mode": "background"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    resp2 = client.get("/api/modes/current")
    assert resp2.json()["mode"] == "background"


def test_security_headers(client):
    resp = client.get("/health")
    assert resp.headers.get("x-frame-options") == "DENY"
    assert resp.headers.get("x-content-type-options") == "nosniff"
    assert "x-ava-version" in resp.headers


def test_swagger_ui(client):
    resp = client.get("/docs")
    assert resp.status_code == 200


def test_openapi_json(client):
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    spec = resp.json()
    assert spec["info"]["title"] == "Ava Project API"
    assert "/api/modes/current" in spec["paths"]

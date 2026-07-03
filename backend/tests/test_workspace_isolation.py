"""Workspace isolation tests — single-resource endpoints must enforce ownership.

A resource created in workspace A must not be readable, writable, or deletable
by a client operating in workspace B.  The expected response is HTTP 404 (not
403) so that existence is never leaked.

Covered routers: meetings, notes, todos, action_items, contexts.
Jobs are omitted because creating a JobTask requires procrastinate infra that is
not available in the test environment.

Test structure:
  1. Ensure "work" workspace exists.
  2. Create a resource in "work".
  3. Attempt GET / PUT / DELETE from "personal" → expect 404.
  4. Attempt same operations from "work" → expect 2xx (same-workspace access works).
"""
from __future__ import annotations

import pytest


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _ws_header(workspace: str) -> dict:
    return {"X-Workspace": workspace}


def _ensure_work_workspace(client) -> None:
    """Create the 'work' workspace if it doesn't already exist."""
    resp = client.post("/api/workspaces", json={
        "name": "work",
        "system_prompt": "Work workspace for isolation tests.",
    })
    # 200 = created, 409 = already exists — both are acceptable
    assert resp.status_code in (200, 409), f"Unexpected status creating 'work': {resp.text}"


# ─── Meetings ────────────────────────────────────────────────────────────────


class TestMeetingIsolation:
    def test_get_meeting_wrong_workspace_returns_404(self, client):
        _ensure_work_workspace(client)

        # Create in 'work'
        resp = client.post("/api/meetings", json={
            "title": "Work Meeting",
            "workspace": "work",
            "participants": ["Eve"],
        })
        assert resp.status_code == 200, resp.text
        meeting_id = resp.json()["id"]

        # Attempt GET from 'personal' → 404
        get_resp = client.get(
            f"/api/meetings/{meeting_id}",
            headers=_ws_header("personal"),
        )
        assert get_resp.status_code == 404, get_resp.text

    def test_get_meeting_correct_workspace_returns_200(self, client):
        _ensure_work_workspace(client)

        resp = client.post("/api/meetings", json={
            "title": "Work Meeting Same WS",
            "workspace": "work",
            "participants": [],
        })
        assert resp.status_code == 200, resp.text
        meeting_id = resp.json()["id"]

        get_resp = client.get(
            f"/api/meetings/{meeting_id}",
            headers=_ws_header("work"),
        )
        assert get_resp.status_code == 200, get_resp.text
        assert get_resp.json()["id"] == meeting_id

    def test_update_meeting_wrong_workspace_returns_404(self, client):
        _ensure_work_workspace(client)

        resp = client.post("/api/meetings", json={
            "title": "Work Meeting Update",
            "workspace": "work",
            "participants": [],
        })
        meeting_id = resp.json()["id"]

        put_resp = client.put(
            f"/api/meetings/{meeting_id}",
            json={"title": "Hijacked"},
            headers=_ws_header("personal"),
        )
        assert put_resp.status_code == 404, put_resp.text

    def test_delete_meeting_wrong_workspace_returns_404(self, client):
        _ensure_work_workspace(client)

        resp = client.post("/api/meetings", json={
            "title": "Work Meeting Delete",
            "workspace": "work",
            "participants": [],
        })
        meeting_id = resp.json()["id"]

        del_resp = client.delete(
            f"/api/meetings/{meeting_id}",
            headers=_ws_header("personal"),
        )
        assert del_resp.status_code == 404, del_resp.text

        # The meeting must still exist under the correct workspace
        get_resp = client.get(
            f"/api/meetings/{meeting_id}",
            headers=_ws_header("work"),
        )
        assert get_resp.status_code == 200, get_resp.text

    def test_stop_meeting_wrong_workspace_returns_404(self, client):
        _ensure_work_workspace(client)

        resp = client.post("/api/meetings", json={
            "title": "Work Meeting Stop",
            "workspace": "work",
            "participants": [],
        })
        meeting_id = resp.json()["id"]

        stop_resp = client.post(
            f"/api/meetings/{meeting_id}/stop",
            headers=_ws_header("personal"),
        )
        assert stop_resp.status_code == 404, stop_resp.text

    def test_related_meetings_wrong_workspace_returns_404(self, client):
        _ensure_work_workspace(client)

        resp = client.post("/api/meetings", json={
            "title": "Work Meeting Related",
            "workspace": "work",
            "participants": [],
        })
        meeting_id = resp.json()["id"]

        related_resp = client.get(
            f"/api/meetings/{meeting_id}/related",
            headers=_ws_header("personal"),
        )
        assert related_resp.status_code == 404, related_resp.text


# ─── Notes ───────────────────────────────────────────────────────────────────


class TestNoteIsolation:
    def test_get_note_wrong_workspace_returns_404(self, client):
        _ensure_work_workspace(client)

        resp = client.post("/api/notes", json={
            "title": "Work Note",
            "content": "Confidential work content.",
            "workspace": "work",
            "tags": [],
        })
        assert resp.status_code == 201, resp.text
        note_id = resp.json()["id"]

        get_resp = client.get(
            f"/api/notes/{note_id}",
            headers=_ws_header("personal"),
        )
        assert get_resp.status_code == 404, get_resp.text

    def test_get_note_correct_workspace_returns_200(self, client):
        _ensure_work_workspace(client)

        resp = client.post("/api/notes", json={
            "title": "Work Note Same WS",
            "content": "Content.",
            "workspace": "work",
            "tags": [],
        })
        note_id = resp.json()["id"]

        get_resp = client.get(
            f"/api/notes/{note_id}",
            headers=_ws_header("work"),
        )
        assert get_resp.status_code == 200, get_resp.text
        assert get_resp.json()["id"] == note_id

    def test_update_note_wrong_workspace_returns_404(self, client):
        _ensure_work_workspace(client)

        resp = client.post("/api/notes", json={
            "title": "Work Note Update",
            "content": "Original.",
            "workspace": "work",
            "tags": [],
        })
        note_id = resp.json()["id"]

        put_resp = client.put(
            f"/api/notes/{note_id}",
            json={"title": "Hijacked"},
            headers=_ws_header("personal"),
        )
        assert put_resp.status_code == 404, put_resp.text

    def test_delete_note_wrong_workspace_returns_404(self, client):
        _ensure_work_workspace(client)

        resp = client.post("/api/notes", json={
            "title": "Work Note Delete",
            "content": "Original.",
            "workspace": "work",
            "tags": [],
        })
        note_id = resp.json()["id"]

        del_resp = client.delete(
            f"/api/notes/{note_id}",
            headers=_ws_header("personal"),
        )
        assert del_resp.status_code == 404, del_resp.text

        # Verify the note is intact from the correct workspace
        get_resp = client.get(
            f"/api/notes/{note_id}",
            headers=_ws_header("work"),
        )
        assert get_resp.status_code == 200, get_resp.text


# ─── Todos ────────────────────────────────────────────────────────────────────


class TestTodoIsolation:
    def test_get_todo_wrong_workspace_returns_404(self, client):
        _ensure_work_workspace(client)

        resp = client.post("/api/todos", json={
            "title": "Work Todo",
            "workspace": "work",
        })
        assert resp.status_code == 201, resp.text
        todo_id = resp.json()["id"]

        get_resp = client.get(
            f"/api/todos/{todo_id}",
            headers=_ws_header("personal"),
        )
        assert get_resp.status_code == 404, get_resp.text

    def test_get_todo_correct_workspace_returns_200(self, client):
        _ensure_work_workspace(client)

        resp = client.post("/api/todos", json={
            "title": "Work Todo Same WS",
            "workspace": "work",
        })
        todo_id = resp.json()["id"]

        get_resp = client.get(
            f"/api/todos/{todo_id}",
            headers=_ws_header("work"),
        )
        assert get_resp.status_code == 200, get_resp.text
        assert get_resp.json()["id"] == todo_id

    def test_update_todo_wrong_workspace_returns_404(self, client):
        _ensure_work_workspace(client)

        resp = client.post("/api/todos", json={
            "title": "Work Todo Update",
            "workspace": "work",
        })
        todo_id = resp.json()["id"]

        put_resp = client.put(
            f"/api/todos/{todo_id}",
            json={"title": "Hijacked"},
            headers=_ws_header("personal"),
        )
        assert put_resp.status_code == 404, put_resp.text

    def test_delete_todo_wrong_workspace_returns_404(self, client):
        _ensure_work_workspace(client)

        resp = client.post("/api/todos", json={
            "title": "Work Todo Delete",
            "workspace": "work",
        })
        todo_id = resp.json()["id"]

        del_resp = client.delete(
            f"/api/todos/{todo_id}",
            headers=_ws_header("personal"),
        )
        assert del_resp.status_code == 404, del_resp.text

        get_resp = client.get(
            f"/api/todos/{todo_id}",
            headers=_ws_header("work"),
        )
        assert get_resp.status_code == 200, get_resp.text


# ─── Action Items ─────────────────────────────────────────────────────────────


class TestActionItemIsolation:
    def test_get_action_item_wrong_workspace_returns_404(self, client):
        _ensure_work_workspace(client)

        resp = client.post("/api/action-items", json={
            "description": "Work action item",
            "workspace": "work",
            "owner": "Eve",
        })
        assert resp.status_code == 200, resp.text
        item_id = resp.json()["id"]

        get_resp = client.get(
            f"/api/action-items/{item_id}",
            headers=_ws_header("personal"),
        )
        assert get_resp.status_code == 404, get_resp.text

    def test_get_action_item_correct_workspace_returns_200(self, client):
        _ensure_work_workspace(client)

        resp = client.post("/api/action-items", json={
            "description": "Work action item same ws",
            "workspace": "work",
            "owner": "Eve",
        })
        item_id = resp.json()["id"]

        get_resp = client.get(
            f"/api/action-items/{item_id}",
            headers=_ws_header("work"),
        )
        assert get_resp.status_code == 200, get_resp.text
        assert get_resp.json()["id"] == item_id

    def test_update_action_item_wrong_workspace_returns_404(self, client):
        _ensure_work_workspace(client)

        resp = client.post("/api/action-items", json={
            "description": "Work action item update",
            "workspace": "work",
            "owner": "Eve",
        })
        item_id = resp.json()["id"]

        put_resp = client.put(
            f"/api/action-items/{item_id}",
            json={"description": "Hijacked"},
            headers=_ws_header("personal"),
        )
        assert put_resp.status_code == 404, put_resp.text

    def test_delete_action_item_wrong_workspace_returns_404(self, client):
        _ensure_work_workspace(client)

        resp = client.post("/api/action-items", json={
            "description": "Work action item delete",
            "workspace": "work",
            "owner": "Eve",
        })
        item_id = resp.json()["id"]

        del_resp = client.delete(
            f"/api/action-items/{item_id}",
            headers=_ws_header("personal"),
        )
        assert del_resp.status_code == 404, del_resp.text

        get_resp = client.get(
            f"/api/action-items/{item_id}",
            headers=_ws_header("work"),
        )
        assert get_resp.status_code == 200, get_resp.text

    def test_link_action_item_wrong_workspace_returns_404(self, client):
        _ensure_work_workspace(client)

        r1 = client.post("/api/action-items", json={
            "description": "Work item 1",
            "workspace": "work",
            "owner": "Eve",
        })
        r2 = client.post("/api/action-items", json={
            "description": "Work item 2",
            "workspace": "work",
            "owner": "Eve",
        })
        id1 = r1.json()["id"]
        id2 = r2.json()["id"]

        link_resp = client.post(
            f"/api/action-items/{id1}/link",
            json={"linked_item_id": id2},
            headers=_ws_header("personal"),
        )
        assert link_resp.status_code == 404, link_resp.text


# ─── Contexts ─────────────────────────────────────────────────────────────────


class TestContextIsolation:
    def test_get_context_wrong_workspace_returns_404(self, client):
        _ensure_work_workspace(client)

        resp = client.post("/api/contexts", json={
            "name": "Work Context Isolation",
            "workspace": "work",
        })
        assert resp.status_code == 201, resp.text
        ctx_id = resp.json()["id"]

        get_resp = client.get(
            f"/api/contexts/{ctx_id}",
            headers=_ws_header("personal"),
        )
        assert get_resp.status_code == 404, get_resp.text

    def test_get_context_correct_workspace_returns_200(self, client):
        _ensure_work_workspace(client)

        resp = client.post("/api/contexts", json={
            "name": "Work Context Same WS",
            "workspace": "work",
        })
        ctx_id = resp.json()["id"]

        get_resp = client.get(
            f"/api/contexts/{ctx_id}",
            headers=_ws_header("work"),
        )
        assert get_resp.status_code == 200, get_resp.text
        assert get_resp.json()["id"] == ctx_id

    def test_update_context_wrong_workspace_returns_404(self, client):
        _ensure_work_workspace(client)

        resp = client.post("/api/contexts", json={
            "name": "Work Context Update",
            "workspace": "work",
        })
        ctx_id = resp.json()["id"]

        put_resp = client.put(
            f"/api/contexts/{ctx_id}",
            json={"name": "Hijacked"},
            headers=_ws_header("personal"),
        )
        assert put_resp.status_code == 404, put_resp.text

    def test_delete_context_wrong_workspace_returns_404(self, client):
        _ensure_work_workspace(client)

        resp = client.post("/api/contexts", json={
            "name": "Work Context Delete",
            "workspace": "work",
        })
        ctx_id = resp.json()["id"]

        del_resp = client.delete(
            f"/api/contexts/{ctx_id}",
            headers=_ws_header("personal"),
        )
        assert del_resp.status_code == 404, del_resp.text

        get_resp = client.get(
            f"/api/contexts/{ctx_id}",
            headers=_ws_header("work"),
        )
        assert get_resp.status_code == 200, get_resp.text

    def test_context_bundle_wrong_workspace_returns_404(self, client):
        _ensure_work_workspace(client)

        resp = client.post("/api/contexts", json={
            "name": "Work Context Bundle",
            "workspace": "work",
        })
        ctx_id = resp.json()["id"]

        bundle_resp = client.get(
            f"/api/contexts/{ctx_id}/bundle",
            headers=_ws_header("personal"),
        )
        assert bundle_resp.status_code == 404, bundle_resp.text

    def test_context_items_wrong_workspace_returns_404(self, client):
        _ensure_work_workspace(client)

        resp = client.post("/api/contexts", json={
            "name": "Work Context Items",
            "workspace": "work",
        })
        ctx_id = resp.json()["id"]

        items_resp = client.get(
            f"/api/contexts/{ctx_id}/items",
            headers=_ws_header("personal"),
        )
        assert items_resp.status_code == 404, items_resp.text

    def test_context_link_wrong_workspace_returns_404(self, client):
        _ensure_work_workspace(client)

        resp = client.post("/api/contexts", json={
            "name": "Work Context Link",
            "workspace": "work",
        })
        ctx_id = resp.json()["id"]

        # Create a meeting in 'work' to link
        m_resp = client.post("/api/meetings", json={
            "title": "Work Meeting for Context Link",
            "workspace": "work",
            "participants": [],
        })
        meeting_id = m_resp.json()["id"]

        link_resp = client.post(
            f"/api/contexts/{ctx_id}/link",
            json={"item_type": "meeting", "item_id": meeting_id},
            headers=_ws_header("personal"),
        )
        assert link_resp.status_code == 404, link_resp.text

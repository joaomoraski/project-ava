"""Integration tests for Meetings, Action Items, Notes, and MCP API endpoints.

Uses the session-scoped sync TestClient from conftest.py.  All tests hit the
real PostgreSQL test DB (created by the _db_schema fixture).

Notes on patterns:
- client is a sync FastAPI TestClient (starlette); no asyncio needed in tests.
- "personal" workspace is created automatically by the _fake_init hook in conftest.
- Tests are independent but share the same DB; IDs returned by POST are used for
  subsequent GET/PUT/DELETE calls within each test.
"""
from __future__ import annotations

import pytest


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _meeting_payload(**kwargs) -> dict:
    defaults = {
        "title": "Test Meeting",
        "workspace": "personal",
        "participants": ["Alice", "Bob"],
    }
    defaults.update(kwargs)
    return defaults


def _action_item_payload(**kwargs) -> dict:
    defaults = {
        "description": "Follow up on deliverables",
        "workspace": "personal",
        "owner": "Alice",
    }
    defaults.update(kwargs)
    return defaults


def _note_payload(**kwargs) -> dict:
    defaults = {
        "title": "My Note",
        "content": "Some note content here.",
        "workspace": "personal",
        "tags": [],
    }
    defaults.update(kwargs)
    return defaults


# ─── Meetings ────────────────────────────────────────────────────────────────

class TestMeetings:
    def test_create_meeting(self, client):
        resp = client.post("/api/meetings", json=_meeting_payload())
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "id" in data
        assert data["title"] == "Test Meeting"
        assert data["status"] == "recording"
        assert "Alice" in data["participants"]

    def test_list_meetings(self, client):
        client.post("/api/meetings", json=_meeting_payload(title="Meeting A"))
        client.post("/api/meetings", json=_meeting_payload(title="Meeting B"))

        resp = client.get("/api/meetings")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "meetings" in data
        assert "count" in data
        # At least the two we just created
        assert data["count"] >= 2

    def test_get_meeting(self, client):
        create_resp = client.post("/api/meetings", json=_meeting_payload(title="Get Me"))
        assert create_resp.status_code == 200
        meeting_id = create_resp.json()["id"]

        resp = client.get(f"/api/meetings/{meeting_id}")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["id"] == meeting_id
        assert data["title"] == "Get Me"

    def test_get_meeting_not_found(self, client):
        resp = client.get("/api/meetings/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404

    def test_update_meeting(self, client):
        create_resp = client.post("/api/meetings", json=_meeting_payload(title="Original Title"))
        meeting_id = create_resp.json()["id"]

        resp = client.put(f"/api/meetings/{meeting_id}", json={"title": "Updated Title"})
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["title"] == "Updated Title"
        assert data["id"] == meeting_id

    def test_stop_meeting(self, client):
        create_resp = client.post("/api/meetings", json=_meeting_payload(title="Ongoing Meeting"))
        meeting_id = create_resp.json()["id"]
        assert create_resp.json()["status"] == "recording"

        resp = client.post(f"/api/meetings/{meeting_id}/stop")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "completed"
        assert data["ended_at"] is not None

    def test_delete_meeting(self, client):
        create_resp = client.post("/api/meetings", json=_meeting_payload(title="To Be Deleted"))
        meeting_id = create_resp.json()["id"]

        del_resp = client.delete(f"/api/meetings/{meeting_id}")
        assert del_resp.status_code == 200, del_resp.text

        get_resp = client.get(f"/api/meetings/{meeting_id}")
        assert get_resp.status_code == 404

    def test_meeting_prep(self, client):
        # Create a meeting with specific participants
        client.post("/api/meetings", json=_meeting_payload(
            title="Prep Source Meeting",
            participants=["Alice", "Charlie"],
        ))

        resp = client.get("/api/meetings/prep?participants=Alice,Charlie")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "past_meetings" in data
        assert "open_action_items" in data
        # At minimum, structure is correct — past_meetings is a list
        assert isinstance(data["past_meetings"], list)
        assert isinstance(data["open_action_items"], list)

    def test_meeting_prep_requires_participants(self, client):
        resp = client.get("/api/meetings/prep")
        assert resp.status_code == 422

    def test_meeting_decisions(self, client):
        resp = client.get("/api/meetings/decisions")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "decisions" in data
        assert "count" in data
        assert isinstance(data["decisions"], list)

    def test_meeting_decisions_with_workspace_filter(self, client):
        resp = client.get("/api/meetings/decisions?workspace=personal")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "decisions" in data

    def test_meeting_related(self, client):
        # Create two meetings in the same workspace
        r1 = client.post("/api/meetings", json=_meeting_payload(title="Related A"))
        r2 = client.post("/api/meetings", json=_meeting_payload(title="Related B"))
        id1 = r1.json()["id"]
        id2 = r2.json()["id"]

        resp = client.get(f"/api/meetings/{id1}/related")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "related" in data
        assert "count" in data
        assert isinstance(data["related"], list)
        # id2 should appear in the related list (same workspace, different meeting)
        related_ids = [m["id"] for m in data["related"]]
        assert id2 in related_ids

    def test_list_meetings_filter_by_status(self, client):
        create_resp = client.post("/api/meetings", json=_meeting_payload(title="Filter Status"))
        meeting_id = create_resp.json()["id"]
        client.post(f"/api/meetings/{meeting_id}/stop")

        resp = client.get("/api/meetings?status=completed")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert all(m["status"] == "completed" for m in data["meetings"])

    def test_list_meetings_filter_by_workspace(self, client):
        resp = client.get("/api/meetings?workspace=personal")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "meetings" in data

    def test_list_meetings_unknown_workspace_returns_empty(self, client):
        resp = client.get("/api/meetings?workspace=nonexistent_workspace_xyz")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["meetings"] == []
        assert data["count"] == 0


# ─── Action Items ─────────────────────────────────────────────────────────────

class TestActionItems:
    def test_create_action_item(self, client):
        resp = client.post("/api/action-items", json=_action_item_payload())
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "id" in data
        assert data["description"] == "Follow up on deliverables"
        assert data["status"] == "pending"
        assert data["owner"] == "Alice"
        assert data["completed_at"] is None

    def test_list_action_items(self, client):
        for i in range(3):
            client.post("/api/action-items", json=_action_item_payload(
                description=f"Task {i}"
            ))

        resp = client.get("/api/action-items")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "action_items" in data
        assert "count" in data
        assert data["count"] >= 3

    def test_filter_action_items_by_status(self, client):
        # Create one pending and one done item
        pending_resp = client.post("/api/action-items", json=_action_item_payload(
            description="Pending task filter test"
        ))
        pending_id = pending_resp.json()["id"]

        done_resp = client.post("/api/action-items", json=_action_item_payload(
            description="Done task filter test"
        ))
        done_id = done_resp.json()["id"]
        client.put(f"/api/action-items/{done_id}", json={"status": "done"})

        # Filter by pending
        resp = client.get("/api/action-items?status=pending")
        assert resp.status_code == 200, resp.text
        pending_ids = [a["id"] for a in resp.json()["action_items"]]
        assert pending_id in pending_ids
        assert done_id not in pending_ids

        # Filter by done
        resp = client.get("/api/action-items?status=done")
        assert resp.status_code == 200, resp.text
        done_ids = [a["id"] for a in resp.json()["action_items"]]
        assert done_id in done_ids
        assert pending_id not in done_ids

    def test_update_action_item_done(self, client):
        create_resp = client.post("/api/action-items", json=_action_item_payload(
            description="Mark as done"
        ))
        item_id = create_resp.json()["id"]
        assert create_resp.json()["completed_at"] is None

        resp = client.put(f"/api/action-items/{item_id}", json={"status": "done"})
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "done"
        assert data["completed_at"] is not None

    def test_update_action_item_fields(self, client):
        create_resp = client.post("/api/action-items", json=_action_item_payload())
        item_id = create_resp.json()["id"]

        resp = client.put(f"/api/action-items/{item_id}", json={
            "description": "Updated description",
            "owner": "Bob",
        })
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["description"] == "Updated description"
        assert data["owner"] == "Bob"

    def test_get_action_item(self, client):
        create_resp = client.post("/api/action-items", json=_action_item_payload())
        item_id = create_resp.json()["id"]

        resp = client.get(f"/api/action-items/{item_id}")
        assert resp.status_code == 200, resp.text
        assert resp.json()["id"] == item_id

    def test_get_action_item_not_found(self, client):
        resp = client.get("/api/action-items/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404

    def test_delete_action_item(self, client):
        create_resp = client.post("/api/action-items", json=_action_item_payload(
            description="Delete me"
        ))
        item_id = create_resp.json()["id"]

        del_resp = client.delete(f"/api/action-items/{item_id}")
        assert del_resp.status_code == 200, del_resp.text

        get_resp = client.get(f"/api/action-items/{item_id}")
        assert get_resp.status_code == 404

    def test_link_action_items(self, client):
        r1 = client.post("/api/action-items", json=_action_item_payload(description="Item 1"))
        r2 = client.post("/api/action-items", json=_action_item_payload(description="Item 2"))
        id1 = r1.json()["id"]
        id2 = r2.json()["id"]

        resp = client.post(f"/api/action-items/{id1}/link", json={"linked_item_id": id2})
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["linked_item_id"] == id2

    def test_link_action_item_missing_linked_id(self, client):
        create_resp = client.post("/api/action-items", json=_action_item_payload())
        item_id = create_resp.json()["id"]

        resp = client.post(f"/api/action-items/{item_id}/link", json={})
        assert resp.status_code == 422

    def test_link_action_item_nonexistent_target(self, client):
        create_resp = client.post("/api/action-items", json=_action_item_payload())
        item_id = create_resp.json()["id"]

        resp = client.post(f"/api/action-items/{item_id}/link", json={
            "linked_item_id": "00000000-0000-0000-0000-000000000000"
        })
        assert resp.status_code == 404

    def test_filter_action_items_by_owner(self, client):
        client.post("/api/action-items", json=_action_item_payload(
            description="Owner filter test", owner="UniqueOwnerXYZ"
        ))

        resp = client.get("/api/action-items?owner=UniqueOwnerXYZ")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["count"] >= 1
        assert all(a["owner"] == "UniqueOwnerXYZ" for a in data["action_items"])

    def test_action_item_unknown_workspace_returns_empty(self, client):
        resp = client.get("/api/action-items?workspace=nonexistent_workspace_xyz")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["action_items"] == []
        assert data["count"] == 0

    def test_create_action_item_invalid_workspace(self, client):
        resp = client.post("/api/action-items", json=_action_item_payload(
            workspace="nonexistent_workspace_xyz"
        ))
        assert resp.status_code == 404

    def test_action_item_due_date(self, client):
        resp = client.post("/api/action-items", json=_action_item_payload(
            due_date="2026-12-31T00:00:00"
        ))
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["due_date"] is not None
        assert "2026-12-31" in data["due_date"]


# ─── Notes ────────────────────────────────────────────────────────────────────

class TestNotes:
    def test_create_note(self, client):
        resp = client.post("/api/notes", json=_note_payload())
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert "id" in data
        assert data["title"] == "My Note"
        assert data["content"] == "Some note content here."
        assert data["tags"] == []

    def test_list_notes(self, client):
        client.post("/api/notes", json=_note_payload(title="Note Alpha"))
        client.post("/api/notes", json=_note_payload(title="Note Beta"))

        resp = client.get("/api/notes?workspace=personal")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "notes" in data
        assert "count" in data
        assert data["count"] >= 2

    def test_get_note(self, client):
        create_resp = client.post("/api/notes", json=_note_payload(title="Fetch Me"))
        note_id = create_resp.json()["id"]

        resp = client.get(f"/api/notes/{note_id}")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["id"] == note_id
        assert data["title"] == "Fetch Me"

    def test_get_note_not_found(self, client):
        resp = client.get("/api/notes/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404

    def test_get_note_invalid_id(self, client):
        resp = client.get("/api/notes/not-a-uuid")
        assert resp.status_code == 422

    def test_filter_notes_by_tags(self, client):
        client.post("/api/notes", json=_note_payload(
            title="Tagged Note",
            tags=["python", "backend"],
        ))
        client.post("/api/notes", json=_note_payload(
            title="Untagged Note",
            tags=[],
        ))

        resp = client.get("/api/notes?workspace=personal&tags=python")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["count"] >= 1
        for note in data["notes"]:
            assert "python" in note["tags"]

    def test_filter_notes_multiple_tags(self, client):
        client.post("/api/notes", json=_note_payload(
            title="Multi-tag Note",
            tags=["python", "backend", "api"],
        ))

        resp = client.get("/api/notes?workspace=personal&tags=python,backend")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        # All returned notes must have both tags
        for note in data["notes"]:
            assert "python" in note["tags"]
            assert "backend" in note["tags"]

    def test_search_notes(self, client):
        unique_term = "xyzUniqueContentTerm987"
        client.post("/api/notes", json=_note_payload(
            title="Searchable Note",
            content=f"This note contains the term {unique_term}.",
        ))

        resp = client.get(f"/api/notes?workspace=personal&search={unique_term}")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["count"] >= 1
        assert any(unique_term in n["content"] for n in data["notes"])

    def test_search_notes_by_title(self, client):
        unique_title = "UniqueTitleXYZ456"
        client.post("/api/notes", json=_note_payload(title=unique_title))

        resp = client.get(f"/api/notes?workspace=personal&search={unique_title}")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["count"] >= 1
        assert any(n["title"] == unique_title for n in data["notes"])

    def test_update_note(self, client):
        create_resp = client.post("/api/notes", json=_note_payload(title="Old Title"))
        note_id = create_resp.json()["id"]

        resp = client.put(f"/api/notes/{note_id}", json={
            "title": "New Title",
            "content": "Updated content.",
        })
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["title"] == "New Title"
        assert data["content"] == "Updated content."

    def test_update_note_tags(self, client):
        create_resp = client.post("/api/notes", json=_note_payload(tags=["old"]))
        note_id = create_resp.json()["id"]

        resp = client.put(f"/api/notes/{note_id}", json={"tags": ["new", "tags"]})
        assert resp.status_code == 200, resp.text
        assert resp.json()["tags"] == ["new", "tags"]

    def test_delete_note(self, client):
        create_resp = client.post("/api/notes", json=_note_payload(title="Delete Me"))
        note_id = create_resp.json()["id"]

        del_resp = client.delete(f"/api/notes/{note_id}")
        assert del_resp.status_code == 200, del_resp.text

        get_resp = client.get(f"/api/notes/{note_id}")
        assert get_resp.status_code == 404

    def test_delete_note_not_found(self, client):
        resp = client.delete("/api/notes/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404

    def test_list_notes_unknown_workspace(self, client):
        resp = client.get("/api/notes?workspace=nonexistent_workspace_xyz")
        assert resp.status_code == 404

    def test_create_note_invalid_workspace(self, client):
        resp = client.post("/api/notes", json=_note_payload(workspace="nonexistent_workspace_xyz"))
        assert resp.status_code == 404


# ─── MCP ─────────────────────────────────────────────────────────────────────

class TestMcp:
    def test_list_mcp_servers(self, client):
        resp = client.get("/api/mcp/servers")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "servers" in data
        assert "count" in data
        assert isinstance(data["servers"], list)

    def test_register_mcp_server(self, client):
        server_name = "test-mcp-server-integration"
        # Clean up any leftover from a prior run
        client.delete(f"/api/mcp/servers/{server_name}")

        resp = client.post("/api/mcp/servers", json={
            "name": server_name,
            "command": "echo",
            "args": ["hello"],
            "description": "Integration test MCP server",
        })
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["ok"] is True
        assert data["name"] == server_name

        # Verify it appears in the list
        list_resp = client.get("/api/mcp/servers")
        names = [s["name"] for s in list_resp.json()["servers"]]
        assert server_name in names

        # Clean up
        client.delete(f"/api/mcp/servers/{server_name}")

    def test_register_duplicate_mcp_server_returns_409(self, client):
        server_name = "test-mcp-dup-server"
        client.delete(f"/api/mcp/servers/{server_name}")

        client.post("/api/mcp/servers", json={
            "name": server_name,
            "command": "echo",
        })
        resp = client.post("/api/mcp/servers", json={
            "name": server_name,
            "command": "echo",
        })
        assert resp.status_code == 409

        # Clean up
        client.delete(f"/api/mcp/servers/{server_name}")

    def test_delete_mcp_server(self, client):
        server_name = "test-mcp-delete-server"
        client.post("/api/mcp/servers", json={
            "name": server_name,
            "command": "echo",
        })

        resp = client.delete(f"/api/mcp/servers/{server_name}")
        assert resp.status_code == 200, resp.text

        list_resp = client.get("/api/mcp/servers")
        names = [s["name"] for s in list_resp.json()["servers"]]
        assert server_name not in names

    def test_mcp_server_health_not_found(self, client):
        resp = client.get("/api/mcp/servers/nonexistent-server-xyz/health")
        assert resp.status_code == 404

    def test_mcp_server_health(self, client):
        server_name = "test-mcp-health-server"
        client.delete(f"/api/mcp/servers/{server_name}")
        client.post("/api/mcp/servers", json={
            "name": server_name,
            "command": "echo",
        })

        resp = client.get(f"/api/mcp/servers/{server_name}/health")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "status" in data
        assert data["name"] == server_name

        client.delete(f"/api/mcp/servers/{server_name}")

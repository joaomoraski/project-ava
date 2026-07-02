"""Tests for meeting search and embedding bug fixes.

Covers:
- search_meetings SQL fallback finds meetings by exact title (bug 1b)
- embed_meeting_document does not duplicate chunks on retry (bug 2a)
- delete_meeting removes KB chunks (bug 2d)
- "(summary unavailable)" meetings are not embedded (bug 2b)
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ─── Bug 1b: SQL fallback searches by title ──────────────────────────────────

@pytest.mark.asyncio
async def test_search_meetings_sql_fallback_finds_by_title():
    """When RAG returns no meeting chunks the SQL fallback must match on title."""
    from unittest.mock import AsyncMock, MagicMock, patch

    # RAG returns nothing meeting-typed → forces SQL fallback.
    mock_kb = MagicMock()
    mock_kb.search = AsyncMock(return_value=[])

    # Fake workspace id
    ws_scalar = MagicMock()
    ws_scalar.scalar_one_or_none.return_value = "ws-uuid-123"

    # Fake meeting object matching by title
    fake_meeting = MagicMock()
    fake_meeting.title = "Budget Review 2026"
    fake_meeting.status = "completed"
    fake_meeting.started_at = None
    fake_meeting.transcript = ""
    fake_meeting.summary = "We reviewed the budget."

    meetings_result = MagicMock()
    meetings_result.scalars.return_value.all.return_value = [fake_meeting]

    mock_session = AsyncMock()
    # First execute → workspace id; second execute → meetings list
    mock_session.execute = AsyncMock(side_effect=[ws_scalar, meetings_result])

    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("core.db.engine.async_session", return_value=mock_ctx), \
         patch("core.knowledge.rag.KnowledgeBase", return_value=mock_kb):
        from tools.meetings import search_meetings
        result = await search_meetings.arun({"query": "Budget Review 2026", "workspace": "personal"})

    assert "Budget Review 2026" in result


# ─── Bug 1a: RAG over-fetch ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_search_meetings_rag_uses_top_k_20():
    """search_meetings must call kb.search with top_k >= 20 to avoid crowding."""
    mock_kb = MagicMock()
    mock_kb.search = AsyncMock(return_value=[])

    ws_scalar = MagicMock()
    ws_scalar.scalar_one_or_none.return_value = None

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=ws_scalar)

    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("core.db.engine.async_session", return_value=mock_ctx), \
         patch("core.knowledge.rag.KnowledgeBase", return_value=mock_kb):
        from tools.meetings import search_meetings
        await search_meetings.arun({"query": "anything", "workspace": "personal"})

    mock_kb.search.assert_called_once()
    _args, kwargs = mock_kb.search.call_args
    called_top_k = kwargs.get("top_k", _args[2] if len(_args) > 2 else None)
    assert called_top_k >= 20, f"Expected top_k >= 20, got {called_top_k}"


# ─── Bug 2a: dedup on retry — delete_source called before add_chunks ─────────

@pytest.mark.asyncio
async def test_embed_meeting_document_deletes_before_adding():
    """embed_meeting_document must call delete_source before add_chunks on retry."""
    import uuid as _uuid

    call_order: list[str] = []

    mock_kb = MagicMock()

    async def _delete_source(session, source):
        call_order.append("delete_source")
        return 3  # pretend 3 old chunks removed

    async def _add_chunks(session, chunks):
        call_order.append("add_chunks")
        return len(chunks)

    mock_kb.delete_source = _delete_source
    mock_kb.add_chunks = _add_chunks

    meeting_id = str(_uuid.uuid4())
    fake_meeting = MagicMock()
    fake_meeting.id = _uuid.UUID(meeting_id)
    fake_meeting.title = "Retried Meeting"
    fake_meeting.transcript = "Speaker 1: Hello world. Speaker 2: Let us begin."
    fake_meeting.summary = "A proper summary of the discussion."

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = fake_meeting

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)

    fake_chunks = [{"content": "chunk text", "source": f"meeting:{meeting_id}",
                    "source_type": "meeting", "file_name": ""}]

    with patch("core.knowledge.rag.KnowledgeBase", return_value=mock_kb), \
         patch("core.knowledge.ingestion.chunk_meeting_document", return_value=fake_chunks):
        from core.knowledge.meeting_doc import embed_meeting_document
        await embed_meeting_document(meeting_id, "personal", mock_session)

    assert "delete_source" in call_order, "delete_source was never called"
    assert "add_chunks" in call_order, "add_chunks was never called"
    assert call_order.index("delete_source") < call_order.index("add_chunks"), \
        "delete_source must be called BEFORE add_chunks"


# ─── Bug 2b: placeholder summary must NOT be embedded ────────────────────────

@pytest.mark.asyncio
async def test_embed_meeting_document_skips_placeholder_summary():
    """embed_meeting_document must not embed when summary is the placeholder string."""
    import uuid as _uuid

    mock_kb = MagicMock()
    mock_kb.add_chunks = AsyncMock(return_value=0)
    mock_kb.delete_source = AsyncMock(return_value=0)

    meeting_id = str(_uuid.uuid4())
    fake_meeting = MagicMock()
    fake_meeting.id = _uuid.UUID(meeting_id)
    fake_meeting.title = "Incomplete Meeting"
    fake_meeting.transcript = "Some transcript content here."
    fake_meeting.summary = "(summary unavailable)"

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = fake_meeting

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)

    with patch("core.knowledge.rag.KnowledgeBase", return_value=mock_kb):
        from core.knowledge.meeting_doc import embed_meeting_document
        await embed_meeting_document(meeting_id, "personal", mock_session)

    mock_kb.add_chunks.assert_not_called()


@pytest.mark.asyncio
async def test_embed_meeting_document_skips_empty_summary():
    """embed_meeting_document must not embed when summary is empty."""
    import uuid as _uuid

    mock_kb = MagicMock()
    mock_kb.add_chunks = AsyncMock(return_value=0)
    mock_kb.delete_source = AsyncMock(return_value=0)

    meeting_id = str(_uuid.uuid4())
    fake_meeting = MagicMock()
    fake_meeting.id = _uuid.UUID(meeting_id)
    fake_meeting.title = "Empty Summary Meeting"
    fake_meeting.transcript = "Lots of content here."
    fake_meeting.summary = ""

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = fake_meeting

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)

    with patch("core.knowledge.rag.KnowledgeBase", return_value=mock_kb):
        from core.knowledge.meeting_doc import embed_meeting_document
        await embed_meeting_document(meeting_id, "personal", mock_session)

    mock_kb.add_chunks.assert_not_called()


# ─── Bug 2d: delete_meeting removes KB chunks ────────────────────────────────

def test_delete_meeting_removes_kb_chunks(client):
    """DELETE /api/meetings/{id} must attempt to purge KB chunks (no crash)."""
    # Create a meeting
    resp = client.post("/api/meetings", json={
        "title": "Meeting With Chunks",
        "workspace": "personal",
        "participants": [],
    })
    assert resp.status_code == 200, resp.text
    meeting_id = resp.json()["id"]

    # Patch KnowledgeBase.delete_source so we can assert it was called.
    deleted_sources: list[str] = []

    async def _fake_delete(session, source):
        deleted_sources.append(source)
        return 0

    # KnowledgeBase is lazily imported inside the endpoint, so patch the class
    # at its definition module so both the DELETE and PUT endpoints pick it up.
    with patch("core.knowledge.rag.KnowledgeBase") as MockKB:
        instance = MockKB.return_value
        instance.delete_source = _fake_delete
        del_resp = client.delete(f"/api/meetings/{meeting_id}")

    assert del_resp.status_code == 200, del_resp.text
    MockKB.assert_called_once()
    assert f"meeting:{meeting_id}" in deleted_sources


# ─── Bug 2e: update summary purges stale chunks ──────────────────────────────

def test_update_meeting_summary_purges_kb_chunks(client):
    """PUT /api/meetings/{id} with a changed summary must purge KB chunks."""
    resp = client.post("/api/meetings", json={
        "title": "Stale Summary Meeting",
        "workspace": "personal",
        "participants": [],
    })
    assert resp.status_code == 200, resp.text
    meeting_id = resp.json()["id"]

    deleted_sources: list[str] = []

    async def _fake_delete(session, source):
        deleted_sources.append(source)
        return 2

    with patch("core.knowledge.rag.KnowledgeBase") as MockKB:
        instance = MockKB.return_value
        instance.delete_source = _fake_delete
        put_resp = client.put(
            f"/api/meetings/{meeting_id}",
            json={"summary": "Updated and accurate summary."},
        )

    assert put_resp.status_code == 200, put_resp.text
    MockKB.assert_called_once()
    assert f"meeting:{meeting_id}" in deleted_sources


def test_update_meeting_no_summary_change_skips_kb(client):
    """PUT /api/meetings/{id} without summary change must NOT touch KB."""
    resp = client.post("/api/meetings", json={
        "title": "Title Only Update",
        "workspace": "personal",
        "participants": [],
    })
    assert resp.status_code == 200, resp.text
    meeting_id = resp.json()["id"]

    with patch("core.knowledge.rag.KnowledgeBase") as MockKB:
        put_resp = client.put(
            f"/api/meetings/{meeting_id}",
            json={"title": "New Title Only"},
        )

    assert put_resp.status_code == 200, put_resp.text
    # KnowledgeBase should NOT be instantiated when only title changes
    MockKB.assert_not_called()

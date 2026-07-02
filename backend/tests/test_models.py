"""CRUD tests for Meeting, ActionItem, Note, GoogleAccount, CalendarEvent models."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from core.db.models import (
    ActionItem,
    CalendarEvent,
    GoogleAccount,
    Meeting,
    Note,
    Workspace,
)


# ── helpers ──────────────────────────────────────────────────────────────────

async def _make_workspace(session, name: str | None = None) -> Workspace:
    ws = Workspace(name=name or f"test-ws-{uuid.uuid4().hex[:8]}")
    session.add(ws)
    await session.flush()
    return ws


async def _make_meeting(session, workspace_id, **kwargs) -> Meeting:
    m = Meeting(workspace_id=workspace_id, **kwargs)
    session.add(m)
    await session.flush()
    return m


async def _make_google_account(session, email: str | None = None) -> GoogleAccount:
    acct = GoogleAccount(
        email=email or f"user-{uuid.uuid4().hex[:6]}@example.com",
        refresh_token_encrypted=b"encrypted-token",
        scopes=["https://www.googleapis.com/auth/calendar"],
    )
    session.add(acct)
    await session.flush()
    return acct


# ── Meeting ───────────────────────────────────────────────────────────────────

class TestMeeting:
    async def test_create_meeting(self, db_session):
        ws = await _make_workspace(db_session)
        meeting = Meeting(
            workspace_id=ws.id,
            title="Q2 Planning",
            calendar_event_id="evt_abc123",
            participants=["alice@example.com", "bob@example.com"],
            transcript="Bob: Let's review OKRs.",
            summary="Reviewed Q2 OKRs.",
            decisions=["Ship v2 by June"],
            document_md="# Q2 Planning\n\nSummary here.",
            status="completed",
        )
        db_session.add(meeting)
        await db_session.commit()
        await db_session.refresh(meeting)

        result = await db_session.get(Meeting, meeting.id)
        assert result is not None
        assert result.title == "Q2 Planning"
        assert result.calendar_event_id == "evt_abc123"
        assert result.participants == ["alice@example.com", "bob@example.com"]
        assert result.decisions == ["Ship v2 by June"]
        assert result.status == "completed"

    async def test_meeting_workspace_cascade(self, db_session):
        ws = await _make_workspace(db_session)
        ws_id = ws.id
        meeting = await _make_meeting(db_session, ws.id, title="To be deleted")
        meeting_id = meeting.id

        await db_session.delete(ws)
        await db_session.commit()
        db_session.expire_all()

        result = await db_session.get(Meeting, meeting_id)
        assert result is None

    async def test_meeting_default_values(self, db_session):
        ws = await _make_workspace(db_session)
        meeting = Meeting(workspace_id=ws.id)
        db_session.add(meeting)
        await db_session.commit()
        await db_session.refresh(meeting)

        assert meeting.status == "recording"
        assert meeting.participants == []
        assert meeting.decisions == []
        assert meeting.title == ""
        assert meeting.transcript == ""
        assert meeting.ended_at is None

    async def test_meeting_update_status(self, db_session):
        ws = await _make_workspace(db_session)
        meeting = await _make_meeting(db_session, ws.id, title="Standup")
        assert meeting.status == "recording"

        ended = datetime(2026, 4, 6, 10, 30, tzinfo=timezone.utc)
        meeting.status = "completed"
        meeting.ended_at = ended
        await db_session.commit()
        await db_session.refresh(meeting)

        assert meeting.status == "completed"
        assert meeting.ended_at == ended

    async def test_meeting_participants_jsonb(self, db_session):
        ws = await _make_workspace(db_session)
        participants = ["alice@example.com", "charlie@example.com", "dave@example.com"]
        meeting = await _make_meeting(db_session, ws.id, participants=participants)
        await db_session.commit()
        await db_session.refresh(meeting)

        assert meeting.participants == participants
        assert len(meeting.participants) == 3


# ── ActionItem ────────────────────────────────────────────────────────────────

class TestActionItem:
    async def test_create_action_item(self, db_session):
        ws = await _make_workspace(db_session)
        meeting = await _make_meeting(db_session, ws.id, title="Sprint Review")

        item = ActionItem(
            workspace_id=ws.id,
            meeting_id=meeting.id,
            description="Write test plan",
            owner="alice@example.com",
            status="pending",
        )
        db_session.add(item)
        await db_session.commit()
        await db_session.refresh(item)

        assert item.id is not None
        assert item.meeting_id == meeting.id
        assert item.workspace_id == ws.id
        assert item.description == "Write test plan"
        assert item.owner == "alice@example.com"

    async def test_action_item_without_meeting(self, db_session):
        ws = await _make_workspace(db_session)
        item = ActionItem(
            workspace_id=ws.id,
            description="Standalone task — no meeting",
        )
        db_session.add(item)
        await db_session.commit()
        await db_session.refresh(item)

        assert item.meeting_id is None
        assert item.description == "Standalone task — no meeting"

    async def test_action_item_meeting_cascade(self, db_session):
        ws = await _make_workspace(db_session)
        meeting = await _make_meeting(db_session, ws.id, title="Retro")
        item = ActionItem(
            workspace_id=ws.id,
            meeting_id=meeting.id,
            description="Follow up on bugs",
        )
        db_session.add(item)
        await db_session.flush()
        item_id = item.id

        await db_session.delete(meeting)
        await db_session.commit()
        db_session.expire_all()

        result = await db_session.get(ActionItem, item_id)
        assert result is None

    async def test_action_item_status_values(self, db_session):
        ws = await _make_workspace(db_session)
        for status in ("pending", "done", "overdue"):
            item = ActionItem(
                workspace_id=ws.id,
                description=f"Task with status {status}",
                status=status,
            )
            db_session.add(item)
        await db_session.commit()

        rows = await db_session.execute(
            select(ActionItem).where(ActionItem.workspace_id == ws.id)
        )
        items = rows.scalars().all()
        statuses = {i.status for i in items}
        assert statuses == {"pending", "done", "overdue"}

    async def test_action_item_link(self, db_session):
        ws = await _make_workspace(db_session)
        item_a = ActionItem(workspace_id=ws.id, description="Parent task")
        db_session.add(item_a)
        await db_session.flush()

        item_b = ActionItem(
            workspace_id=ws.id,
            description="Linked task",
            linked_item_id=item_a.id,
        )
        db_session.add(item_b)
        await db_session.commit()
        await db_session.refresh(item_b)

        assert item_b.linked_item_id == item_a.id


# ── Note ──────────────────────────────────────────────────────────────────────

class TestNote:
    async def test_create_note(self, db_session):
        ws = await _make_workspace(db_session)
        note = Note(
            workspace_id=ws.id,
            title="Meeting Prep",
            content="Review the agenda before Monday.",
            tags=["work", "meetings"],
        )
        db_session.add(note)
        await db_session.commit()
        await db_session.refresh(note)

        assert note.id is not None
        assert note.title == "Meeting Prep"
        assert note.content == "Review the agenda before Monday."
        assert note.tags == ["work", "meetings"]

    async def test_note_workspace_cascade(self, db_session):
        ws = await _make_workspace(db_session)
        note = Note(workspace_id=ws.id, title="Ephemeral note", content="Gone soon.")
        db_session.add(note)
        await db_session.flush()
        note_id = note.id

        await db_session.delete(ws)
        await db_session.commit()
        db_session.expire_all()

        result = await db_session.get(Note, note_id)
        assert result is None

    async def test_note_tags_jsonb(self, db_session):
        ws = await _make_workspace(db_session)
        tags = ["python", "sqlalchemy", "testing", "backend"]
        note = Note(workspace_id=ws.id, title="Tech notes", tags=tags)
        db_session.add(note)
        await db_session.commit()
        await db_session.refresh(note)

        assert note.tags == tags
        assert len(note.tags) == 4

    async def test_note_update(self, db_session):
        ws = await _make_workspace(db_session)
        note = Note(workspace_id=ws.id, title="Original", content="First draft.")
        db_session.add(note)
        await db_session.flush()
        original_created = note.created_at

        note.title = "Revised"
        note.content = "Second draft with more detail."
        await db_session.commit()
        await db_session.refresh(note)

        assert note.title == "Revised"
        assert note.content == "Second draft with more detail."
        # created_at must not change; updated_at is server-managed (may equal created_at in same txn)
        assert note.created_at == original_created


# ── GoogleAccount ─────────────────────────────────────────────────────────────

class TestGoogleAccount:
    async def test_create_google_account(self, db_session):
        acct = GoogleAccount(
            email="user@gmail.com",
            refresh_token_encrypted=b"\xde\xad\xbe\xef",
            scopes=[
                "https://www.googleapis.com/auth/calendar",
                "https://www.googleapis.com/auth/gmail.readonly",
            ],
            label="Personal Gmail",
        )
        db_session.add(acct)
        await db_session.commit()
        await db_session.refresh(acct)

        assert acct.id is not None
        assert acct.email == "user@gmail.com"
        assert acct.refresh_token_encrypted == b"\xde\xad\xbe\xef"
        assert len(acct.scopes) == 2
        assert acct.label == "Personal Gmail"

    async def test_google_account_unique_email(self, db_session):
        email = f"unique-{uuid.uuid4().hex[:8]}@example.com"
        acct1 = GoogleAccount(email=email, refresh_token_encrypted=b"token-1")
        acct2 = GoogleAccount(email=email, refresh_token_encrypted=b"token-2")
        db_session.add(acct1)
        await db_session.flush()

        db_session.add(acct2)
        with pytest.raises(IntegrityError):
            await db_session.flush()

        await db_session.rollback()


# ── CalendarEvent ─────────────────────────────────────────────────────────────

class TestCalendarEvent:
    async def test_create_calendar_event(self, db_session):
        acct = await _make_google_account(db_session)
        start = datetime(2026, 4, 7, 9, 0, tzinfo=timezone.utc)
        end = datetime(2026, 4, 7, 10, 0, tzinfo=timezone.utc)

        event = CalendarEvent(
            google_account_id=acct.id,
            google_event_id="google_evt_xyz",
            title="Weekly Sync",
            start_time=start,
            end_time=end,
            attendees=["alice@example.com", "bob@example.com"],
            meet_link="https://meet.google.com/abc-defg-hij",
        )
        db_session.add(event)
        await db_session.commit()
        await db_session.refresh(event)

        assert event.id is not None
        assert event.google_event_id == "google_evt_xyz"
        assert event.title == "Weekly Sync"
        assert event.start_time == start
        assert event.end_time == end
        assert event.attendees == ["alice@example.com", "bob@example.com"]
        assert event.meet_link == "https://meet.google.com/abc-defg-hij"

    async def test_calendar_event_account_cascade(self, db_session):
        acct = await _make_google_account(db_session)
        start = datetime(2026, 4, 8, 14, 0, tzinfo=timezone.utc)
        end = datetime(2026, 4, 8, 15, 0, tzinfo=timezone.utc)

        event = CalendarEvent(
            google_account_id=acct.id,
            google_event_id="evt_to_cascade",
            title="Cascade Test",
            start_time=start,
            end_time=end,
        )
        db_session.add(event)
        await db_session.flush()
        event_id = event.id

        await db_session.delete(acct)
        await db_session.commit()
        db_session.expire_all()

        result = await db_session.get(CalendarEvent, event_id)
        assert result is None

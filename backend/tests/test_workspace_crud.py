"""Tests for workspace CRUD operations and chat history — PostgreSQL backend."""
import pytest
import pytest_asyncio

from core.memory.chat_history import ChatManager, CrossWorkspaceChatManager, ChatSession
from core.workspace import create_workspace, workspace_exists


@pytest.fixture()
def _ensure_workspace(db_session):
    """Ensure test workspaces exist in DB for chat tests."""
    import asyncio

    async def _create():
        for name in ("personal", "ws1", "ws2"):
            if not await workspace_exists(db_session, name):
                await create_workspace(db_session, name)

    asyncio.get_event_loop().run_until_complete(_create())


class TestChatSession:
    @pytest.mark.asyncio
    async def test_create_and_append(self, db_session):
        if not await workspace_exists(db_session, "personal"):
            await create_workspace(db_session, "personal")

        mgr = ChatManager("personal")
        cs = await mgr.create_session(db_session, title="Test Chat")
        msg = await cs.append(db_session, "user", "Hello!")
        assert msg["role"] == "user"
        assert msg["content"] == "Hello!"
        assert len(cs) == 1

    @pytest.mark.asyncio
    async def test_messages_persisted_immediately(self, db_session):
        if not await workspace_exists(db_session, "personal"):
            await create_workspace(db_session, "personal")

        mgr = ChatManager("personal")
        cs = await mgr.create_session(db_session, title="Persist Test")
        await cs.append(db_session, "user", "First message")
        await cs.append(db_session, "assistant", "Response here")

        loaded = await ChatSession.load(db_session, cs.session_id, "personal")
        assert loaded is not None
        assert len(loaded) == 2
        assert loaded.messages[0]["content"] == "First message"

    @pytest.mark.asyncio
    async def test_load_nonexistent_returns_none(self, db_session):
        if not await workspace_exists(db_session, "personal"):
            await create_workspace(db_session, "personal")

        result = await ChatSession.load(db_session, "00000000-0000-0000-0000-000000000000", "personal")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_context_messages_limits(self, db_session):
        if not await workspace_exists(db_session, "personal"):
            await create_workspace(db_session, "personal")

        mgr = ChatManager("personal")
        cs = await mgr.create_session(db_session, title="Context Test")
        for i in range(30):
            await cs.append(db_session, "user", f"Message {i}")

        context = cs.get_context_messages(max_recent=10)
        assert len(context) == 10
        assert context[-1]["content"] == "Message 29"


class TestChatManager:
    @pytest.mark.asyncio
    async def test_create_session(self, db_session):
        if not await workspace_exists(db_session, "personal"):
            await create_workspace(db_session, "personal")

        mgr = ChatManager("personal")
        cs = await mgr.create_session(db_session, title="My Chat")
        assert cs.title == "My Chat"
        assert cs.workspace == "personal"

    @pytest.mark.asyncio
    async def test_list_sessions(self, db_session):
        if not await workspace_exists(db_session, "personal"):
            await create_workspace(db_session, "personal")

        mgr = ChatManager("personal")
        s1 = await mgr.create_session(db_session, title="First")
        await s1.append(db_session, "user", "Hello")

        s2 = await mgr.create_session(db_session, title="Second")
        await s2.append(db_session, "user", "World")

        sessions = await mgr.list_sessions(db_session)
        assert len(sessions) >= 2
        titles = [s["title"] for s in sessions]
        assert "First" in titles
        assert "Second" in titles

    @pytest.mark.asyncio
    async def test_search_finds_content(self, db_session):
        if not await workspace_exists(db_session, "personal"):
            await create_workspace(db_session, "personal")

        mgr = ChatManager("personal")
        cs = await mgr.create_session(db_session, title="Search Test")
        await cs.append(db_session, "user", "I need to buy groceries today")
        await cs.append(db_session, "assistant", "I can help you make a shopping list")

        results = await mgr.search(db_session, "groceries")
        assert len(results) >= 1
        assert any("groceries" in r["content"].lower() for r in results)

    @pytest.mark.asyncio
    async def test_search_no_results(self, db_session):
        if not await workspace_exists(db_session, "personal"):
            await create_workspace(db_session, "personal")

        mgr = ChatManager("personal")
        cs = await mgr.create_session(db_session)
        await cs.append(db_session, "user", "Hello world")

        results = await mgr.search(db_session, "xyznonexistentterm123")
        assert results == []

    @pytest.mark.asyncio
    async def test_delete_session(self, db_session):
        if not await workspace_exists(db_session, "personal"):
            await create_workspace(db_session, "personal")

        mgr = ChatManager("personal")
        cs = await mgr.create_session(db_session, title="To Delete")
        session_id = cs.session_id

        loaded = await mgr.get_session(db_session, session_id)
        assert loaded is not None
        await mgr.delete_session(db_session, session_id)
        loaded = await mgr.get_session(db_session, session_id)
        assert loaded is None

    @pytest.mark.asyncio
    async def test_get_nonexistent_session(self, db_session):
        if not await workspace_exists(db_session, "personal"):
            await create_workspace(db_session, "personal")

        mgr = ChatManager("personal")
        result = await mgr.get_session(db_session, "00000000-0000-0000-0000-000000000000")
        assert result is None


class TestCrossWorkspaceSearch:
    @pytest.mark.asyncio
    async def test_search_across_workspaces(self, db_session):
        for name in ("ws1", "ws2"):
            if not await workspace_exists(db_session, name):
                await create_workspace(db_session, name)

        m1 = ChatManager("ws1")
        s1 = await m1.create_session(db_session, title="WS1 Chat")
        await s1.append(db_session, "user", "unique_search_term_abc")

        m2 = ChatManager("ws2")
        s2 = await m2.create_session(db_session, title="WS2 Chat")
        await s2.append(db_session, "user", "unique_search_term_abc")

        results = await CrossWorkspaceChatManager.search_all(db_session, "unique_search_term_abc")
        assert len(results) >= 2
        workspaces_found = {r["workspace"] for r in results}
        assert "ws1" in workspaces_found
        assert "ws2" in workspaces_found

    @pytest.mark.asyncio
    async def test_list_all_workspaces(self, db_session):
        for name in ("alpha", "beta"):
            if not await workspace_exists(db_session, name):
                await create_workspace(db_session, name)

        workspaces = await CrossWorkspaceChatManager.list_all_workspaces(db_session)
        assert "alpha" in workspaces
        assert "beta" in workspaces

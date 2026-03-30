"""Tests for workspace CRUD operations and chat history."""
import json
import os
import pytest

from core.memory.chat_history import ChatManager, CrossWorkspaceChatManager, ChatSession


class TestChatSession:
    def test_create_and_append(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        os.makedirs("workspaces/test/chat_history", exist_ok=True)

        session = ChatSession(session_id="abc123", workspace="test", title="Test Chat")
        msg = session.append("user", "Hello!")
        assert msg["role"] == "user"
        assert msg["content"] == "Hello!"
        assert len(session) == 1

    def test_messages_persisted_immediately(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        os.makedirs("workspaces/test/chat_history", exist_ok=True)

        session = ChatSession(session_id="persist123", workspace="test")
        session.append("user", "First message")
        session.append("assistant", "Response here")

        # Load fresh from disk
        loaded = ChatSession.load("persist123", "test")
        assert loaded is not None
        assert len(loaded) == 2
        assert loaded.messages[0]["content"] == "First message"

    def test_load_nonexistent_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        os.makedirs("workspaces/test/chat_history", exist_ok=True)
        result = ChatSession.load("nonexistent", "test")
        assert result is None

    def test_get_context_messages_limits(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        os.makedirs("workspaces/test/chat_history", exist_ok=True)

        session = ChatSession(session_id="ctx123", workspace="test")
        for i in range(30):
            session.append("user", f"Message {i}")

        context = session.get_context_messages(max_recent=10)
        assert len(context) == 10
        assert context[-1]["content"] == "Message 29"


class TestChatManager:
    def test_create_session(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        os.makedirs("workspaces/personal/chat_history", exist_ok=True)

        manager = ChatManager("personal")
        session = manager.create_session(title="My Chat")
        assert session.title == "My Chat"
        assert session.workspace == "personal"

    def test_list_sessions(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        os.makedirs("workspaces/personal/chat_history", exist_ok=True)

        manager = ChatManager("personal")
        s1 = manager.create_session(title="First")
        s1.append("user", "Hello")
        manager.update_session_index(s1)

        s2 = manager.create_session(title="Second")
        s2.append("user", "World")
        manager.update_session_index(s2)

        sessions = manager.list_sessions()
        assert len(sessions) >= 2
        titles = [s["title"] for s in sessions]
        assert "First" in titles
        assert "Second" in titles

    def test_search_finds_content(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        os.makedirs("workspaces/personal/chat_history", exist_ok=True)

        manager = ChatManager("personal")
        session = manager.create_session(title="Search Test")
        session.append("user", "I need to buy groceries today")
        session.append("assistant", "I can help you make a shopping list")
        manager.update_session_index(session)

        results = manager.search("groceries")
        assert len(results) >= 1
        assert any("groceries" in r["content"].lower() for r in results)

    def test_search_no_results(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        os.makedirs("workspaces/personal/chat_history", exist_ok=True)

        manager = ChatManager("personal")
        session = manager.create_session()
        session.append("user", "Hello world")
        manager.update_session_index(session)

        results = manager.search("xyznonexistentterm123")
        assert results == []

    def test_delete_session(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        os.makedirs("workspaces/personal/chat_history", exist_ok=True)

        manager = ChatManager("personal")
        session = manager.create_session(title="To Delete")
        session_id = session.session_id

        assert manager.get_session(session_id) is not None
        manager.delete_session(session_id)
        assert manager.get_session(session_id) is None

    def test_get_nonexistent_session(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        os.makedirs("workspaces/personal/chat_history", exist_ok=True)

        manager = ChatManager("personal")
        result = manager.get_session("does-not-exist")
        assert result is None


class TestCrossWorkspaceSearch:
    def test_search_across_workspaces(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        os.makedirs("workspaces/ws1/chat_history", exist_ok=True)
        os.makedirs("workspaces/ws2/chat_history", exist_ok=True)

        m1 = ChatManager("ws1")
        s1 = m1.create_session(title="WS1 Chat")
        s1.append("user", "unique_search_term_abc")
        m1.update_session_index(s1)

        m2 = ChatManager("ws2")
        s2 = m2.create_session(title="WS2 Chat")
        s2.append("user", "unique_search_term_abc")
        m2.update_session_index(s2)

        results = CrossWorkspaceChatManager.search_all("unique_search_term_abc")
        assert len(results) >= 2
        workspaces_found = {r["workspace"] for r in results}
        assert "ws1" in workspaces_found
        assert "ws2" in workspaces_found

    def test_list_all_workspaces(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        os.makedirs("workspaces/alpha/chat_history", exist_ok=True)
        os.makedirs("workspaces/beta/chat_history", exist_ok=True)

        workspaces = CrossWorkspaceChatManager.list_all_workspaces()
        assert "alpha" in workspaces
        assert "beta" in workspaces


# Keep the original workspace CLI tests
class TestWorkspaceCLI:
    def test_workspace_list(self, test_workspace):
        from cli import cmd_workspace_list
        cmd_workspace_list()

    def test_workspace_create(self, test_workspace):
        from cli import cmd_workspace_create
        cmd_workspace_create("b5-test-workspace")
        config_path = "workspaces/b5-test-workspace/config.json"
        assert os.path.exists(config_path)
        with open(config_path) as f:
            cfg = json.load(f)
        assert cfg["name"] == "b5-test-workspace"

    def test_workspace_create_invalid_name(self, test_workspace):
        from cli import cmd_workspace_create
        with pytest.raises(SystemExit):
            cmd_workspace_create("invalid name!")

    def test_workspace_create_duplicate(self, test_workspace):
        from cli import cmd_workspace_create
        cmd_workspace_create("b5-unique-ws")
        with pytest.raises(SystemExit):
            cmd_workspace_create("b5-unique-ws")

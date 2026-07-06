from fastapi.testclient import TestClient

from app.api import chat as chat_api
from app.main import app
from app.profile.agent import run_cold_start as run_profile_cold_start
from app.profile.store import get_profile, list_chat_messages
from tests.helpers import auth_session


def test_chat_stream_returns_sse_events(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / ".data" / "chat_smoke.db"
    monkeypatch.setattr(chat_api.profile_storage, "DB_PATH", db_path)

    client = TestClient(app)
    session = auth_session(client, "chat_smoke_user", db_path)
    response = client.post("/api/chat/stream", json={"question": "麻黄的功效是什么？"}, headers=session["headers"])

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    body = response.text
    assert "event: node_start" in body
    assert '"node":"rag_agent"' in body
    assert "event: token" in body
    assert "event: done" in body
    assert '"sources":' in body


def test_chat_stream_profile_setup_uses_request_user_id(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / ".data" / "chat_profile.db"

    async def run_cold_start_in_tmp(*args, **kwargs):
        kwargs["db_path"] = db_path
        return await run_profile_cold_start(*args, **kwargs)

    monkeypatch.setattr(chat_api.profile_storage, "DB_PATH", db_path)
    monkeypatch.setattr("app.agents.graph.run_cold_start", run_cold_start_in_tmp)

    client = TestClient(app)
    session = auth_session(client, "profile_user_from_chat", db_path)
    response = client.post(
        "/api/chat/stream",
        json={
            "question": "请根据我的学习目标和基础建立画像，我是中医学专业学生。",
            "session_id": "profile_session",
        },
        headers=session["headers"],
    )

    assert response.status_code == 200
    assert '"node":"profile_agent"' in response.text
    assert f'"user_id":"{session["user"]["user_id"]}"' in response.text
    assert '"session_id":"profile_session"' in response.text
    assert get_profile(session["user"]["user_id"], db_path=db_path).user_id == session["user"]["user_id"]


def test_chat_stream_reuses_history_for_same_session(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / ".data" / "chat_history.db"
    captured_lengths: list[int] = []

    async def fake_stream_agent_events(question, **kwargs):
        captured_lengths.append(len(kwargs["history_messages"]))
        yield {"event": "node_start", "data": {"node": "router"}}
        yield {"event": "token", "data": {"text": f"答复:{question}"}}
        yield {"event": "done", "data": {"sources": []}}

    monkeypatch.setattr(chat_api.profile_storage, "DB_PATH", db_path)
    monkeypatch.setattr(chat_api, "stream_agent_events", fake_stream_agent_events)

    client = TestClient(app)
    session = auth_session(client, "history_user", db_path)
    payload = {"session_id": "same_session"}
    first = client.post("/api/chat/stream", json={**payload, "question": "第一轮"}, headers=session["headers"])
    second = client.post("/api/chat/stream", json={**payload, "question": "第二轮"}, headers=session["headers"])

    assert first.status_code == 200
    assert second.status_code == 200
    assert captured_lengths == [0, 2]
    messages = list_chat_messages(session["user"]["user_id"], "same_session", db_path=db_path)
    assert [message["role"] for message in messages] == ["user", "assistant", "user", "assistant"]

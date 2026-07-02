from fastapi.testclient import TestClient

from app.main import app


def test_chat_stream_returns_sse_events() -> None:
    client = TestClient(app)
    response = client.post("/api/chat/stream", json={"question": "麻黄的功效是什么？"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    body = response.text
    assert "event: node_start" in body
    assert '"node":"rag_agent"' in body
    assert "event: token" in body
    assert "event: done" in body
    assert '"sources":' in body

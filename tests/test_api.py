from fastapi.testclient import TestClient

from backend.main import app


def test_health_check(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.database.DATABASE_PATH", tmp_path / "test.db")
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_chat_persists_messages(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.database.DATABASE_PATH", tmp_path / "test.db")
    monkeypatch.setattr("backend.main.generate_ai_reply", lambda _: "I can help with that.")
    with TestClient(app) as client:
        response = client.post("/api/chat", json={"message": "I need help"})
        stats = client.get("/api/dashboard")
    assert response.status_code == 200
    assert response.json()["reply"] == "I can help with that."
    assert response.json()["conversation_id"]
    assert stats.json()["conversations"] == 1
    assert stats.json()["ai_resolved"] == 1


def test_chat_rejects_blank_message(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.database.DATABASE_PATH", tmp_path / "test.db")
    with TestClient(app) as client:
        response = client.post("/api/chat", json={"message": "   "})
    assert response.status_code == 422


def test_chat_explains_missing_api_key(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.database.DATABASE_PATH", tmp_path / "test.db")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with TestClient(app) as client:
        response = client.post("/api/chat", json={"message": "Can you help me?"})
    assert response.status_code == 503
    assert "OPENAI_API_KEY" in response.json()["detail"]

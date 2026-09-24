from fastapi.testclient import TestClient

from backend.main import app


def register(client: TestClient, email: str = "alex@example.com") -> dict:
    response = client.post("/api/auth/register", json={"name": "Alex Morgan", "email": email, "password": "safe-password-42"})
    assert response.status_code == 200
    return response.json()


def test_health_check(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.database.DATABASE_PATH", tmp_path / "test.db")
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}


def test_registration_and_session_profile(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.database.DATABASE_PATH", tmp_path / "test.db")
    with TestClient(app) as client:
        user = register(client)
        profile = client.get("/api/me")
    assert profile.status_code == 200
    assert profile.json()["email"] == user["email"]


def test_chat_persists_messages_for_signed_in_user(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.database.DATABASE_PATH", tmp_path / "test.db")
    monkeypatch.setattr("backend.main.route_conversation", lambda history: ({"scenario_id": "SC30", "confidence": 92, "reason": "Payment issue", "language": "ru", "reply_language": "ru", "alternative_ids": ["SC31"], "missing_slots": [], "topic_switched": False}, 220))
    monkeypatch.setattr("backend.main.generate_ai_reply", lambda history, user, scenario: "Чем могу помочь по страховке?")
    with TestClient(app) as client:
        register(client)
        response = client.post("/api/chat", json={"message": "Who am I?"})
        stats = client.get("/api/dashboard")
        history = client.get(f"/api/conversations/{response.json()['conversation_id']}/messages")
        trace = client.get(f"/api/conversations/{response.json()['conversation_id']}/trace")
        analytics = client.get("/api/analytics")
        feedback = client.post(f"/api/responses/{response.json()['assistant_message_id']}/feedback", json={"helpful": True})
    assert response.status_code == 200
    assert response.json()["reply"]
    assert response.json()["route"]["scenario_id"] == "SC30"
    assert stats.json()["conversations"] == 1
    assert [item["role"] for item in history.json()] == ["user", "assistant"]
    assert trace.json()["status"] == "completed"
    assert len(trace.json()["steps"]) == 4
    assert analytics.json()["summary"]["responses"] == 1
    assert feedback.status_code == 200


def test_chat_requires_authentication(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.database.DATABASE_PATH", tmp_path / "test.db")
    with TestClient(app) as client:
        response = client.post("/api/chat", json={"message": "Hello"})
    assert response.status_code == 401


def test_voice_output_requires_authentication(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.database.DATABASE_PATH", tmp_path / "test.db")
    with TestClient(app) as client:
        response = client.post("/api/speech", json={"text": "Hello"})
    assert response.status_code == 401

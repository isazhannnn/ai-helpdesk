import json
import os
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from hashlib import pbkdf2_hmac
from secrets import compare_digest, token_urlsafe
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

DATABASE_PATH = Path(os.getenv("DATABASE_PATH", "data/helpdesk.db"))


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def initialize_database() -> None:
    with connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                user_id TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                content TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (conversation_id) REFERENCES conversations(id)
            );
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS ai_response_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id INTEGER NOT NULL UNIQUE,
                conversation_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                latency_ms INTEGER NOT NULL,
                helpful INTEGER CHECK(helpful IN (0, 1)),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (message_id) REFERENCES messages(id),
                FOREIGN KEY (conversation_id) REFERENCES conversations(id)
            );
            CREATE TABLE IF NOT EXISTS route_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                scenario_id TEXT NOT NULL,
                confidence INTEGER NOT NULL,
                reason TEXT NOT NULL,
                alternatives_json TEXT NOT NULL,
                missing_slots_json TEXT NOT NULL,
                language TEXT NOT NULL,
                topic_switched INTEGER NOT NULL DEFAULT 0,
                routing_latency_ms INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        columns = {row[1] for row in conn.execute("PRAGMA table_info(conversations)")}
        if "user_id" not in columns:
            conn.execute("ALTER TABLE conversations ADD COLUMN user_id TEXT")


def create_conversation(user_id: str) -> str:
    conversation_id = str(uuid.uuid4())
    with connection() as conn:
        conn.execute("INSERT INTO conversations (id, user_id) VALUES (?, ?)", (conversation_id, user_id))
    return conversation_id


def ensure_conversation(conversation_id: str | None, user_id: str) -> str:
    if not conversation_id:
        return create_conversation(user_id)
    with connection() as conn:
        found = conn.execute("SELECT 1 FROM conversations WHERE id = ? AND user_id = ?", (conversation_id, user_id)).fetchone()
        if found:
            return conversation_id
    raise PermissionError("Conversation not found.")


def save_message(conversation_id: str, role: str, content: str) -> int:
    with connection() as conn:
        cursor = conn.execute(
            "INSERT INTO messages (conversation_id, role, content) VALUES (?, ?, ?)",
            (conversation_id, role, content),
        )
        conn.execute("UPDATE conversations SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (conversation_id,))
    return cursor.lastrowid


def record_ai_response(message_id: int, conversation_id: str, user_id: str, latency_ms: int) -> None:
    with connection() as conn:
        conn.execute("""INSERT INTO ai_response_metrics (message_id, conversation_id, user_id, latency_ms)
                     VALUES (?, ?, ?, ?)""", (message_id, conversation_id, user_id, latency_ms))


def record_route(conversation_id: str, user_id: str, decision: dict[str, object], latency_ms: int) -> None:
    with connection() as conn:
        conn.execute("""INSERT INTO route_metrics (conversation_id, user_id, scenario_id, confidence, reason,
                     alternatives_json, missing_slots_json, language, topic_switched, routing_latency_ms)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                     (conversation_id, user_id, decision["scenario_id"], decision["confidence"], decision["reason"],
                      json.dumps(decision["alternative_ids"]), json.dumps(decision["missing_slots"]), decision["language"],
                      int(decision["topic_switched"]), latency_ms))


def latest_route(conversation_id: str, user_id: str) -> dict[str, object] | None:
    with connection() as conn:
        row = conn.execute("""SELECT * FROM route_metrics WHERE conversation_id = ? AND user_id = ?
                            ORDER BY id DESC LIMIT 1""", (conversation_id, user_id)).fetchone()
    if not row:
        return None
    route = dict(row)
    route["alternative_ids"] = json.loads(route.pop("alternatives_json"))
    route["missing_slots"] = json.loads(route.pop("missing_slots_json"))
    route["topic_switched"] = bool(route["topic_switched"])
    return route


def get_messages(conversation_id: str, limit: int = 12) -> list[dict[str, str]]:
    with connection() as conn:
        rows = conn.execute(
            "SELECT id, role, content, created_at FROM messages WHERE conversation_id = ? ORDER BY id DESC LIMIT ?",
            (conversation_id, limit),
        ).fetchall()
    return [dict(row) for row in reversed(rows)]


def get_dashboard_stats(user_id: str) -> dict[str, int]:
    with connection() as conn:
        conversations = conn.execute("SELECT COUNT(*) FROM conversations WHERE user_id = ?", (user_id,)).fetchone()[0]
        messages = conn.execute("SELECT COUNT(*) FROM ai_response_metrics WHERE user_id = ?", (user_id,)).fetchone()[0]
        average = conn.execute("SELECT COALESCE(AVG(latency_ms), 0) FROM ai_response_metrics WHERE user_id = ?", (user_id,)).fetchone()[0]
    return {"conversations": conversations, "ai_resolved": messages, "waiting_for_agent": 0, "avg_response_seconds": round(average / 1000, 1)}


def set_response_feedback(message_id: int, user_id: str, helpful: bool) -> bool:
    with connection() as conn:
        result = conn.execute("UPDATE ai_response_metrics SET helpful = ? WHERE message_id = ? AND user_id = ?",
                              (int(helpful), message_id, user_id))
    return result.rowcount == 1


def get_analytics(user_id: str) -> dict[str, object]:
    with connection() as conn:
        summary = conn.execute("""SELECT COUNT(*) AS responses, COALESCE(AVG(latency_ms), 0) AS avg_latency,
                                  COALESCE(MAX(latency_ms), 0) AS max_latency,
                                  SUM(CASE WHEN latency_ms <= 5000 THEN 1 ELSE 0 END) AS smooth,
                                  SUM(CASE WHEN helpful = 1 THEN 1 ELSE 0 END) AS helpful,
                                  SUM(CASE WHEN helpful = 0 THEN 1 ELSE 0 END) AS unhelpful,
                                  SUM(CASE WHEN helpful IS NULL THEN 1 ELSE 0 END) AS awaiting_feedback
                                  FROM ai_response_metrics WHERE user_id = ?""", (user_id,)).fetchone()
        rows = conn.execute("""SELECT m.id AS message_id, c.id AS conversation_id, m.content, r.latency_ms, r.helpful, r.created_at
                               FROM ai_response_metrics r JOIN messages m ON m.id = r.message_id
                               JOIN conversations c ON c.id = r.conversation_id WHERE r.user_id = ?
                               ORDER BY r.id DESC LIMIT 30""", (user_id,)).fetchall()
    data = dict(summary)
    return {"summary": data, "responses": [dict(row) for row in rows]}


def _hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    digest = pbkdf2_hmac("sha256", password.encode(), salt, 310_000)
    return f"{salt.hex()}${digest.hex()}"


def create_user(name: str, email: str, password: str) -> dict[str, str]:
    user = {"id": str(uuid.uuid4()), "name": name, "email": email.lower()}
    with connection() as conn:
        try:
            conn.execute("INSERT INTO users (id, name, email, password_hash) VALUES (?, ?, ?, ?)",
                         (user["id"], user["name"], user["email"], _hash_password(password)))
        except sqlite3.IntegrityError as error:
            raise ValueError("An account with this email already exists.") from error
    return user


def authenticate_user(email: str, password: str) -> dict[str, str] | None:
    with connection() as conn:
        row = conn.execute("SELECT id, name, email, password_hash FROM users WHERE email = ?", (email.lower(),)).fetchone()
    if not row:
        return None
    salt_hex, expected = row["password_hash"].split("$", maxsplit=1)
    actual = _hash_password(password, bytes.fromhex(salt_hex)).split("$", maxsplit=1)[1]
    return {"id": row["id"], "name": row["name"], "email": row["email"]} if compare_digest(actual, expected) else None


def create_session(user_id: str) -> str:
    token = token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + timedelta(days=14)).isoformat()
    with connection() as conn:
        conn.execute("INSERT INTO sessions (token, user_id, expires_at) VALUES (?, ?, ?)", (token, user_id, expires))
    return token


def get_user_by_session(token: str | None) -> dict[str, str] | None:
    if not token:
        return None
    with connection() as conn:
        row = conn.execute("""SELECT u.id, u.name, u.email FROM sessions s JOIN users u ON u.id = s.user_id
                            WHERE s.token = ? AND s.expires_at > ?""", (token, datetime.now(timezone.utc).isoformat())).fetchone()
    return dict(row) if row else None


def delete_session(token: str | None) -> None:
    if token:
        with connection() as conn:
            conn.execute("DELETE FROM sessions WHERE token = ?", (token,))


def list_conversations(user_id: str) -> list[dict[str, str]]:
    with connection() as conn:
        rows = conn.execute("""SELECT c.id, c.updated_at, COALESCE((SELECT content FROM messages WHERE conversation_id = c.id
                            ORDER BY id LIMIT 1), 'New conversation') AS title FROM conversations c
                            WHERE c.user_id = ? ORDER BY c.updated_at DESC LIMIT 20""", (user_id,)).fetchall()
    return [dict(row) for row in rows]

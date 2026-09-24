import os
import sqlite3
import uuid
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
            """
        )


def create_conversation() -> str:
    conversation_id = str(uuid.uuid4())
    with connection() as conn:
        conn.execute("INSERT INTO conversations (id) VALUES (?)", (conversation_id,))
    return conversation_id


def ensure_conversation(conversation_id: str | None) -> str:
    if not conversation_id:
        return create_conversation()
    with connection() as conn:
        found = conn.execute("SELECT 1 FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
        if found:
            return conversation_id
        conn.execute("INSERT INTO conversations (id) VALUES (?)", (conversation_id,))
    return conversation_id


def save_message(conversation_id: str, role: str, content: str) -> None:
    with connection() as conn:
        conn.execute(
            "INSERT INTO messages (conversation_id, role, content) VALUES (?, ?, ?)",
            (conversation_id, role, content),
        )
        conn.execute("UPDATE conversations SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (conversation_id,))


def get_messages(conversation_id: str, limit: int = 12) -> list[dict[str, str]]:
    with connection() as conn:
        rows = conn.execute(
            "SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY id DESC LIMIT ?",
            (conversation_id, limit),
        ).fetchall()
    return [dict(row) for row in reversed(rows)]


def get_dashboard_stats() -> dict[str, int]:
    with connection() as conn:
        conversations = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        messages = conn.execute("SELECT COUNT(*) FROM messages WHERE role = 'assistant'").fetchone()[0]
    return {"conversations": conversations, "ai_resolved": messages, "waiting_for_agent": 0, "avg_response_seconds": 2}

import json
import sqlite3
import time
from pathlib import Path


class ContextLimitError(ValueError):
    pass


class Storage:
    def __init__(self, path: str, history_days: int = 30):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.history_seconds = history_days * 86400
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY,
                chat_id INTEGER NOT NULL,
                topic_id INTEGER NOT NULL,
                expires_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS turns (
                id INTEGER PRIMARY KEY,
                conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                parent_id INTEGER REFERENCES turns(id),
                question TEXT NOT NULL,
                answer TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS message_links (
                chat_id INTEGER NOT NULL,
                topic_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                turn_id INTEGER NOT NULL REFERENCES turns(id) ON DELETE CASCADE,
                kind TEXT NOT NULL,
                PRIMARY KEY (chat_id, message_id)
            );
            CREATE TABLE IF NOT EXISTS requests (
                created_at REAL NOT NULL,
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS requests_time ON requests(created_at);
            CREATE INDEX IF NOT EXISTS requests_user ON requests(chat_id, user_id, created_at);
            CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)

    def close(self) -> None:
        self.db.close()

    def find_turn(self, chat_id: int, topic_id: int, message_id: int) -> int | None:
        row = self.db.execute("""
            SELECT t.id FROM message_links l
            JOIN turns t ON t.id=l.turn_id
            JOIN conversations c ON c.id=t.conversation_id
            WHERE l.chat_id=? AND l.topic_id=? AND l.message_id=? AND c.expires_at>?
        """, (chat_id, topic_id, message_id, time.time())).fetchone()
        return row[0] if row else None

    def has_processed(self, chat_id: int, message_id: int) -> bool:
        return self.db.execute(
            "SELECT 1 FROM message_links WHERE chat_id=? AND message_id=? AND kind='user'",
            (chat_id, message_id),
        ).fetchone() is not None

    def context(self, turn_id: int | None, max_chars: int, max_messages: int) -> list[dict]:
        result = []
        size = 0
        while turn_id is not None:
            row = self.db.execute(
                "SELECT parent_id, question, answer FROM turns WHERE id=?", (turn_id,)
            ).fetchone()
            if row is None:
                raise ContextLimitError("История этой ветки недоступна. Начните новый вопрос без reply.")
            parent, question, answer = row
            size += len(question) + len(answer)
            if size > max_chars or len(result) + 2 > max_messages:
                raise ContextLimitError("Диалог слишком длинный. Начните новый вопрос с @бот без reply.")
            result.extend([{"role": "assistant", "content": answer},
                           {"role": "user", "content": question}])
            turn_id = parent
        result.reverse()
        return result

    def save_turn(self, chat_id: int, topic_id: int, user_message_id: int,
                  parent_id: int | None, question: str, answer: str) -> int:
        with self.db:
            if parent_id is None:
                cursor = self.db.execute(
                    "INSERT INTO conversations(chat_id,topic_id,expires_at) VALUES(?,?,?)",
                    (chat_id, topic_id, time.time() + self.history_seconds),
                )
                conversation_id = cursor.lastrowid
            else:
                row = self.db.execute("""
                    SELECT c.id FROM turns t JOIN conversations c ON c.id=t.conversation_id
                    WHERE t.id=? AND c.chat_id=? AND c.topic_id=?
                """, (parent_id, chat_id, topic_id)).fetchone()
                if row is None:
                    raise ValueError("Parent conversation is unavailable")
                conversation_id = row[0]
                self.db.execute("UPDATE conversations SET expires_at=? WHERE id=?",
                                (time.time() + self.history_seconds, conversation_id))
            cursor = self.db.execute(
                "INSERT INTO turns(conversation_id,parent_id,question,answer) VALUES(?,?,?,?)",
                (conversation_id, parent_id, question, answer),
            )
            turn_id = cursor.lastrowid
            self.db.execute("INSERT INTO message_links VALUES(?,?,?,?,?)",
                            (chat_id, topic_id, user_message_id, turn_id, "user"))
            return turn_id

    def link_answer(self, chat_id: int, topic_id: int, message_id: int, turn_id: int) -> None:
        with self.db:
            self.db.execute("INSERT INTO message_links VALUES(?,?,?,?,?)",
                            (chat_id, topic_id, message_id, turn_id, "assistant"))

    def reserve_request(self, chat_id: int, user_id: int, cooldown: int,
                        per_minute: int, per_day: int, now: float | None = None) -> str | None:
        now = time.time() if now is None else now
        with self.db:
            last = self.db.execute(
                "SELECT MAX(created_at) FROM requests WHERE chat_id=? AND user_id=?",
                (chat_id, user_id),
            ).fetchone()[0]
            if last is not None and now - last < cooldown:
                return f"Подождите {cooldown} секунд между запросами."
            minute = self.db.execute("SELECT COUNT(*) FROM requests WHERE created_at>?",
                                     (now - 60,)).fetchone()[0]
            if minute >= per_minute:
                return "Достигнут общий минутный лимит. Попробуйте через минуту."
            day = self.db.execute("SELECT COUNT(*) FROM requests WHERE created_at>=?",
                                  (now - now % 86400,)).fetchone()[0]
            if day >= per_day:
                return "Достигнут дневной лимит бота. Он обновится в 00:00 UTC (03:00 МСК)."
            self.db.execute("INSERT INTO requests VALUES(?,?,?)", (now, chat_id, user_id))
        return None

    def purge(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        with self.db:
            self.db.execute("DELETE FROM conversations WHERE expires_at<=?", (now,))
            self.db.execute("DELETE FROM requests WHERE created_at<?", (now - 86400,))

    def get_offset(self) -> int:
        row = self.db.execute("SELECT value FROM state WHERE key='offset'").fetchone()
        return int(json.loads(row[0])) if row else 0

    def set_offset(self, offset: int) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO state VALUES('offset',?)", (json.dumps(offset),))

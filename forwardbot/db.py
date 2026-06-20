from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from time import time


@dataclass(frozen=True)
class UserSession:
    name: str
    owner_id: int
    phone: str | None
    created_at: int
    updated_at: int


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row

    def init(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS user_sessions (
                    name TEXT PRIMARY KEY,
                    owner_id INTEGER NOT NULL,
                    phone TEXT,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS copy_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    requester_id INTEGER NOT NULL,
                    source_link TEXT NOT NULL,
                    target_chat TEXT NOT NULL,
                    status TEXT NOT NULL,
                    detail TEXT,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                """
            )

    def upsert_session(self, name: str, owner_id: int, phone: str | None) -> None:
        now = int(time())
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO user_sessions (name, owner_id, phone, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    owner_id = excluded.owner_id,
                    phone = excluded.phone,
                    updated_at = excluded.updated_at
                """,
                (name, owner_id, phone, now, now),
            )

    def list_sessions(self) -> list[UserSession]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM user_sessions ORDER BY updated_at DESC"
            ).fetchall()
        return [UserSession(**dict(row)) for row in rows]

    def get_session(self, name: str) -> UserSession | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM user_sessions WHERE name = ?", (name,)
            ).fetchone()
        return UserSession(**dict(row)) if row else None

    def create_job(self, requester_id: int, source_link: str, target_chat: str) -> int:
        now = int(time())
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                INSERT INTO copy_jobs
                    (requester_id, source_link, target_chat, status, created_at, updated_at)
                VALUES (?, ?, ?, 'started', ?, ?)
                """,
                (requester_id, source_link, target_chat, now, now),
            )
            return int(cursor.lastrowid)

    def update_job(self, job_id: int, status: str, detail: str | None = None) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE copy_jobs
                SET status = ?, detail = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, detail, int(time()), job_id),
            )

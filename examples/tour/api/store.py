# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The advisor's sessions on disk: ``SqliteSessionStore``, the six storage methods of
``demo_common.sessions.SessionStore`` over one SQLite file, plus the two reads the history
routes need. An advisor's workbench is open all day and the API restarts under them, so a
conversation has to be there after the restart; a single file is all this deployment needs
for it, and the store is a subclass exactly as the base class's docstring describes, so no
route or record shape changes.

Two tables. ``sessions`` holds one row per session — the principal, the state document, and
the version the base class's compare-and-set runs on — and ``messages`` holds the transcript
as one row per message, so a long conversation appends rather than rewriting itself. Both
sides of a turn go through the base class: it decides what changed, and these methods only
store it.

Reads and writes are synchronous, like the base class's, and serialised on one lock: the
host is a single asyncio process, so nothing here blocks another request for longer than a
statement. The file is opened per call and left in WAL mode, so a second process — a
restarted API, a script — sees the same sessions.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from demo_common import SessionConflictError, SessionStore
from shopping_agent import ShoppingSessionState

# How long a statement waits for another writer's transaction before it gives up.
_BUSY_TIMEOUT_S = 5.0

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL,
    version    INTEGER NOT NULL,
    state_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_by_user ON sessions (user_id, updated_at DESC);
CREATE TABLE IF NOT EXISTS messages (
    session_id   TEXT NOT NULL,
    seq          INTEGER NOT NULL,
    message_json TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    PRIMARY KEY (session_id, seq)
);
"""

# The note ``demo_common.host.append_user_turn`` puts in front of a user's message when
# something happened outside the conversation. It is written for the model, so the history
# view drops it.
_APP_EVENT_NOTE = re.compile(r"^\[[^\]]{0,80}since your last reply:")

# What a session is called before anyone has said anything in it.
UNTITLED = "新会话"

TITLE_CHARS = 40


@dataclass(frozen=True)
class SessionSummary:
    """One line of the advisor's history list: the session, what to call it, when it last
    changed, and how many messages the history view shows for it."""

    session_id: str
    title: str
    updated_at: datetime
    message_count: int


def display_text(message: dict[str, Any]) -> str:
    """The part of one stored message a person is shown: the text of a string content, or
    the text blocks of a block list joined. Everything the model exchanged with the tools
    is left out — a ``tool_use`` or ``tool_result`` block carries no text — and so is the
    app-event note, which is the host talking to the model and not the advisor talking."""
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    parts = []
    for block in content if isinstance(content, list) else []:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        text = str(block.get("text") or "").strip()
        if text and not _APP_EVENT_NOTE.match(text):
            parts.append(text)
    return "\n\n".join(parts)


def display_messages(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    """A transcript as the history route returns it: the advisor's turns and the
    assistant's replies in order, each as one string, with the tool exchange between them
    dropped. A message the view has nothing to show for is not a line."""
    view = []
    for message in messages:
        role = message.get("role")
        if role not in ("user", "assistant"):
            continue
        if text := display_text(message):
            view.append({"role": role, "text": text})
    return view


def title_of(view: list[dict[str, str]]) -> str:
    """What the history list calls a conversation: its first advisor message, on one line
    and cut to :data:`TITLE_CHARS` characters."""
    for line in view:
        if line["role"] == "user":
            return " ".join(line["text"].split())[:TITLE_CHARS] or UNTITLED
    return UNTITLED


class SqliteSessionStore(SessionStore[ShoppingSessionState]):
    """``SessionStore`` over ``path``. The file and its parent are created on
    construction, so a boot fails here rather than on the first request."""

    def __init__(self, path: Path, state_type: type[ShoppingSessionState] = ShoppingSessionState):
        super().__init__(state_type)
        self._path = path
        # Re-entrant: summaries() reads the session rows and then each transcript.
        self._lock = threading.RLock()
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._open() as connection:
            # WAL is a property of the file, so it is set once and outlives this process.
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(_SCHEMA)

    # -- connections ---------------------------------------------------------------------

    @contextmanager
    def _open(self) -> Iterator[sqlite3.Connection]:
        """A connection of this call's own, in autocommit; the lock serialises the callers
        in this process and SQLite's own locking the ones in others."""
        with self._lock:
            connection = sqlite3.connect(self._path, timeout=_BUSY_TIMEOUT_S, isolation_level=None)
            try:
                yield connection
            finally:
                connection.close()

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        """One transaction, taking the write lock before it reads: a compare-and-set has
        to see what another process wrote."""
        with self._open() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except BaseException:
                connection.execute("ROLLBACK")
                raise
            connection.execute("COMMIT")

    # -- storage: the six methods the base class calls ------------------------------------

    def read_state(self, session_id: str) -> tuple[int, dict[str, Any]] | None:
        with self._open() as connection:
            row = connection.execute(
                "SELECT version, state_json FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        return None if row is None else (row[0], json.loads(row[1]))

    def write_state(self, session_id: str, document: dict[str, Any], version: int) -> None:
        now = _now()
        with self._write() as connection:
            row = connection.execute(
                "SELECT version FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            if (row[0] if row else 0) != version:
                raise SessionConflictError(session_id)
            connection.execute(
                "INSERT INTO sessions (session_id, user_id, version, state_json, created_at,"
                " updated_at) VALUES (?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(session_id) DO UPDATE SET user_id = excluded.user_id,"
                " version = excluded.version, state_json = excluded.state_json,"
                " updated_at = excluded.updated_at",
                (
                    session_id,
                    document["user_id"],
                    version + 1,
                    json.dumps(document, ensure_ascii=False, default=str),
                    now,
                    now,
                ),
            )

    def read_messages(self, session_id: str) -> list[dict[str, Any]]:
        with self._open() as connection:
            rows = connection.execute(
                "SELECT message_json FROM messages WHERE session_id = ? ORDER BY seq",
                (session_id,),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def write_messages(self, session_id: str, messages: list[dict[str, Any]], start: int) -> None:
        """Replace the transcript from ``start`` on, which is an append at its stored length
        and a rewrite of the whole transcript at 0 (a turn that compacted earlier messages)."""
        now = _now()
        with self._write() as connection:
            connection.execute(
                "DELETE FROM messages WHERE session_id = ? AND seq >= ?", (session_id, start)
            )
            connection.executemany(
                "INSERT INTO messages (session_id, seq, message_json, created_at)"
                " VALUES (?, ?, ?, ?)",
                [
                    (session_id, start + offset, _dump_message(message), now)
                    for offset, message in enumerate(messages)
                ],
            )

    def delete(self, session_id: str) -> None:
        with self._write() as connection:
            connection.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
            connection.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))

    def session_ids_for_user(self, user_id: str) -> list[str]:
        """The user's sessions, the one written last first."""
        with self._open() as connection:
            rows = connection.execute(
                "SELECT session_id FROM sessions WHERE user_id = ?"
                " ORDER BY updated_at DESC, session_id DESC",
                (user_id,),
            ).fetchall()
        return [row[0] for row in rows]

    # -- the history routes' two reads ---------------------------------------------------

    def summaries(self, user_id: str) -> list[SessionSummary]:
        """One line per session of ``user_id``, newest first. The count is the history
        view's own length, so the list and the transcript route agree; that reads every
        transcript, which one advisor's day of conversations is small enough for."""
        with self._open() as connection:
            rows = connection.execute(
                "SELECT session_id, updated_at FROM sessions WHERE user_id = ?"
                " ORDER BY updated_at DESC, session_id DESC",
                (user_id,),
            ).fetchall()
        summaries = []
        for session_id, updated_at in rows:
            view = display_messages(self.transcript(session_id))
            summaries.append(
                SessionSummary(
                    session_id=session_id,
                    title=title_of(view),
                    updated_at=datetime.fromisoformat(updated_at),
                    message_count=len(view),
                )
            )
        return summaries

    def transcript(self, session_id: str) -> list[dict[str, Any]]:
        """The stored messages of one session, as stored. An unknown session has none."""
        return self.read_messages(session_id)


def _now() -> str:
    """The stored timestamp: UTC ISO 8601, which sorts as text in the order it happened."""
    return datetime.now(UTC).isoformat()


def _dump_message(message: dict[str, Any]) -> str:
    return json.dumps(message, ensure_ascii=False, default=str)

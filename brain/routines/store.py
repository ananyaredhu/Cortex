"""Where routines are kept (docs/routines.md, "Data"): one row per routine, and nothing about access.

A routine row holds the saved question and its schedule. It holds no token, credential, token set or decision: who may see
what is decided afresh at every run. `last_run` is a pointer to the run's audit event and conversation, not a copy of an
answer; the answer is read back through the conversation, under its withholding rule.

`claim` is what lets two Brain processes share one table: a due routine is taken by moving `next_run_at` forward, and only
the process whose update finds the old value wins.
"""
import json
import threading
from dataclasses import asdict, dataclass, field
from typing import Protocol

import psycopg

SCHEMA = """
CREATE TABLE IF NOT EXISTS routines (
    routine_id   TEXT PRIMARY KEY,
    owner        TEXT NOT NULL,
    status       TEXT NOT NULL,
    next_run_at  TEXT,
    data         JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS routines_owner_idx ON routines (owner);
CREATE INDEX IF NOT EXISTS routines_due_idx ON routines (status, next_run_at);
"""


@dataclass
class Routine:
    routine_id: str
    owner: str                           # canonical email
    name: str
    question: str
    schedule: dict                       # schedule.Schedule.as_json()
    timezone: str                        # IANA name
    created_at: str
    updated_at: str
    sources: list[str] = field(default_factory=list)
    status: str = "active"               # active | paused
    paused_reason: str | None = None     # None when the owner paused it; else why the Brain did
    next_run_at: str | None = None       # UTC, "YYYY-MM-DDTHH:MM:SSZ"; None while paused
    last_opened_at: str | None = None
    last_run: dict | None = None         # {request_id, conversation_id, ran_at, outcome}
    skips: int = 0                       # scheduled runs in a row skipped because the owner could not be resolved
    retried: bool = False                # the one retry after an unavailable answer service has been used
    running_since: str | None = None     # set while a run is being answered, so the owner can see it is under way


class RoutineStore(Protocol):
    def of_owner(self, owner: str) -> list[Routine]: ...
    def get(self, routine_id: str) -> Routine | None: ...
    def put(self, routine: Routine) -> None: ...
    def delete(self, routine_id: str) -> None: ...
    def due(self, now: str) -> list[Routine]: ...
    def claim(self, routine_id: str, expected: str, new: str) -> bool: ...


class MemoryRoutineStore:
    """For the fixture runtime and the tests. Rows are copied in and out, as a database would."""

    def __init__(self) -> None:
        self._rows: dict[str, Routine] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _copy(r: Routine) -> Routine:
        return Routine(**json.loads(json.dumps(asdict(r))))

    def of_owner(self, owner: str) -> list[Routine]:
        with self._lock:
            return sorted((self._copy(r) for r in self._rows.values() if r.owner == owner), key=lambda r: (r.created_at, r.routine_id))

    def get(self, routine_id: str) -> Routine | None:
        with self._lock:
            row = self._rows.get(routine_id)
            return self._copy(row) if row else None

    def put(self, routine: Routine) -> None:
        with self._lock:
            self._rows[routine.routine_id] = self._copy(routine)

    def delete(self, routine_id: str) -> None:
        with self._lock:
            self._rows.pop(routine_id, None)

    def due(self, now: str) -> list[Routine]:
        with self._lock:
            return sorted((self._copy(r) for r in self._rows.values()
                           if r.status == "active" and r.next_run_at and r.next_run_at <= now), key=lambda r: (r.next_run_at, r.routine_id))

    def claim(self, routine_id: str, expected: str, new: str) -> bool:
        with self._lock:
            row = self._rows.get(routine_id)
            if row is None or row.status != "active" or row.next_run_at != expected:
                return False
            row.next_run_at = new
            return True


class PostgresRoutineStore:
    """The same over Postgres (ADR-006), so routines survive a restart of the Brain (N5)."""

    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute(SCHEMA)

    @classmethod
    def connect(cls, url: str) -> "PostgresRoutineStore":
        return cls(psycopg.connect(url, autocommit=True))

    @staticmethod
    def _row(data: dict) -> Routine:
        return Routine(**data)

    def of_owner(self, owner: str) -> list[Routine]:
        with self._lock:
            rows = self._conn.execute("SELECT data FROM routines WHERE owner = %s", (owner,)).fetchall()
        return sorted((self._row(r[0]) for r in rows), key=lambda r: (r.created_at, r.routine_id))

    def get(self, routine_id: str) -> Routine | None:
        with self._lock:
            row = self._conn.execute("SELECT data FROM routines WHERE routine_id = %s", (routine_id,)).fetchone()
        return self._row(row[0]) if row else None

    def put(self, routine: Routine) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO routines (routine_id, owner, status, next_run_at, data) VALUES (%s, %s, %s, %s, %s) "
                "ON CONFLICT (routine_id) DO UPDATE SET owner = EXCLUDED.owner, status = EXCLUDED.status, "
                "next_run_at = EXCLUDED.next_run_at, data = EXCLUDED.data",
                (routine.routine_id, routine.owner, routine.status, routine.next_run_at, json.dumps(asdict(routine))))

    def delete(self, routine_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM routines WHERE routine_id = %s", (routine_id,))

    def due(self, now: str) -> list[Routine]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT data FROM routines WHERE status = 'active' AND next_run_at IS NOT NULL AND next_run_at <= %s "
                "ORDER BY next_run_at, routine_id", (now,)).fetchall()
        return [self._row(r[0]) for r in rows]

    def claim(self, routine_id: str, expected: str, new: str) -> bool:
        """Move `next_run_at` from `expected` to `new` in one statement; false when another process got there first."""
        with self._lock:
            done = self._conn.execute(
                "UPDATE routines SET next_run_at = %s, data = jsonb_set(data, '{next_run_at}', to_jsonb(%s::text)) "
                "WHERE routine_id = %s AND status = 'active' AND next_run_at = %s", (new, new, routine_id, expected))
            return done.rowcount == 1

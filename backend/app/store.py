from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

TERMINAL = {"ready", "failed", "cancelled", "interrupted"}


class Store:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "jobs.sqlite"
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, spec TEXT NOT NULL,
                    plan TEXT NOT NULL, state TEXT NOT NULL, error TEXT,
                    cancel INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL, updated REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, job TEXT NOT NULL,
                    timestamp REAL NOT NULL, stage TEXT NOT NULL, message TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS events_job_seq ON events(job, seq);
                CREATE INDEX IF NOT EXISTS jobs_state_created ON jobs(state, created);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def create(self, spec: dict, plan: dict) -> dict:
        job_id = uuid.uuid4().hex
        now = time.time()
        with self.connect() as db:
            db.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,0,?,?)",
                       (job_id, spec["name"], json.dumps(spec), json.dumps(plan),
                        "queued", None, now, now))
        return self.get(job_id)

    def get(self, job_id: str) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        result = dict(row)
        result["spec"] = json.loads(result["spec"])
        result["plan"] = json.loads(result["plan"])
        return result

    def list(self) -> list[dict]:
        with self.connect() as db:
            ids = [r[0] for r in db.execute("SELECT id FROM jobs ORDER BY created DESC LIMIT 100")]
        return [self.get(job_id) for job_id in ids]

    def update(self, job_id: str, state: str, error: str | None = None):
        with self.connect() as db:
            db.execute("UPDATE jobs SET state=?,error=?,updated=? WHERE id=?",
                       (state, error, time.time(), job_id))
        self.event(job_id, state, error or state)

    def event(self, job_id: str, stage: str, message: str):
        with self.connect() as db:
            db.execute("INSERT INTO events(job,timestamp,stage,message) VALUES (?,?,?,?)",
                       (job_id, time.time(), stage, message[-4000:]))
            db.execute("""DELETE FROM events WHERE job=? AND seq NOT IN
                       (SELECT seq FROM events WHERE job=? ORDER BY seq DESC LIMIT 2000)""",
                       (job_id, job_id))

    def events(self, job_id: str, after: int = 0) -> list[dict]:
        with self.connect() as db:
            order = "ASC" if after else "DESC"
            rows = [dict(row) for row in db.execute(
                f"SELECT * FROM events WHERE job=? AND seq>? ORDER BY seq {order} LIMIT 200",
                (job_id, after))]
            return rows if after else rows[::-1]

    def cancel(self, job_id: str):
        with self.connect() as db:
            db.execute("UPDATE jobs SET cancel=1 WHERE id=?", (job_id,))

    def cancelled(self, job_id: str) -> bool:
        return bool(self.get(job_id)["cancel"])

    def claim(self) -> dict | None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT id FROM jobs WHERE state='queued' ORDER BY created LIMIT 1").fetchone()
            if row:
                db.execute("UPDATE jobs SET state='starting',updated=? WHERE id=?",
                           (time.time(), row[0]))
        return self.get(row[0]) if row else None

    def recover(self):
        with self.connect() as db:
            db.execute("""UPDATE jobs SET state='interrupted',
                       error='Application stopped during processing. Retry to reuse validated downloads.'
                       WHERE state NOT IN ('ready','failed','cancelled','interrupted','queued')""")

    def directory(self, job_id: str) -> Path:
        if len(job_id) != 32 or any(c not in "0123456789abcdef" for c in job_id):
            raise KeyError(job_id)
        self.get(job_id)
        path = self.root / "jobs" / job_id
        path.mkdir(parents=True, exist_ok=True)
        return path

"""SQLite 状态库：运行、团队、任务、消息、事件、产物。

只做查询与留痕，不作为恢复的唯一来源——`run.json` 才是可移植快照。
"""
from __future__ import annotations

import datetime as _dt
import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import DigitalHuman, Message, Run, Task

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY,
  goal TEXT,
  status TEXT,
  dir TEXT,
  created_at TEXT,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS members (
  run_id TEXT,
  id TEXT,
  name TEXT,
  role TEXT,
  title TEXT,
  mission TEXT,
  PRIMARY KEY (run_id, id)
);

CREATE TABLE IF NOT EXISTS tasks (
  run_id TEXT,
  id TEXT,
  title TEXT,
  role TEXT,
  assignee TEXT,
  status TEXT,
  depends_on TEXT,
  artifact TEXT,
  rounds INTEGER,
  review TEXT,
  PRIMARY KEY (run_id, id)
);

CREATE TABLE IF NOT EXISTS messages (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT,
  ts TEXT,
  sender TEXT,
  recipient TEXT,
  kind TEXT,
  body TEXT
);

CREATE TABLE IF NOT EXISTS events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT,
  ts TEXT,
  kind TEXT,
  detail TEXT
);

CREATE TABLE IF NOT EXISTS artifacts (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT,
  task_id TEXT,
  author TEXT,
  title TEXT,
  path TEXT
);
"""


def now() -> str:
    return _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class RunStore:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ---------- 写入 ----------

    def save_run(self, run: Run) -> None:
        ts = now()
        self.conn.execute(
            "INSERT INTO runs (id, goal, status, dir, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET status=excluded.status, updated_at=excluded.updated_at",
            (run.id, run.goal, run.status, run.dir, ts, ts),
        )
        self.conn.execute("DELETE FROM members WHERE run_id=?", (run.id,))
        for member in run.team.members:
            self.conn.execute(
                "INSERT INTO members (run_id, id, name, role, title, mission) VALUES (?,?,?,?,?,?)",
                (run.id, member.id, member.name, member.role, member.title, member.mission),
            )
        self.conn.commit()

    def save_tasks(self, run_id: str, tasks: List[Task]) -> None:
        for task in tasks:
            self.conn.execute(
                "INSERT INTO tasks (run_id, id, title, role, assignee, status, depends_on, artifact, rounds, review) "
                "VALUES (?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(run_id, id) DO UPDATE SET title=excluded.title, assignee=excluded.assignee, "
                "status=excluded.status, artifact=excluded.artifact, rounds=excluded.rounds, review=excluded.review",
                (
                    run_id,
                    task.id,
                    task.title,
                    task.role,
                    task.assignee,
                    task.status,
                    json.dumps(task.depends_on, ensure_ascii=False),
                    task.artifact,
                    task.rounds,
                    json.dumps(task.review, ensure_ascii=False),
                ),
            )
        self.conn.commit()

    def update_run_status(self, run_id: str, status: str) -> None:
        self.conn.execute(
            "UPDATE runs SET status=?, updated_at=? WHERE id=?", (status, now(), run_id)
        )
        self.conn.commit()

    def add_message(self, run_id: str, message: Message) -> None:
        self.conn.execute(
            "INSERT INTO messages (run_id, ts, sender, recipient, kind, body) VALUES (?,?,?,?,?,?)",
            (run_id, now(), message.sender, message.recipient, message.kind, message.body),
        )
        self.conn.commit()

    def log_event(self, run_id: str, kind: str, detail: str) -> None:
        self.conn.execute(
            "INSERT INTO events (run_id, ts, kind, detail) VALUES (?,?,?,?)",
            (run_id, now(), kind, detail),
        )
        self.conn.commit()

    def add_artifact(self, run_id: str, task: Task, author: str) -> None:
        self.conn.execute(
            "INSERT INTO artifacts (run_id, task_id, author, title, path) VALUES (?,?,?,?,?)",
            (run_id, task.id, author, task.title, task.artifact),
        )
        self.conn.commit()

    # ---------- 查询 ----------

    def run_record(self, run_id: str) -> Optional[Dict[str, Any]]:
        cursor = self.conn.execute("SELECT * FROM runs WHERE id=?", (run_id,))
        row = cursor.fetchone()
        if not row:
            return None
        keys = [d[0] for d in cursor.description]
        return dict(zip(keys, row))

    def members(self, run_id: str) -> List[DigitalHuman]:
        cursor = self.conn.execute(
            "SELECT id, name, role, title, mission FROM members WHERE run_id=? ORDER BY rowid",
            (run_id,),
        )
        return [
            DigitalHuman(id=r[0], name=r[1], role=r[2], title=r[3], mission=r[4] or "")
            for r in cursor.fetchall()
        ]

    def tasks(self, run_id: str) -> List[Dict[str, Any]]:
        cursor = self.conn.execute(
            "SELECT id, title, role, assignee, status, depends_on, artifact, rounds, review "
            "FROM tasks WHERE run_id=? ORDER BY rowid",
            (run_id,),
        )
        rows = []
        for r in cursor.fetchall():
            rows.append(
                {
                    "id": r[0],
                    "title": r[1],
                    "role": r[2],
                    "assignee": r[3],
                    "status": r[4],
                    "depends_on": json.loads(r[5] or "[]"),
                    "artifact": r[6] or "",
                    "rounds": r[7] or 0,
                    "review": json.loads(r[8] or "{}"),
                }
            )
        return rows

    def events(self, run_id: str, limit: int = 200) -> List[Dict[str, str]]:
        cursor = self.conn.execute(
            "SELECT ts, kind, detail FROM events WHERE run_id=? ORDER BY seq ASC LIMIT ?",
            (run_id, limit),
        )
        return [{"ts": r[0], "kind": r[1], "detail": r[2]} for r in cursor.fetchall()]

    def messages(self, run_id: str, limit: int = 200) -> List[Dict[str, str]]:
        cursor = self.conn.execute(
            "SELECT ts, sender, recipient, kind, body FROM messages WHERE run_id=? "
            "ORDER BY seq ASC LIMIT ?",
            (run_id, limit),
        )
        return [
            {"ts": r[0], "sender": r[1], "recipient": r[2], "kind": r[3], "body": r[4]}
            for r in cursor.fetchall()
        ]

    def close(self) -> None:
        try:
            self.conn.close()
        except sqlite3.Error:
            pass

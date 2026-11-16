"""运行目录管理：产物路径、快照读写、运行索引。

目录约定见 docs/tech-design.md：
    <data_dir>/runs/<run_id>/{run.json, state.db, brief.md, team.md, plan.md,
                             final_report.md, artifacts/, run.log}
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import Run, Task

_SLUG_RE = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff]+")


def slugify(text: str, limit: int = 24) -> str:
    cleaned = _SLUG_RE.sub("-", (text or "").strip()).strip("-")
    return cleaned[:limit] or "run"


def make_run_id(goal: str) -> str:
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    digest = hashlib.sha1((goal or "").encode("utf-8")).hexdigest()[:4]
    return "%s-%s-%s" % (stamp, slugify(goal, 20), digest)


class RunWorkspace:
    def __init__(self, data_dir: str, run_id: str):
        self.data_dir = Path(data_dir)
        self.run_id = run_id
        self.root = self.data_dir / "runs" / run_id
        self.artifacts_dir = self.root / "artifacts"
        self.db_path = self.root / "state.db"
        self.log_path = self.root / "run.log"

    def ensure(self) -> "RunWorkspace":
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        return self

    # ---------- 路径 ----------

    def artifact_relpath(self, task: Task) -> str:
        filename = "%s-%s.md" % (task.id, slugify(task.title, 18))
        return "artifacts/%s" % filename

    def path(self, relpath: str) -> Path:
        return self.root / relpath

    # ---------- 读写 ----------

    def write_text(self, relpath: str, text: str) -> Path:
        target = self.path(relpath)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return target

    def read_text(self, relpath: str, limit: Optional[int] = None) -> str:
        target = self.path(relpath)
        if not target.exists():
            return ""
        text = target.read_text(encoding="utf-8", errors="replace")
        if limit and len(text) > limit:
            return text[:limit] + "\n\n（已截断）"
        return text

    def write_json(self, relpath: str, payload: Any) -> Path:
        return self.write_text(relpath, json.dumps(payload, ensure_ascii=False, indent=2))

    def read_json(self, relpath: str) -> Any:
        text = self.read_text(relpath)
        return json.loads(text) if text else None

    def save_run(self, run: Run) -> None:
        self.write_json("run.json", run.to_dict())

    def load_run(self) -> Optional[Run]:
        payload = self.read_json("run.json")
        return Run.from_dict(payload) if payload else None

    def append_log(self, line: str) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(line.rstrip("\n") + "\n")


def find_run(data_dir: str, run_id: str) -> Optional[Path]:
    candidate = Path(data_dir) / "runs" / run_id
    return candidate if (candidate / "run.json").exists() else None


def latest_run(data_dir: str) -> Optional[Path]:
    runs_dir = Path(data_dir) / "runs"
    if not runs_dir.exists():
        return None
    candidates = [p for p in runs_dir.iterdir() if (p / "run.json").exists()]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def list_runs(data_dir: str, limit: int = 20) -> List[Dict[str, Any]]:
    runs_dir = Path(data_dir) / "runs"
    if not runs_dir.exists():
        return []
    rows = []
    items = [p for p in runs_dir.iterdir() if (p / "run.json").exists()]
    for path in sorted(items, key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
        try:
            payload = json.loads((path / "run.json").read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        rows.append(
            {
                "id": payload.get("id", path.name),
                "goal": payload.get("goal", ""),
                "status": payload.get("status", ""),
                "dir": str(path),
            }
        )
    return rows

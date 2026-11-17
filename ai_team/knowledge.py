"""跨运行项目知识库（v0.3）。

设计要点：
1. 条目按 `title` 去重，同名不新增，只更新正文与来源运行，避免多次运行堆出重复经验。
2. `kb.json` 是唯一数据源，`kb.md` 每次全量重渲染，供人读与后续运行引用。
3. 只做纯文本存储，不引入向量检索：标签 + 关键词即可满足当前规模（几十到几百条）。
"""
from __future__ import annotations

import datetime as _dt
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

MAX_NOTES = 500


class KnowledgeBase:
    def __init__(self, data_dir: str):
        self.root = Path(data_dir) / "knowledge"
        self.json_path = self.root / "kb.json"
        self.md_path = self.root / "kb.md"

    # ---------- 读写 ----------

    def notes(self) -> List[Dict[str, Any]]:
        if not self.json_path.exists():
            return []
        try:
            payload = json.loads(self.json_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
        rows = payload.get("notes") if isinstance(payload, dict) else payload
        return [row for row in (rows or []) if isinstance(row, dict)]

    def add(self, items: List[Dict[str, Any]], run_id: str) -> List[Dict[str, Any]]:
        """写入知识条目，按 title 去重，返回实际新增/更新的条目。"""
        notes = self.notes()
        by_title = {str(n.get("title") or "").strip(): n for n in notes}
        changed: List[Dict[str, Any]] = []
        now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")

        for item in items:
            title = str(item.get("title") or "").strip()
            body = str(item.get("body") or item.get("detail") or "").strip()
            if not title or not body:
                continue
            tags = [str(t).strip() for t in (item.get("tags") or []) if str(t).strip()]
            existing = by_title.get(title)
            if existing:
                existing.update({"body": body, "tags": tags or existing.get("tags", []),
                                 "run_id": run_id, "created": now})
                changed.append(existing)
                continue
            note = {
                "id": "kb-%s-%d" % (_dt.datetime.now().strftime("%Y%m%d"), len(notes) + 1),
                "title": title,
                "body": body,
                "tags": tags,
                "run_id": run_id,
                "created": now,
            }
            notes.append(note)
            by_title[title] = note
            changed.append(note)

        notes = notes[-MAX_NOTES:]
        self.root.mkdir(parents=True, exist_ok=True)
        self.json_path.write_text(
            json.dumps({"version": 1, "notes": notes}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.md_path.write_text(render_notes(notes), encoding="utf-8")
        return changed

    # ---------- 查询 ----------

    def search(self, query: str = "", tag: str = "", limit: int = 50) -> List[Dict[str, Any]]:
        rows = self.notes()
        if tag:
            rows = [n for n in rows if tag in (n.get("tags") or [])]
        if query:
            text = query.lower()
            rows = [
                n for n in rows
                if text in str(n.get("title", "")).lower()
                or text in str(n.get("body", "")).lower()
                or any(text in str(t).lower() for t in (n.get("tags") or []))
            ]
        return rows[-limit:]

    def related(self, text: str, limit: int = 5) -> List[Dict[str, Any]]:
        """按中文二元组词面重合召回，供基线/计划阶段引用历史经验。"""
        rows = self.notes()
        if not rows or not (text or "").strip():
            return []
        grams = set(_bigrams(text))
        scored = []
        for note in rows:
            hay = "%s %s %s" % (note.get("title", ""), note.get("body", ""), " ".join(note.get("tags") or []))
            score = len(grams & set(_bigrams(hay)))
            if score > 0:
                scored.append((score, note))
        if not scored:
            return rows[-limit:]
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [note for _, note in scored[:limit]]

    def count(self) -> int:
        return len(self.notes())


def _bigrams(text: str) -> List[str]:
    cleaned = re.sub(r"[^\u4e00-\u9fff0-9A-Za-z]+", "", text or "")
    return [cleaned[i:i + 2] for i in range(len(cleaned) - 1)]


def render_notes(notes: List[Dict[str, Any]]) -> str:
    lines = ["# 项目知识库", "", "> 跨运行累积的经验条目，按时间倒序展示最近 50 条。", ""]
    if not notes:
        lines.append("（暂无条目）")
        return "\n".join(lines) + "\n"
    for note in reversed(notes[-50:]):
        tags = "、".join(note.get("tags") or []) or "无"
        lines += [
            "## %s" % note.get("title", ""),
            "",
            "- 标签：%s" % tags,
            "- 来源运行：`%s`" % note.get("run_id", ""),
            "- 记录时间：%s" % note.get("created", ""),
            "",
            str(note.get("body", "")).strip(),
            "",
        ]
    return "\n".join(lines) + "\n"


def find_note_by_title(notes: List[Dict[str, Any]], title: str) -> Optional[Dict[str, Any]]:
    target = (title or "").strip()
    for note in notes:
        if str(note.get("title") or "").strip() == target:
            return note
    return None

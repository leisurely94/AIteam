"""跨运行记忆库。

每个数字人一个 JSON 文件，保存三类内容：
- `notes`：用户明确要求记住的事实与偏好（长期）
- `history`：最近若干轮对话（短期，超出淘汰最旧）

检索用「字符二元组 + 英文词」的轻量重合度打分，不引入向量库，
保证离线可跑、可解释、可测试。
"""
from __future__ import annotations

import datetime as _dt
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

MAX_NOTES = 50
MAX_HISTORY = 50

TRIGGERS = ("记住", "以后", "往后", "每次", "记得", "我们的约定", "统一按", "always", "默认按")
PREFERENCE_WORDS = ("偏好", "喜欢", "优先", "默认", "习惯")

_LATIN = re.compile(r"[A-Za-z0-9_]+")
_CJK = re.compile(r"[\u4e00-\u9fff]")


def now() -> str:
    return _dt.datetime.now().strftime("%Y-%m-%d %H:%M")


def tokens(text: str) -> set:
    """中文取二元组，英文取整词。用于低成本相关性打分。"""
    latin = set(_LATIN.findall(text or ""))
    cjk = "".join(_CJK.findall(text or ""))
    grams = {cjk[i:i + 2] for i in range(max(0, len(cjk) - 1))}
    if len(cjk) == 1:
        grams.add(cjk)
    return latin | grams


def should_remember(text: str) -> bool:
    """只在用户明确表达「要记住」时写长期记忆，避免把一次性任务要求当偏好。"""
    return any(word in (text or "") for word in TRIGGERS)


def note_kind(text: str) -> str:
    return "preference" if any(word in text for word in PREFERENCE_WORDS) else "fact"


class MemoryStore:
    def __init__(self, data_dir: str):
        self.data_dir = Path(data_dir)
        self.root = self.data_dir / "memory"

    def path(self, human_id: str) -> Path:
        safe = re.sub(r"[^0-9A-Za-z_\u4e00-\u9fff-]", "_", human_id or "unknown")
        return self.root / ("%s.json" % safe)

    # ---------- 读写 ----------

    def load(self, human_id: str, name: str = "") -> Dict[str, Any]:
        path = self.path(human_id)
        payload: Dict[str, Any] = {}
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                payload = {}
        payload.setdefault("human_id", human_id)
        payload.setdefault("name", name)
        payload.setdefault("notes", [])
        payload.setdefault("history", [])
        if name:
            payload["name"] = name
        return payload

    def save(self, payload: Dict[str, Any]) -> None:
        path = self.path(payload.get("human_id", "unknown"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---------- 记忆条目 ----------

    def add_note(self, human_id: str, text: str, kind: str = "fact", name: str = "") -> bool:
        text = (text or "").strip()
        if not text:
            return False
        payload = self.load(human_id, name)
        for note in payload["notes"]:
            if note.get("text") == text:
                return False
        payload["notes"].append({"ts": now(), "kind": kind, "text": text[:200]})
        payload["notes"] = payload["notes"][-MAX_NOTES:]
        self.save(payload)
        return True

    def recall(self, human_id: str, query: str, limit: int = 3) -> List[str]:
        payload = self.load(human_id)
        notes = payload.get("notes") or []
        if not notes:
            return []
        q = tokens(query)
        scored = []
        for index, note in enumerate(notes):
            text = note.get("text", "")
            overlap = len(q & tokens(text))
            # 新记忆略加权，避免旧结论永远压住新结论
            score = overlap / (len(q) ** 0.5 or 1) + index / (len(notes) * 10.0)
            scored.append((score, text))
        scored.sort(key=lambda item: (-item[0], item[1]))
        hits = [text for score, text in scored if score > 0][:limit]
        if hits:
            return hits
        # 没有相关命中时给出最近一条，保证「记得这个人」的连续性
        return [notes[-1]["text"]] if notes else []

    def notes(self, human_id: str) -> List[Dict[str, str]]:
        return list(self.load(human_id).get("notes") or [])

    def clear_notes(self, human_id: str) -> int:
        payload = self.load(human_id)
        count = len(payload.get("notes") or [])
        payload["notes"] = []
        self.save(payload)
        return count

    # ---------- 会话历史 ----------

    def append_turn(self, human_id: str, role: str, text: str, name: str = "") -> None:
        payload = self.load(human_id, name)
        payload["history"].append({"ts": now(), "role": role, "text": (text or "").strip()[:1000]})
        payload["history"] = payload["history"][-MAX_HISTORY:]
        self.save(payload)

    def recent(self, human_id: str, limit: int = 8) -> List[Dict[str, str]]:
        history = list(self.load(human_id).get("history") or [])
        return history[-limit:] if limit else history

    def clear_history(self, human_id: str) -> int:
        payload = self.load(human_id)
        count = len(payload.get("history") or [])
        payload["history"] = []
        self.save(payload)
        return count

    # ---------- 汇总 ----------

    def all(self) -> List[Dict[str, Any]]:
        if not self.root.exists():
            return []
        rows = []
        for path in sorted(self.root.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            rows.append(payload)
        return rows


def format_notes(notes: Sequence[Dict[str, str]]) -> str:
    if not notes:
        return "（空）"
    kind_cn = {"fact": "事实", "preference": "偏好", "note": "记录"}
    return "\n".join(
        "- [%s] %s（%s）" % (kind_cn.get(note.get("kind", ""), "记录"), note.get("text", ""), note.get("ts", ""))
        for note in notes
    )

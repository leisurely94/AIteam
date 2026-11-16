"""数字人对话引擎：人设 + 记忆召回 + 多轮上下文。

一次 reply 的流程：
1. 取出最近会话（短期）与相关记忆（长期）；
2. 重建人设（每轮重来，避免历史稀释人设）；
3. 调模型回复，失败回落规则对话；
4. 落库会话，必要时写长期记忆并回报给入口。
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional

from .memory import MemoryStore, note_kind, should_remember
from .models import DigitalHuman, ProjectBrief


class DigitalHumanChat:
    def __init__(
        self,
        human: DigitalHuman,
        reasoner,
        fallback,
        memory: MemoryStore,
        brief: Optional[ProjectBrief] = None,
        emit: Optional[Callable[[str, str], None]] = None,
    ):
        self.human = human
        self.reasoner = reasoner
        self.fallback = fallback
        self.memory = memory
        self.brief = brief
        self.emit = emit or (lambda kind, message: None)

    def greeting(self) -> str:
        """首次进入会话时的开场，带人设与一句职责介绍。"""
        return self.human.greeting or (
            "我是%s，%s。%s"
            % (self.human.name, self.human.title, self.human.mission)
        )

    def reply(self, text: str) -> Dict[str, object]:
        text = (text or "").strip()
        if not text:
            return {"text": "", "remembered": []}

        recall = self.memory.recall(self.human.id, text)
        history = self.memory.recent(self.human.id, limit=8)
        self.memory.append_turn(self.human.id, "user", text, self.human.name)

        try:
            answer = self.reasoner.converse(self.human, text, history, recall, self.brief)
        except Exception as exc:
            self.emit("warn", "对话生成失败，使用规则回复：%s" % exc)
            answer = self.fallback.converse(self.human, text, history, recall, self.brief)

        answer = (answer or "").strip()
        if not answer:
            answer = "我在，但这次没组织好回答。换个说法再说一次？"

        self.memory.append_turn(self.human.id, "human", answer, self.human.name)

        remembered: List[str] = []
        if should_remember(text):
            note = _clean_note(text)
            added = self.memory.add_note(self.human.id, note, note_kind(text), self.human.name)
            if added:
                remembered.append(note)

        return {"text": answer, "remembered": remembered}


def _clean_note(text: str) -> str:
    """去掉「记住：」这类触发词，只留要记的内容。"""
    note = (text or "").strip()
    for prefix in ("请记住", "记住", "记得", "以后", "往后"):
        if note.startswith(prefix):
            note = note[len(prefix):].lstrip("：:，, ")
    return note[:140]

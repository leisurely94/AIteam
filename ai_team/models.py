"""核心数据模型：需求基线、数字人、团队、任务、消息、运行。

保持纯数据 + 少量派生方法，便于直接序列化和单元测试。
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


def _known_fields(cls, data: Any) -> Dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    names = {f.name for f in dataclasses.fields(cls)}
    return {k: v for k, v in data.items() if k in names}


def _as_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [str(value)]


@dataclass
class ProjectBrief:
    """需求基线：一次运行唯一的判断依据。"""

    goal: str
    background: str = ""
    deliverables: List[str] = field(default_factory=list)
    constraints: List[str] = field(default_factory=list)
    acceptance: List[str] = field(default_factory=list)
    open_questions: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: Any) -> "ProjectBrief":
        d = _known_fields(cls, data)
        d["goal"] = str(d.get("goal") or "").strip()
        for key in ("deliverables", "constraints", "acceptance", "open_questions"):
            d[key] = _as_list(d.get(key))
        return cls(**d)


@dataclass
class DigitalHuman:
    """一个角色化的数字人：人设 + 职责 + 边界 + 过程记忆。"""

    id: str
    name: str
    role: str
    title: str
    mission: str = ""
    skills: List[str] = field(default_factory=list)
    responsibilities: List[str] = field(default_factory=list)
    boundaries: List[str] = field(default_factory=list)
    voice: str = ""
    avatar: str = ""
    greeting: str = ""
    memory: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: Any) -> "DigitalHuman":
        d = _known_fields(cls, data)
        for key in ("skills", "responsibilities", "boundaries", "memory"):
            d[key] = _as_list(d.get(key))
        for key in ("id", "name", "role", "title", "mission", "voice", "avatar", "greeting"):
            d[key] = str(d.get(key) or "")
        return cls(**d)

    def remember(self, note: str, limit: int = 8) -> None:
        note = (note or "").strip()
        if not note:
            return
        self.memory.append(note)
        if len(self.memory) > limit:
            del self.memory[: len(self.memory) - limit]

    @property
    def card_line(self) -> str:
        return "%s（%s）" % (self.name, self.title)


@dataclass
class Team:
    name: str
    lead: str
    members: List[DigitalHuman] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "lead": self.lead,
            "members": [m.to_dict() for m in self.members],
        }

    @classmethod
    def from_dict(cls, data: Any) -> "Team":
        d = data if isinstance(data, dict) else {}
        return cls(
            name=str(d.get("name") or "数字人团队"),
            lead=str(d.get("lead") or ""),
            members=[DigitalHuman.from_dict(m) for m in (d.get("members") or [])],
        )

    def get(self, human_id: str) -> DigitalHuman:
        for member in self.members:
            if member.id == human_id:
                return member
        raise KeyError("团队中没有 id=%s 的数字人" % human_id)

    def by_role(self, role: str) -> Optional[DigitalHuman]:
        for member in self.members:
            if member.role == role:
                return member
        return None

    def ids(self) -> List[str]:
        return [m.id for m in self.members]

    @property
    def lead_human(self) -> Optional[DigitalHuman]:
        for member in self.members:
            if member.id == self.lead:
                return member
        return self.members[0] if self.members else None


@dataclass
class ReviewResult:
    verdict: str                     # pass | changes
    issues: List[str] = field(default_factory=list)
    summary: str = ""

    @property
    def passed(self) -> bool:
        return self.verdict == "pass"

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: Any) -> "ReviewResult":
        d = _known_fields(cls, data)
        verdict = str(d.get("verdict") or "").strip().lower()
        if verdict not in ("pass", "changes"):
            verdict = "pass" if verdict in ("通过", "ok", "approved") else "changes"
        return cls(
            verdict=verdict,
            issues=_as_list(d.get("issues")),
            summary=str(d.get("summary") or ""),
        )


@dataclass
class Task:
    id: str
    title: str
    role: str
    description: str = ""
    deliverable: str = ""
    depends_on: List[str] = field(default_factory=list)
    assignee: str = ""
    status: str = "pending"          # pending | running | done | failed | blocked
    artifact: str = ""
    rounds: int = 0
    review: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: Any) -> "Task":
        d = _known_fields(cls, data)
        d["depends_on"] = _as_list(d.get("depends_on"))
        for key in ("id", "title", "role", "description", "deliverable", "assignee", "status", "artifact"):
            d[key] = str(d.get(key) or "")
        d["status"] = d["status"] or "pending"
        try:
            d["rounds"] = int(d.get("rounds") or 0)
        except (TypeError, ValueError):
            d["rounds"] = 0
        d["review"] = d.get("review") or {}
        return cls(**d)


@dataclass
class Message:
    sender: str
    recipient: str
    kind: str                        # handoff | review | decision
    body: str

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass
class Run:
    id: str
    goal: str
    dir: str
    brief: ProjectBrief
    team: Team
    tasks: List[Task] = field(default_factory=list)
    status: str = "created"          # created | running | finished | partial

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "goal": self.goal,
            "dir": self.dir,
            "status": self.status,
            "brief": self.brief.to_dict(),
            "team": self.team.to_dict(),
            "tasks": [t.to_dict() for t in self.tasks],
        }

    @classmethod
    def from_dict(cls, data: Any) -> "Run":
        d = data if isinstance(data, dict) else {}
        return cls(
            id=str(d.get("id") or ""),
            goal=str(d.get("goal") or ""),
            dir=str(d.get("dir") or ""),
            brief=ProjectBrief.from_dict(d.get("brief")),
            team=Team.from_dict(d.get("team")),
            tasks=[Task.from_dict(t) for t in (d.get("tasks") or [])],
            status=str(d.get("status") or "created"),
        )

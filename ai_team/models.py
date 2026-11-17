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
    """一个角色化的数字人：人设 + 职责 + 边界 + 过程记忆 + 上岗资格（v0.3）。"""

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
    onboarded: bool = True          # 是否通过本项目上岗考核
    training_file: str = ""         # 培训材料相对路径
    exam_score: int = 0             # 上岗考试成绩
    exam_issues: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: Any) -> "DigitalHuman":
        d = _known_fields(cls, data)
        for key in ("skills", "responsibilities", "boundaries", "memory", "exam_issues"):
            d[key] = _as_list(d.get(key))
        for key in ("id", "name", "role", "title", "mission", "voice", "avatar", "greeting", "training_file"):
            d[key] = str(d.get(key) or "")
        d["onboarded"] = bool(d.get("onboarded", True))
        try:
            d["exam_score"] = int(d.get("exam_score") or 0)
        except (TypeError, ValueError):
            d["exam_score"] = 0
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

    @property
    def onboard_text(self) -> str:
        if self.onboarded:
            return "已上岗（考核 %d 分）" % self.exam_score if self.exam_score else "已上岗"
        if not self.exam_score:
            return "待上岗"
        return "未上岗（考核 %d 分）" % self.exam_score


@dataclass
class Position:
    """项目经理定义的岗位需求。"""

    key: str
    title: str
    duties: List[str] = field(default_factory=list)
    requirements: List[str] = field(default_factory=list)
    exam_focus: List[str] = field(default_factory=list)
    headcount: int = 1
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: Any) -> "Position":
        d = _known_fields(cls, data)
        for key in ("key", "title", "reason"):
            d[key] = str(d.get(key) or "").strip()
        for key in ("duties", "requirements", "exam_focus"):
            d[key] = _as_list(d.get(key))
        try:
            d["headcount"] = max(1, int(d.get("headcount") or 1))
        except (TypeError, ValueError):
            d["headcount"] = 1
        return cls(**d)


@dataclass
class TrainingPlan:
    """一名成员的上岗培训包：材料 + 考题（含采分点）。"""

    human_id: str
    material: str = ""
    questions: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"human_id": self.human_id, "material": self.material, "questions": self.questions}

    @classmethod
    def from_dict(cls, data: Any) -> "TrainingPlan":
        d = data if isinstance(data, dict) else {}
        questions = d.get("questions") or []
        cleaned: List[Dict[str, Any]] = []
        for item in questions:
            if isinstance(item, dict) and str(item.get("q") or "").strip():
                cleaned.append({"q": str(item["q"]).strip(), "points": _as_list(item.get("points"))})
            elif isinstance(item, str) and item.strip():
                cleaned.append({"q": item.strip(), "points": []})
        return cls(
            human_id=str(d.get("human_id") or ""),
            material=str(d.get("material") or ""),
            questions=cleaned,
        )


PASS_SCORE = 80
QUESTION_SCORES = (34, 33, 33)


@dataclass
class ExamResult:
    """项目经理判卷结果。"""

    human_id: str
    score: int = 0
    passed: bool = False
    issues: List[str] = field(default_factory=list)
    attempts: int = 1
    answers: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: Any) -> "ExamResult":
        d = _known_fields(cls, data)
        for key in ("human_id",):
            d[key] = str(d.get(key) or "")
        for key in ("issues", "answers"):
            d[key] = _as_list(d.get(key))
        try:
            d["score"] = max(0, min(100, int(d.get("score") or 0)))
        except (TypeError, ValueError):
            d["score"] = 0
        try:
            d["attempts"] = max(1, int(d.get("attempts") or 1))
        except (TypeError, ValueError):
            d["attempts"] = 1
        d["passed"] = d["score"] >= PASS_SCORE if "passed" not in d else bool(d.get("passed")) and d["score"] >= PASS_SCORE
        return cls(**d)


@dataclass
class RetroItem:
    """复盘条目：问题 / 能力提升 / 流程优化。"""

    kind: str                        # issue | capability | process
    title: str
    detail: str = ""
    cause: str = ""
    action: str = ""
    owner: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: Any) -> "RetroItem":
        d = _known_fields(cls, data)
        for key in ("kind", "title", "detail", "cause", "action", "owner"):
            d[key] = str(d.get(key) or "").strip()
        if d["kind"] not in ("issue", "capability", "process"):
            d["kind"] = "issue"
        return cls(**d)


@dataclass
class Retrospective:
    """项目经理的复盘结果。"""

    summary: str = ""
    issues: List[RetroItem] = field(default_factory=list)
    capabilities: List[RetroItem] = field(default_factory=list)
    processes: List[RetroItem] = field(default_factory=list)
    knowledge: List[Dict[str, Any]] = field(default_factory=list)   # {title, body, tags}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "summary": self.summary,
            "issues": [i.to_dict() for i in self.issues],
            "capabilities": [i.to_dict() for i in self.capabilities],
            "processes": [i.to_dict() for i in self.processes],
            "knowledge": self.knowledge,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "Retrospective":
        d = data if isinstance(data, dict) else {}
        def items(key: str) -> List[RetroItem]:
            raw = d.get(key) or []
            return [RetroItem.from_dict(x) for x in raw if isinstance(x, dict)]
        return cls(
            summary=str(d.get("summary") or ""),
            issues=items("issues"),
            capabilities=items("capabilities"),
            processes=items("processes"),
            knowledge=[x for x in (d.get("knowledge") or []) if isinstance(x, dict)],
        )


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

    def onboarded_members(self) -> List[DigitalHuman]:
        return [m for m in self.members if m.onboarded]

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
    review: Dict[str, Any] = field(default_factory=dict)      # 评审专家结论
    pm_review: Dict[str, Any] = field(default_factory=dict)   # 项目经理复核结论（v0.3）

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
        d["pm_review"] = d.get("pm_review") or {}
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
    positions: List[Position] = field(default_factory=list)
    exams: List[ExamResult] = field(default_factory=list)
    retrospective: Optional[Retrospective] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "goal": self.goal,
            "dir": self.dir,
            "status": self.status,
            "brief": self.brief.to_dict(),
            "team": self.team.to_dict(),
            "tasks": [t.to_dict() for t in self.tasks],
            "positions": [p.to_dict() for p in self.positions],
            "exams": [e.to_dict() for e in self.exams],
            "retrospective": self.retrospective.to_dict() if self.retrospective else None,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "Run":
        d = data if isinstance(data, dict) else {}
        retro = d.get("retrospective")
        return cls(
            id=str(d.get("id") or ""),
            goal=str(d.get("goal") or ""),
            dir=str(d.get("dir") or ""),
            brief=ProjectBrief.from_dict(d.get("brief")),
            team=Team.from_dict(d.get("team")),
            tasks=[Task.from_dict(t) for t in (d.get("tasks") or [])],
            status=str(d.get("status") or "created"),
            positions=[Position.from_dict(p) for p in (d.get("positions") or [])],
            exams=[ExamResult.from_dict(e) for e in (d.get("exams") or [])],
            retrospective=Retrospective.from_dict(retro) if isinstance(retro, dict) else None,
        )

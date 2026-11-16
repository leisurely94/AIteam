"""真实模型推理层：基线、组队、计划、执行、评审、汇总。

每个方法都是「一次提示词 + 一次解析」，解析失败追加纠错提示重试一次。
业务校验（角色合法、任务无环）失败同样重试一次；仍失败由编排器回落到规则实现。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Sequence

from .config import Settings
from .jsonutil import JSONParseError, extract_json
from .llm import ChatClient, LLMError
from .models import DigitalHuman, ProjectBrief, ReviewResult, Task, Team
from .personas import create_human, sections_for, template, unique_id
from .prompts import (
    BRIEF_SYSTEM,
    CLARIFY_SYSTEM,
    INTEGRATE_SYSTEM,
    PLAN_SYSTEM,
    REVIEW_SYSTEM,
    ROSTER_SYSTEM,
    act_system,
    act_user,
    brief_user,
    clarify_user,
    integrate_user,
    plan_user,
    review_user,
    roster_user,
)
from .tasks import PlanError, TaskGraph


class ReasonerError(RuntimeError):
    """推理层失败（模型不可用或输出不可用）。"""


class Reasoner:
    """推理接口：离线实现与真实模型实现遵循同一组签名。"""

    def clarify(self, goal: str) -> List[str]:
        raise NotImplementedError

    def brief(self, goal: str, answers: Optional[Dict[str, str]] = None) -> ProjectBrief:
        raise NotImplementedError

    def roster(self, brief: ProjectBrief, limit: int = 6) -> Team:
        raise NotImplementedError

    def plan(self, brief: ProjectBrief, team: Team, limit: int = 8) -> List[Task]:
        raise NotImplementedError

    def act(
        self,
        human: DigitalHuman,
        brief: ProjectBrief,
        task: Task,
        upstream: Optional[Dict[str, str]] = None,
        feedback: Optional[Sequence[str]] = None,
    ) -> str:
        raise NotImplementedError

    def review(self, reviewer: DigitalHuman, task: Task, artifact: str, brief: ProjectBrief) -> ReviewResult:
        raise NotImplementedError

    def integrate(self, brief: ProjectBrief, team: Team, rows: Sequence[Dict[str, str]]) -> str:
        raise NotImplementedError


class LLMReasoner(Reasoner):
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = ChatClient(settings)

    # ---------- 基础调用 ----------

    def _ask(self, system: str, user: str, max_tokens: int = 3000, temperature: float = 0.0) -> str:
        return self.client.complete(system, user, temperature=temperature, max_tokens=max_tokens)

    def _ask_json(self, system: str, user: str) -> Any:
        raw = self._ask(system, user)
        try:
            return extract_json(raw)
        except JSONParseError as exc:
            retry_user = (
                user
                + "\n\n上一次输出无法解析（%s）。请只输出合法 JSON，不要代码块和解释。" % exc
            )
            raw = self._ask(system, retry_user)
            try:
                return extract_json(raw)
            except JSONParseError as exc2:
                raise ReasonerError("模型两次输出都不是合法 JSON：%s" % exc2)

    # ---------- 各阶段 ----------

    def clarify(self, goal: str) -> List[str]:
        payload = self._ask_json(CLARIFY_SYSTEM, clarify_user(goal))
        questions = payload.get("questions") if isinstance(payload, dict) else payload
        if not isinstance(questions, list):
            return []
        return [str(q).strip() for q in questions if str(q).strip()][:3]

    def brief(self, goal: str, answers: Optional[Dict[str, str]] = None) -> ProjectBrief:
        payload = self._ask_json(BRIEF_SYSTEM, brief_user(goal, answers))
        if not isinstance(payload, dict):
            raise ReasonerError("基线输出不是 JSON 对象")
        brief = ProjectBrief.from_dict(payload)
        if not brief.goal:
            brief.goal = goal
        if not brief.deliverables:
            raise ReasonerError("基线缺少交付物")
        return brief

    def roster(self, brief: ProjectBrief, limit: int = 6) -> Team:
        system = ROSTER_SYSTEM % {"limit": limit}
        for attempt in range(2):
            try:
                payload = self._ask_json(system, roster_user(brief, limit))
                return self._build_team(payload, limit)
            except (ReasonerError, KeyError, ValueError) as exc:
                if attempt == 1:
                    raise ReasonerError("团队结构不合法：%s" % exc)
                system += "\n\n注意：上一次输出的结构不合法（%s），请严格按 schema 输出。" % exc
        raise ReasonerError("团队结构不合法")

    def _build_team(self, payload: Any, limit: int) -> Team:
        if not isinstance(payload, dict):
            raise ReasonerError("组队输出不是 JSON 对象")
        raw_members = payload.get("members")
        if not isinstance(raw_members, list) or not raw_members:
            raise ReasonerError("members 为空")

        members: List[DigitalHuman] = []
        taken: List[str] = []
        role_counter: Dict[str, int] = {}
        for item in raw_members[:limit]:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").strip()
            if not role:
                continue
            template(role)  # 校验角色合法
            index = role_counter.get(role, 0)
            human = create_human(
                role,
                focus=str(item.get("focus") or "").strip(),
                index=index,
                name=str(item.get("name") or "").strip() or None,
            )
            human.id = unique_id(human.id, taken)
            taken.append(human.id)
            role_counter[role] = index + 1
            members.append(human)

        if not members:
            raise ReasonerError("没有可用的团队成员")

        # 保证必备角色存在
        for required in ("product_manager", "reviewer"):
            if not any(m.role == required for m in members):
                if len(members) >= limit:
                    members.pop()
                human = create_human(required, index=0)
                human.id = unique_id(human.id, taken)
                taken.append(human.id)
                members.append(human)

        lead_role = str(payload.get("lead") or "").strip()
        lead = next((m.id for m in members if m.role == lead_role), "")
        if not lead:
            lead = next((m.id for m in members if m.role == "product_manager"), members[0].id)

        name = str(payload.get("team_name") or "").strip() or "数字人项目组"
        return Team(name=name, lead=lead, members=members)

    def plan(self, brief: ProjectBrief, team: Team, limit: int = 8) -> List[Task]:
        system = PLAN_SYSTEM % {"limit": limit}
        roles = {m.role for m in team.members}
        for attempt in range(2):
            try:
                payload = self._ask_json(system, plan_user(brief, team, limit))
                tasks = self._build_tasks(payload, roles)
                return tasks
            except (ReasonerError, PlanError, KeyError, ValueError) as exc:
                if attempt == 1:
                    raise ReasonerError("任务计划不合法：%s" % exc)
                system += "\n\n注意：上一次输出的任务图不合法（%s），请修正后重新输出。" % exc
        raise ReasonerError("任务计划不合法")

    def _build_tasks(self, payload: Any, roles: set) -> List[Task]:
        raw = payload.get("tasks") if isinstance(payload, dict) else payload
        if not isinstance(raw, list) or not raw:
            raise ReasonerError("tasks 为空")

        tasks: List[Task] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").strip()
            if role not in roles:
                raise ReasonerError("任务 %s 的角色 %s 不在团队中" % (item.get("id"), role))
            task = Task(
                id=str(item.get("id") or "").strip(),
                title=str(item.get("title") or "").strip(),
                role=role,
                description=str(item.get("description") or "").strip(),
                deliverable=str(item.get("deliverable") or "").strip(),
                depends_on=[str(d).strip() for d in (item.get("depends_on") or [])],
            )
            if not task.id or not task.title:
                raise ReasonerError("任务缺少 id 或标题")
            tasks.append(task)
        TaskGraph(tasks)  # 触发校验
        return tasks

    def act(
        self,
        human: DigitalHuman,
        brief: ProjectBrief,
        task: Task,
        upstream: Optional[Dict[str, str]] = None,
        feedback: Optional[Sequence[str]] = None,
    ) -> str:
        text = self._ask(
            act_system(human),
            act_user(brief, task, upstream, feedback),
            max_tokens=4000,
            temperature=0.4,
        )
        if len(text) < 120:
            raise ReasonerError("产物过短（%d 字），疑似模型未按要求输出" % len(text))
        return text

    def review(self, reviewer: DigitalHuman, task: Task, artifact: str, brief: ProjectBrief) -> ReviewResult:
        payload = self._ask_json(REVIEW_SYSTEM, review_user(task, artifact, brief))
        result = ReviewResult.from_dict(payload)
        if result.verdict == "changes" and not result.issues:
            result.issues = ["产出未满足验收标准，请补充具体结论与证据"]
        return result

    def integrate(self, brief: ProjectBrief, team: Team, rows: Sequence[Dict[str, str]]) -> str:
        return self._ask(
            INTEGRATE_SYSTEM,
            integrate_user(brief, team, rows),
            max_tokens=4000,
            temperature=0.3,
        )


def dump_structure() -> str:
    """调试用：打印各阶段期望的 JSON 结构。"""
    return json.dumps(
        {
            "brief": ["goal", "background", "deliverables", "constraints", "acceptance", "open_questions"],
            "roster": ["team_name", "lead", "members[role,name,focus]"],
            "plan": ["tasks[id,title,role,description,deliverable,depends_on]"],
            "sections": {r: sections_for(r) for r in ("product_manager", "architect", "qa_engineer")},
        },
        ensure_ascii=False,
        indent=2,
    )

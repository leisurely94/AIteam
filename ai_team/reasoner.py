"""真实模型推理层：基线、组队、计划、执行、评审、汇总。

每个方法都是「一次提示词 + 一次解析」，解析失败追加纠错提示重试一次。
业务校验（角色合法、任务无环）失败同样重试一次；仍失败由编排器回落到规则实现。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .config import Settings
from .jsonutil import JSONParseError, extract_json
from .llm import ChatClient, LLMError
from .models import (
    DigitalHuman,
    ExamResult,
    Position,
    ProjectBrief,
    Retrospective,
    ReviewResult,
    Task,
    Team,
    TrainingPlan,
)
from .onboarding import build_team, normalize_positions
from .personas import RECRUITER_ROLE, sections_for
from .prompts import (
    ANSWER_EXAM_SYSTEM,
    BRIEF_SYSTEM,
    CLARIFY_SYSTEM,
    GRADE_SYSTEM,
    INTEGRATE_SYSTEM,
    PLAN_SYSTEM,
    PM_REVIEW_SYSTEM,
    RECRUIT_SYSTEM,
    RETRO_SYSTEM,
    REVIEW_SYSTEM,
    act_system,
    act_user,
    answer_exam_system,
    answer_exam_user,
    brief_user,
    chat_system,
    chat_user,
    clarify_user,
    grade_exam_user,
    integrate_user,
    plan_user,
    pm_review_user,
    recruit_user,
    retro_user,
    review_user,
    TRAIN_SYSTEM,
    train_user,
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

    def roster(self, brief: ProjectBrief, limit: int = 6) -> Team:
        return self.recruit(brief, limit)[1]

    def recruit(self, brief: ProjectBrief, limit: int = 6) -> Tuple[List[Position], Team]:
        raise NotImplementedError

    def train(self, human: DigitalHuman, brief: ProjectBrief, focus_note: str = "") -> TrainingPlan:
        raise NotImplementedError

    def answer_exam(self, human: DigitalHuman, plan: TrainingPlan, brief: ProjectBrief, focus_note: str = "") -> List[str]:
        raise NotImplementedError

    def grade_exam(
        self,
        pm: DigitalHuman,
        human: DigitalHuman,
        plan: TrainingPlan,
        answers: List[str],
        brief: ProjectBrief,
    ) -> ExamResult:
        raise NotImplementedError

    def pm_review(self, pm: DigitalHuman, task: Task, artifact: str, brief: ProjectBrief) -> ReviewResult:
        raise NotImplementedError

    def retrospect(
        self,
        pm: DigitalHuman,
        brief: ProjectBrief,
        team: Team,
        tasks: Sequence[Task],
        exams: Sequence[ExamResult],
        position_lines: str = "",
    ) -> Retrospective:
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

    def converse(
        self,
        human: DigitalHuman,
        text: str,
        history: Optional[Sequence[Dict[str, str]]] = None,
        recall: Optional[Sequence[str]] = None,
        brief: Optional[ProjectBrief] = None,
    ) -> str:
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

    def recruit(self, brief: ProjectBrief, limit: int = 6) -> Tuple[List[Position], Team]:
        system = RECRUIT_SYSTEM % {"limit": limit}
        for attempt in range(2):
            try:
                payload = self._ask_json(system, recruit_user(brief, limit))
                raw = payload.get("positions") if isinstance(payload, dict) else payload
                if not isinstance(raw, list) or not raw:
                    raise ReasonerError("positions 为空")
                positions = normalize_positions(raw, limit)
                if not positions:
                    raise ReasonerError("岗位清单清洗后为空")
                return positions, build_team(brief, positions, limit)
            except (ReasonerError, KeyError, ValueError) as exc:
                if attempt == 1:
                    raise ReasonerError("岗位清单不合法：%s" % exc)
                system += "\n\n注意：上一次输出的岗位清单不合法（%s），请严格按 schema 输出。" % exc
        raise ReasonerError("岗位清单不合法")

    def train(self, human: DigitalHuman, brief: ProjectBrief, focus_note: str = "") -> TrainingPlan:
        payload = self._ask_json(TRAIN_SYSTEM, train_user(human, brief, focus_note))
        if not isinstance(payload, dict):
            raise ReasonerError("培训输出不是 JSON 对象")
        plan = TrainingPlan.from_dict({"human_id": human.id, "material": payload.get("material"),
                                       "questions": payload.get("questions")})
        if not plan.material or len(plan.material) < 200:
            raise ReasonerError("培训材料过短（%d 字）" % len(plan.material))
        if len(plan.questions) < 2:
            raise ReasonerError("考题少于 2 道")
        return plan

    def answer_exam(self, human: DigitalHuman, plan: TrainingPlan, brief: ProjectBrief, focus_note: str = "") -> List[str]:
        payload = self._ask_json(answer_exam_system(human), answer_exam_user(brief, plan, focus_note))
        answers = payload.get("answers") if isinstance(payload, dict) else payload
        if not isinstance(answers, list) or not answers:
            raise ReasonerError("作答为空")
        cleaned = [str(a).strip() for a in answers if str(a).strip()]
        if len(cleaned) < len(plan.questions):
            raise ReasonerError("作答数量不足（%d/%d）" % (len(cleaned), len(plan.questions)))
        return cleaned[: len(plan.questions)]

    def grade_exam(
        self,
        pm: DigitalHuman,
        human: DigitalHuman,
        plan: TrainingPlan,
        answers: List[str],
        brief: ProjectBrief,
    ) -> ExamResult:
        payload = self._ask_json(GRADE_SYSTEM, grade_exam_user(human, brief, plan, answers))
        if not isinstance(payload, dict) or "score" not in payload:
            raise ReasonerError("判卷输出缺少 score")
        return ExamResult.from_dict(
            {"human_id": human.id, "score": payload.get("score"), "issues": payload.get("issues"),
             "answers": answers}
        )

    def pm_review(self, pm: DigitalHuman, task: Task, artifact: str, brief: ProjectBrief) -> ReviewResult:
        payload = self._ask_json(PM_REVIEW_SYSTEM, pm_review_user(task, artifact, brief))
        result = ReviewResult.from_dict(payload)
        if result.verdict == "changes" and not result.issues:
            result.issues = ["产出尚不可直接交付，请补齐结论与证据"]
        return result

    def retrospect(
        self,
        pm: DigitalHuman,
        brief: ProjectBrief,
        team: Team,
        tasks: Sequence[Task],
        exams: Sequence[ExamResult],
        position_lines: str = "",
    ) -> Retrospective:
        payload = self._ask_json(RETRO_SYSTEM, retro_user(brief, team, tasks, exams, position_lines))
        if not isinstance(payload, dict):
            raise ReasonerError("复盘输出不是 JSON 对象")
        result = Retrospective.from_dict(payload)
        if not (result.issues or result.capabilities or result.processes):
            raise ReasonerError("复盘三类条目全为空")
        return result

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
            if role == RECRUITER_ROLE:
                raise ReasonerError("项目经理不承担业务任务，任务 %s 请改派给业务角色" % item.get("id"))
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

    def converse(
        self,
        human: DigitalHuman,
        text: str,
        history: Optional[Sequence[Dict[str, str]]] = None,
        recall: Optional[Sequence[str]] = None,
        brief: Optional[ProjectBrief] = None,
    ) -> str:
        answer = self._ask(
            chat_system(human, brief, recall),
            chat_user(text, history),
            max_tokens=1200,
            temperature=0.6,
        )
        if len(answer.strip()) < 10:
            raise ReasonerError("对话回复过短，疑似模型空转")
        return answer.strip()


def dump_structure() -> str:
    """调试用：打印各阶段期望的 JSON 结构。"""
    return json.dumps(
        {
            "brief": ["goal", "background", "deliverables", "constraints", "acceptance", "open_questions"],
            "recruit": ["positions[key,title,headcount,duties,requirements,exam_focus,reason]"],
            "train": ["material", "questions[q,points]"],
            "exam_answer": ["answers[]"],
            "grade": ["score", "issues[]", "summary"],
            "plan": ["tasks[id,title,role,description,deliverable,depends_on]"],
            "retro": ["summary", "issues[]", "capabilities[]", "processes[]", "knowledge[]"],
            "sections": {r: sections_for(r) for r in ("project_manager", "product_manager", "qa_engineer")},
        },
        ensure_ascii=False,
        indent=2,
    )

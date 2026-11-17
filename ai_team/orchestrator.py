"""编排器：v0.3 七阶段主流程。

    基线 → 招募（PM 定岗 + 建角色） → 上岗（培训 + 考试闸门） → 计划
         → 执行（评审专家 + 项目经理双评审闭环） → 复盘（知识库沉淀） → 汇总

设计要点：
1. 任一阶段失败都回落到离线规则实现，并留 warn 事件，保证运行不中断。
2. 上岗闸门是硬约束：未通过考核的成员不接任务，任务改派并留痕。
3. 单任务失败重试一次；仍失败则标记 failed 并把下游 blocked。
4. 每个动作都落盘（run.json / state.db / 产物 / run.log / kb.json），支持事后追溯。
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence

from .config import Settings
from .factory import build_reasoner
from .knowledge import KnowledgeBase
from .models import (
    DigitalHuman,
    ExamResult,
    Message,
    Position,
    ProjectBrief,
    Retrospective,
    ReviewResult,
    Run,
    Task,
    Team,
)
from .offline_reasoner import OfflineReasoner
from .onboarding import (
    build_team,
    normalize_positions,
    render_exam_results,
    render_onboarding_summary,
    render_positions,
    render_training,
    replacement_for,
)
from .personas import RECRUITER_ROLE
from .store import RunStore
from .tasks import PlanError, TaskGraph
from .workspace import RunWorkspace, make_run_id

MAX_MEMBERS = 6
MAX_TASKS = 8
UPSTREAM_PER_ARTIFACT = 1600
UPSTREAM_TOTAL = 6400
MAX_EXAM_ATTEMPTS = 2

Emit = Callable[[str, str], None]


def _noop(kind: str, message: str) -> None:
    return None


@dataclass
class RunContext:
    run: Run
    ws: RunWorkspace
    store: RunStore
    graph: TaskGraph
    onboarded: bool = False


class Orchestrator:
    def __init__(self, settings: Settings, emit: Optional[Emit] = None):
        self.settings = settings
        self.emit: Emit = emit or _noop
        self.reasoner = build_reasoner(settings)
        self.fallback = OfflineReasoner()

    # ---------- 阶段一：设计 ----------

    def design(
        self,
        goal: str,
        answers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, object]:
        """只做设计，不落盘；供 chat 模式先展示团队再确认。

        顺序：基线 → 项目经理定岗 + 招募 → 任务计划。项目经理固定由系统创建。
        """
        brief = self._safe("需求基线", self.reasoner.brief, self.fallback.brief, goal, answers)
        positions, team = self._safe(
            "岗位定义与招募", self.reasoner.recruit, self.fallback.recruit, brief, MAX_MEMBERS
        )
        positions, team = self._ensure_recruiter(brief, positions, team)
        tasks = self._safe("任务计划", self.reasoner.plan, self.fallback.plan, brief, team, MAX_TASKS)
        self._assign_roles(tasks, team)
        return {"brief": brief, "team": team, "tasks": tasks, "positions": positions}

    def _ensure_recruiter(self, brief: ProjectBrief, positions, team: Team):
        """兜底：任何情况下团队第一位都必须是项目经理，且它不进入任务分配。"""
        if not isinstance(team, Team) or not team.members:
            positions = normalize_positions([], MAX_MEMBERS)
            team = build_team(brief, positions, MAX_MEMBERS)
        if not any(m.role == RECRUITER_ROLE for m in team.members):
            rebuilt = build_team(brief, positions, MAX_MEMBERS)
            return positions, rebuilt
        recruiter = next(m for m in team.members if m.role == RECRUITER_ROLE)
        recruiter.onboarded = True
        if team.lead != recruiter.id:
            team.lead = recruiter.id
        # 保证项目经理排在第一位，名单与文档顺序稳定
        team.members = [recruiter] + [m for m in team.members if m.id != recruiter.id]
        return positions, team

    def clarify(self, goal: str) -> List[str]:
        try:
            return self.reasoner.clarify(goal)
        except Exception as exc:
            self.emit("warn", "澄清问题生成失败，使用默认问题：%s" % exc)
            return self.fallback.clarify(goal)

    # ---------- 阶段二：创建运行 ----------

    def start(
        self,
        goal: str,
        answers: Optional[Dict[str, str]] = None,
        design: Optional[Dict[str, object]] = None,
    ) -> RunContext:
        design = design or self.design(goal, answers)
        brief: ProjectBrief = design["brief"]      # type: ignore[assignment]
        team: Team = design["team"]                # type: ignore[assignment]
        tasks: List[Task] = design["tasks"]        # type: ignore[assignment]
        positions: List[Position] = design.get("positions") or []  # type: ignore[assignment]
        positions, team = self._ensure_recruiter(brief, positions, team)
        self._assign_roles(tasks, team)   # 外部传入 design 时也要保证每个任务有人

        run_id = make_run_id(goal)
        ws = RunWorkspace(self.settings.data_dir, run_id).ensure()
        run = Run(
            id=run_id, goal=goal, dir=str(ws.root), brief=brief, team=team,
            tasks=tasks, positions=list(positions),
        )
        graph = TaskGraph(tasks)
        store = RunStore(ws.db_path)

        ws.save_run(run)
        store.save_run(run)
        store.save_tasks(run.id, run.tasks)
        store.log_event(run.id, "run_created", "provider=%s，招募人=%s，成员 %d 人，任务 %d 个"
                        % (self.settings.provider, team.lead_human.name if team.lead_human else "-",
                           len(team.members), len(tasks)))

        ws.write_text("brief.md", render_brief(brief))
        ws.write_text("positions.md", render_positions(positions, team.lead_human, brief))
        ws.write_text("team.md", render_team(team))
        ws.copy_avatars(team.members)
        ws.write_text("plan.md", render_plan(tasks, team))
        ws.append_log("[%s] 创建运行 %s" % (_dt.datetime.now().strftime("%H:%M:%S"), run.id))

        self.emit("stage", "运行目录：%s" % ws.root)
        return RunContext(run=run, ws=ws, store=store, graph=graph)

    # ---------- 阶段三：执行 ----------

    def execute(self, ctx: RunContext) -> Run:
        run = ctx.run
        graph = ctx.graph
        run.status = "running"
        self._persist(ctx)

        self._onboard(ctx)

        pm = self._pm(ctx)
        reviewer = run.team.by_role("reviewer")
        if reviewer is not None and not reviewer.onboarded:
            # 未通过考核的评审不能给别人的产出下结论，改用已上岗成员或项目经理兜底
            self._event(ctx, "warn", "评审专家未通过上岗考核，改由已上岗成员承担独立评审")
            reviewer = None
        reviewer = reviewer or next(
            (m for m in run.team.members if m.onboarded and (pm is None or m.id != pm.id)), None
        )
        if reviewer is None and pm is not None:
            # 全员未上岗时由项目经理兼独立评审，至少保证每份产出有人对照验收标准
            self._event(ctx, "warn", "无已上岗成员可评审，项目经理兼任独立评审")
            reviewer = pm
        if reviewer is None:
            raise RuntimeError("团队为空，无法执行")

        total = len(run.tasks)
        while graph.unfinished():
            ready = graph.ready()
            if not ready:
                for task in graph.stalled():
                    task.status = "blocked"
                    task.review = {"verdict": "blocked", "summary": "上游未完成，任务被阻塞", "issues": []}
                    self._event(ctx, "warn", "任务阻塞：%s" % task.title)
                break

            for task in ready:
                index = run.tasks.index(task) + 1
                self.emit("task", "[%d/%d] %s · %s" % (index, total, task.title, self._assignee_name(ctx, task)))
                try:
                    self._run_task(ctx, task, reviewer, pm)
                except Exception as exc:  # 兜底：任何意外都不应中断整轮
                    task.status = "failed"
                    task.review = {"verdict": "failed", "summary": str(exc), "issues": []}
                    self._event(ctx, "error", "任务失败：%s（%s）" % (task.title, exc))
                    graph.block_downstream(task.id, "上游任务失败：%s" % task.title)
                self._persist(ctx)

        self._retrospect(ctx)
        self._finalize(ctx)
        return run

    # ---------- 上岗闸门 ----------

    def _pm(self, ctx: RunContext) -> Optional[DigitalHuman]:
        return ctx.run.team.by_role(RECRUITER_ROLE) or ctx.run.team.lead_human

    def _onboard(self, ctx: RunContext) -> None:
        """培训 + 考试闸门：通过的才允许接任务，未通过的改派并留痕。"""
        if ctx.onboarded:
            return
        pm = self._pm(ctx)
        if pm is None:
            raise RuntimeError("团队缺少项目经理，无法组织上岗考核")
        pm.onboarded = True

        staff = [m for m in ctx.run.team.members if m.id != pm.id]
        self.emit("stage", "上岗培训与考核：%s（%s）组织，共 %d 名候选人" % (pm.name, pm.title, len(staff)))

        exams: List[ExamResult] = []
        attempts_map: Dict[str, int] = {}
        for member in staff:
            exam = self._onboard_one(ctx, pm, member)
            exams.append(exam)
            attempts_map[member.id] = exam.attempts
            self._event(
                ctx,
                "onboard_pass" if exam.passed else "onboard_failed",
                "%s（%s）考核 %d 分，%d 次尝试，结论：%s"
                % (member.name, member.title, exam.score, exam.attempts,
                   "上岗" if exam.passed else "不上岗"),
            )

        ctx.run.exams = exams
        ctx.ws.write_text("exam-results.md", render_exam_results(ctx.run.team, exams, attempts_map))
        ctx.ws.write_text("team.md", render_team(ctx.run.team))
        self._reassign_unqualified(ctx)
        ctx.onboarded = True
        self._persist(ctx)
        self.emit("stage", render_onboarding_summary(ctx.run.team))

    def _onboard_one(self, ctx: RunContext, pm: DigitalHuman, member: DigitalHuman) -> ExamResult:
        attempt = 1
        focus = ""
        exam = ExamResult(human_id=member.id, score=0, passed=False)
        while attempt <= MAX_EXAM_ATTEMPTS:
            plan = self._safe(
                "上岗培训（%s）" % member.name, self.reasoner.train, self.fallback.train,
                member, ctx.run.brief, focus,
            )
            relpath = "onboarding/%s.md" % member.id
            ctx.ws.write_text(relpath, render_training(plan, member, ctx.run.brief))
            member.training_file = relpath

            answers = self._safe(
                "考试作答（%s）" % member.name, self.reasoner.answer_exam, self.fallback.answer_exam,
                member, plan, ctx.run.brief, focus,
            )
            exam = self._safe(
                "判卷（%s）" % member.name, self.reasoner.grade_exam, self.fallback.grade_exam,
                pm, member, plan, answers, ctx.run.brief,
            )
            exam.human_id = member.id
            exam.attempts = attempt
            if exam.passed:
                break
            focus = "；".join(exam.issues[:3])
            self.emit("task", "      %s 考核未通过（%d 分），进入补考" % (member.name, exam.score))
            attempt += 1

        member.exam_score = exam.score
        member.exam_issues = list(exam.issues)
        member.onboarded = exam.passed
        return exam

    def _reassign_unqualified(self, ctx: RunContext) -> None:
        team = ctx.run.team
        for task in ctx.run.tasks:
            if not task.assignee:
                continue
            try:
                human = team.get(task.assignee)
            except KeyError:
                continue
            if human.onboarded:
                continue
            replacement = replacement_for(team, human)
            if replacement is None:
                task.review = {"verdict": "blocked", "summary": "负责人未通过上岗考核且无替补", "issues": []}
                task.assignee = ""
                task.status = "blocked"
                self._event(ctx, "error", "任务 %s 无人可派（%s 未上岗）" % (task.title, human.name))
                continue
            task.assignee = replacement.id
            task.role = replacement.role
            self._event(
                ctx, "reassign",
                "%s 未通过上岗考核，任务「%s」改派 %s（%s）"
                % (human.name, task.title, replacement.name, replacement.title),
            )

    def _run_task(self, ctx: RunContext, task: Task, reviewer: DigitalHuman, pm: Optional[DigitalHuman]) -> None:
        human = ctx.run.team.get(task.assignee)
        task.status = "running"
        self._persist(ctx)

        reworks = 0
        feedback: Optional[Sequence[str]] = None
        artifact = ""
        review = ReviewResult(verdict="pass", summary="")
        pm_review: Optional[ReviewResult] = None

        while True:
            artifact = self._generate(ctx, task, human, feedback)
            review = self._review(ctx, task, reviewer, artifact)
            pm_review = self._pm_review(ctx, task, artifact, pm) if pm else None
            task.review = review.to_dict()
            task.pm_review = pm_review.to_dict() if pm_review else {}
            issues = list(review.issues)
            if pm_review is not None and not pm_review.passed:
                issues += [i for i in pm_review.issues if i not in issues]

            passed = review.passed and (pm_review is None or pm_review.passed)
            if passed:
                self.emit("task", "      双评审通过：%s" % review.summary)
                break
            if reworks >= self.settings.max_rework:
                self.emit("task", "      评审未通过（已达返工上限）：%s" % review.summary)
                break
            reworks += 1
            task.rounds = reworks
            feedback = issues[:6]
            self.emit("task", "      评审未通过，返工第 %d 轮：%s" % (reworks, "；".join(feedback[:2])))
            self._event(ctx, "review", "%s 返工第 %d 轮：%s" % (task.title, reworks, "；".join(feedback)))

        task.artifact = ctx.ws.artifact_relpath(task)
        ctx.ws.write_text(task.artifact, artifact)
        ctx.store.add_artifact(ctx.run.id, task, human.id)
        task.status = "done"

        human.remember("[%s] %s 已完成，产物 %s" % (task.id, task.title, task.artifact))
        recipients = [reviewer.id] + ([pm.id] if pm is not None and pm.id != reviewer.id else [])
        for recipient in recipients:
            handoff = Message(
                sender=human.id,
                recipient=recipient,
                kind="handoff",
                body="完成「%s」，产物：%s；评审结论：%s；PM 复核：%s"
                % (task.title, task.artifact, review.verdict,
                   pm_review.verdict if pm_review is not None else "-"),
            )
            ctx.store.add_message(ctx.run.id, handoff)
        ctx.ws.append_log("[%s] %s 完成 %s（%s）"
                          % (_dt.datetime.now().strftime("%H:%M:%S"), human.name, task.title, task.status))
        self._event(ctx, "task_done", "%s 完成：%s" % (human.name, task.title))

    # ---------- 复盘与知识库 ----------

    def _retrospect(self, ctx: RunContext) -> None:
        pm = self._pm(ctx)
        if pm is None:
            return
        position_lines = "；".join(
            "%s ×%d：%s" % (p.title, p.headcount, p.reason) for p in ctx.run.positions
        )
        retro = self._safe(
            "项目复盘", self.reasoner.retrospect, self.fallback.retrospect,
            pm, ctx.run.brief, ctx.run.team, ctx.run.tasks, ctx.run.exams, position_lines,
        )
        ctx.run.retrospective = retro
        ctx.ws.write_text("retrospective.md", render_retrospective(retro, ctx.run))

        kb = KnowledgeBase(self.settings.data_dir)
        added = kb.add(retro.knowledge, ctx.run.id)
        self._event(ctx, "knowledge",
                    "复盘沉淀 %d 条知识（知识库累计 %d 条）" % (len(added), kb.count()))
        self.emit("stage", "复盘完成：retrospective.md，知识库累计 %d 条" % kb.count())

    # ---------- 阶段四：汇总 ----------

    def _finalize(self, ctx: RunContext) -> None:
        rows = [
            {
                "title": task.title,
                "assignee": self._assignee_name(ctx, task),
                "artifact": task.artifact or "（无产物）",
                "status": task.status,
                "review": (task.review or {}).get("verdict", "-"),
                "rounds": str(task.rounds),
            }
            for task in ctx.run.tasks
        ]
        pending = [t for t in ctx.run.tasks if t.status != "done"]
        report = self._safe(
            "汇总交付",
            self.reasoner.integrate,
            self.fallback.integrate,
            ctx.run.brief,
            ctx.run.team,
            rows,
        )
        report = report + "\n\n" + render_appendix(ctx.run, ctx.run.team)
        ctx.ws.write_text("final_report.md", report)

        ctx.run.status = "finished" if not pending else "partial"
        self._persist(ctx)
        ctx.store.update_run_status(ctx.run.id, ctx.run.status)
        ctx.store.log_event(ctx.run.id, "run_finished", "status=%s，未完成 %d 个" % (ctx.run.status, len(pending)))
        ctx.ws.append_log("[%s] 运行结束：%s"
                          % (_dt.datetime.now().strftime("%H:%M:%S"), ctx.run.status))
        self.emit("stage", "汇总完成：final_report.md（%d 个任务，未闭环 %d 个）"
                  % (len(ctx.run.tasks), len(pending)))

    # ---------- 内部工具 ----------

    def _safe(self, stage: str, primary: Callable, backup: Callable, *args):
        try:
            return primary(*args)
        except Exception as exc:
            self.emit("warn", "%s 主流程失败，改用规则实现：%s" % (stage, exc))
            return backup(*args)

    def _generate(self, ctx: RunContext, task: Task, human: DigitalHuman, feedback: Optional[Sequence[str]]) -> str:
        upstream = self._upstream(ctx, task)
        try:
            return self.reasoner.act(human, ctx.run.brief, task, upstream, feedback)
        except Exception as exc:
            self.emit("warn", "%s 生成失败，使用规则模板：%s" % (task.title, exc))
            self._event(ctx, "warn", "%s 生成失败：%s" % (task.title, exc))
        return self.fallback.act(human, ctx.run.brief, task, upstream, feedback)

    def _review(self, ctx: RunContext, task: Task, reviewer: DigitalHuman, artifact: str) -> ReviewResult:
        try:
            return self.reasoner.review(reviewer, task, artifact, ctx.run.brief)
        except Exception as exc:
            self.emit("warn", "评审失败，使用规则评审：%s" % exc)
            return self.fallback.review(reviewer, task, artifact, ctx.run.brief)

    def _pm_review(self, ctx: RunContext, task: Task, artifact: str, pm: Optional[DigitalHuman]) -> Optional[ReviewResult]:
        if pm is None:
            return None
        try:
            return self.reasoner.pm_review(pm, task, artifact, ctx.run.brief)
        except Exception as exc:
            self.emit("warn", "PM 复核失败，使用规则复核：%s" % exc)
            self._event(ctx, "warn", "PM 复核回落规则判定：%s" % exc)
            return self.fallback.pm_review(pm, task, artifact, ctx.run.brief)

    def _upstream(self, ctx: RunContext, task: Task) -> Dict[str, str]:
        items: Dict[str, str] = {}
        total = 0
        for dep in task.depends_on:
            upstream_task = ctx.graph.get(dep)
            if not upstream_task.artifact:
                continue
            text = ctx.ws.read_text(upstream_task.artifact, limit=UPSTREAM_PER_ARTIFACT)
            if total + len(text) > UPSTREAM_TOTAL:
                text = text[: max(0, UPSTREAM_TOTAL - total)]
            if not text:
                continue
            items[upstream_task.title] = text
            total += len(text)
        return items

    def _assign_roles(self, tasks: List[Task], team: Team) -> None:
        for task in tasks:
            if task.role == RECRUITER_ROLE:
                # 项目经理不承担业务任务，改派给 lead 之外的第一位业务成员
                fallback_human = next(
                    (m for m in team.members if m.role != RECRUITER_ROLE and m.role != "reviewer"), None
                )
                if fallback_human is not None:
                    self.emit("warn", "任务 %s 指派给项目经理，已改派 %s" % (task.id, fallback_human.name))
                    task.role = fallback_human.role
                    task.assignee = fallback_human.id
                else:
                    raise PlanError("任务 %s 指派给项目经理，但团队没有业务角色可承接" % task.id)
                continue
            human = team.by_role(task.role)
            if human is None or human.role == RECRUITER_ROLE:
                fallback_human = next(
                    (m for m in team.members if m.role != RECRUITER_ROLE and m.role != "reviewer"), None
                )
                if fallback_human is None:
                    raise PlanError("任务 %s 的角色 %s 不在团队中，且没有可用的业务角色" % (task.id, task.role))
                self.emit("warn", "任务 %s 的角色 %s 不在团队中，改由 %s 承担" % (task.id, task.role, fallback_human.name))
                task.role = fallback_human.role
                task.assignee = fallback_human.id
            else:
                task.assignee = human.id

    def _assignee_name(self, ctx: RunContext, task: Task) -> str:
        try:
            return ctx.run.team.get(task.assignee).name
        except KeyError:
            return task.assignee or "未分配"

    def _persist(self, ctx: RunContext) -> None:
        ctx.ws.save_run(ctx.run)
        ctx.store.save_run(ctx.run)
        ctx.store.save_tasks(ctx.run.id, ctx.run.tasks)

    def _event(self, ctx: RunContext, kind: str, detail: str) -> None:
        ctx.store.log_event(ctx.run.id, kind, detail)
        ctx.ws.append_log("[%s] %s %s" % (_dt.datetime.now().strftime("%H:%M:%S"), kind, detail))


# ---------- Markdown 渲染 ----------


def render_brief(brief: ProjectBrief) -> str:
    return (
        "# 需求基线\n\n"
        "## 目标\n\n%s\n\n"
        "## 背景\n\n%s\n\n"
        "## 交付物\n\n%s\n\n"
        "## 约束\n\n%s\n\n"
        "## 验收标准\n\n%s\n\n"
        "## 待确认\n\n%s\n"
        % (
            brief.goal,
            brief.background or "未说明",
            "\n".join("- %s" % d for d in brief.deliverables) or "- 无",
            "\n".join("- %s" % c for c in brief.constraints) or "- 无",
            "\n".join("%d. %s" % (i + 1, a) for i, a in enumerate(brief.acceptance)) or "1. 无",
            "\n".join("- %s" % q for q in brief.open_questions) or "- 无",
        )
    )


def render_team(team: Team) -> str:
    lines = ["# 数字人团队：%s" % team.name, ""]
    for member in team.members:
        marks = []
        if member.id == team.lead:
            marks.append("招募人 · 负责人")
        marks.append(member.onboard_text)
        lines.append("## %s（%s）" % (member.name, " · ".join(marks)))
        lines.append("")
        if member.avatar:
            lines.append("![%s](%s)" % (member.name, member.avatar))
            lines.append("")
        lines += [
            "- 角色：%s" % member.title,
            "- 上岗：%s" % member.onboard_text,
            "- 使命：%s" % member.mission,
            "- 能力：%s" % ("、".join(member.skills) or "通用"),
            "- 边界：%s" % ("、".join(member.boundaries) or "无"),
            "- 风格：%s" % (member.voice or "简洁"),
            "",
        ]
    return "\n".join(lines)


def render_plan(tasks: Sequence[Task], team: Team) -> str:
    lines = ["# 任务计划", "", "| 编号 | 任务 | 角色 | 依赖 | 交付物 |", "| --- | --- | --- | --- | --- |"]
    for task in tasks:
        role = next((m.title for m in team.members if m.role == task.role), task.role)
        deps = "、".join(task.depends_on) or "无"
        lines.append("| %s | %s | %s | %s | %s |" % (task.id, task.title, role, deps, task.deliverable))
    lines += ["", "依赖关系：" + ("、".join("%s←%s" % (t.id, ",".join(t.depends_on)) for t in tasks if t.depends_on) or "无")]
    return "\n".join(lines) + "\n"


def render_appendix(run: Run, team: Optional[Team] = None) -> str:
    lines = [
        "---", "", "## 附录：产物清单", "",
        "| 任务 | 负责人 | 产物 | 状态 | 评审 | PM 复核 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]

    def name_of(task: Task) -> str:
        if team is not None:
            try:
                return team.get(task.assignee).name
            except KeyError:
                pass
        return task.assignee or "未分配"

    for task in run.tasks:
        reviewer_verdict = (task.review or {}).get("verdict", "-")
        pm_verdict = (task.pm_review or {}).get("verdict", "-")
        lines.append(
            "| %s | %s | %s | %s | %s | %s |"
            % (task.title, name_of(task), task.artifact or "无", task.status, reviewer_verdict, pm_verdict)
        )

    if run.positions:
        lines += ["", "## 附录：岗位与上岗", "", "| 岗位 | 人数 | 用工理由 |", "| --- | --- | --- |"]
        for position in run.positions:
            lines.append("| %s | %d | %s |" % (position.title, position.headcount, position.reason or "-"))

    if run.exams:
        lines += ["", "## 附录：上岗考核", "", "| 成员 | 分数 | 结论 |", "| --- | --- | --- |"]
        name_map = {m.id: m.name for m in (team.members if team else [])}
        for exam in run.exams:
            lines.append(
                "| %s | %d | %s |"
                % (name_map.get(exam.human_id, exam.human_id), exam.score,
                   "通过" if exam.passed else "未通过")
            )

    lines += ["", "运行目录：`%s`" % run.dir]
    return "\n".join(lines) + "\n"


def render_retrospective(retro: Retrospective, run: Run) -> str:
    lines = ["# 项目复盘", "", retro.summary or "（项目经理未给出结论）", ""]
    sections = (
        ("过程问题", retro.issues),
        ("角色能力提升", retro.capabilities),
        ("流程优化", retro.processes),
    )
    for title, items in sections:
        lines += ["## %s" % title, ""]
        if not items:
            lines += ["- 无", ""]
            continue
        for item in items:
            lines.append("### %s" % item.title)
            lines.append("")
            if item.detail:
                lines.append("- 现象与证据：%s" % item.detail)
            if item.cause:
                lines.append("- 根因：%s" % item.cause)
            if item.action:
                lines.append("- 改进动作：%s" % item.action)
            if item.owner:
                lines.append("- 责任角色：%s" % item.owner)
            lines.append("")

    lines += ["## 知识库条目", ""]
    if retro.knowledge:
        for item in retro.knowledge:
            tags = "、".join([str(t) for t in (item.get("tags") or [])]) or "无"
            lines.append("- **%s**（%s）：%s" % (item.get("title", ""), tags, item.get("body", "")))
    else:
        lines.append("- 无")
    lines += ["", "---", "", "运行 id：`%s`　目标：%s" % (run.id, run.goal)]
    return "\n".join(lines) + "\n"

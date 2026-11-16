"""编排器：五阶段主流程。

    基线 → 组队 → 计划 → 执行（含评审闭环） → 汇总

设计要点：
1. 任一阶段失败都回落到离线规则实现，并留 warn 事件，保证运行不中断。
2. 单任务失败重试一次；仍失败则标记 failed 并把下游 blocked。
3. 每个动作都落盘（run.json / state.db / 产物 / run.log），支持事后追溯。
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence

from .config import Settings
from .factory import build_reasoner
from .models import DigitalHuman, Message, ProjectBrief, ReviewResult, Run, Task, Team
from .offline_reasoner import OfflineReasoner
from .store import RunStore
from .tasks import PlanError, TaskGraph
from .workspace import RunWorkspace, make_run_id

MAX_MEMBERS = 6
MAX_TASKS = 8
UPSTREAM_PER_ARTIFACT = 1600
UPSTREAM_TOTAL = 6400

Emit = Callable[[str, str], None]


def _noop(kind: str, message: str) -> None:
    return None


@dataclass
class RunContext:
    run: Run
    ws: RunWorkspace
    store: RunStore
    graph: TaskGraph


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
        """只做设计，不落盘；供 chat 模式先展示团队再确认。"""
        brief = self._safe("需求基线", self.reasoner.brief, self.fallback.brief, goal, answers)
        team = self._safe("团队组建", self.reasoner.roster, self.fallback.roster, brief, MAX_MEMBERS)
        tasks = self._safe("任务计划", self.reasoner.plan, self.fallback.plan, brief, team, MAX_TASKS)
        self._assign_roles(tasks, team)
        return {"brief": brief, "team": team, "tasks": tasks}

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

        run_id = make_run_id(goal)
        ws = RunWorkspace(self.settings.data_dir, run_id).ensure()
        run = Run(id=run_id, goal=goal, dir=str(ws.root), brief=brief, team=team, tasks=tasks)
        graph = TaskGraph(tasks)
        store = RunStore(ws.db_path)

        ws.save_run(run)
        store.save_run(run)
        store.save_tasks(run.id, run.tasks)
        store.log_event(run.id, "run_created", "provider=%s，成员 %d 人，任务 %d 个"
                        % (self.settings.provider, len(team.members), len(tasks)))

        ws.write_text("brief.md", render_brief(brief))
        ws.write_text("team.md", render_team(team))
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

        reviewer = run.team.by_role("reviewer") or run.team.lead_human
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
                    self._run_task(ctx, task, reviewer)
                except Exception as exc:  # 兜底：任何意外都不应中断整轮
                    task.status = "failed"
                    task.review = {"verdict": "failed", "summary": str(exc), "issues": []}
                    self._event(ctx, "error", "任务失败：%s（%s）" % (task.title, exc))
                    graph.block_downstream(task.id, "上游任务失败：%s" % task.title)
                self._persist(ctx)

        self._finalize(ctx)
        return run

    def _run_task(self, ctx: RunContext, task: Task, reviewer: DigitalHuman) -> None:
        human = ctx.run.team.get(task.assignee)
        task.status = "running"
        self._persist(ctx)

        reworks = 0
        feedback: Optional[Sequence[str]] = None
        artifact = ""
        review = ReviewResult(verdict="pass", summary="")

        while True:
            artifact = self._generate(ctx, task, human, feedback)
            review = self._review(ctx, task, reviewer, artifact)
            task.review = review.to_dict()
            if review.passed:
                self.emit("task", "      评审通过：%s" % review.summary)
                break
            if reworks >= self.settings.max_rework:
                self.emit("task", "      评审未通过（已达返工上限）：%s" % review.summary)
                break
            reworks += 1
            task.rounds = reworks
            feedback = review.issues
            self.emit("task", "      评审未通过，返工第 %d 轮：%s" % (reworks, "；".join(review.issues[:2])))
            self._event(ctx, "review", "%s 返工第 %d 轮：%s" % (task.title, reworks, "；".join(review.issues)))

        task.artifact = ctx.ws.artifact_relpath(task)
        ctx.ws.write_text(task.artifact, artifact)
        ctx.store.add_artifact(ctx.run.id, task, human.id)
        task.status = "done"

        human.remember("[%s] %s 已完成，产物 %s" % (task.id, task.title, task.artifact))
        handoff = Message(
            sender=human.id,
            recipient=reviewer.id,
            kind="handoff",
            body="完成「%s」，产物：%s；评审结论：%s"
            % (task.title, task.artifact, review.verdict),
        )
        ctx.store.add_message(ctx.run.id, handoff)
        ctx.ws.append_log("[%s] %s 完成 %s（%s）"
                          % (_dt.datetime.now().strftime("%H:%M:%S"), human.name, task.title, task.status))
        self._event(ctx, "task_done", "%s 完成：%s" % (human.name, task.title))

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
        lead = team.lead_human
        for task in tasks:
            human = team.by_role(task.role)
            if human is None:
                if lead is None:
                    raise PlanError("任务 %s 的角色 %s 不在团队中，且没有可用的 lead" % (task.id, task.role))
                self.emit("warn", "任务 %s 的角色 %s 不在团队中，改由 %s 承担" % (task.id, task.role, lead.name))
                task.role = lead.role
                task.assignee = lead.id
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
        lead_mark = "（负责人）" if member.id == team.lead else ""
        lines += [
            "## %s%s" % (member.name, lead_mark),
            "",
            "- 角色：%s" % member.title,
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
    lines = ["---", "", "## 附录：产物清单", "", "| 任务 | 负责人 | 产物 | 状态 | 评审 |", "| --- | --- | --- | --- | --- |"]

    def name_of(task: Task) -> str:
        if team is not None:
            try:
                return team.get(task.assignee).name
            except KeyError:
                pass
        return task.assignee or "未分配"

    for task in run.tasks:
        reviewer_verdict = (task.review or {}).get("verdict", "-")
        lines.append(
            "| %s | %s | %s | %s | %s |"
            % (task.title, name_of(task), task.artifact or "无", task.status, reviewer_verdict)
        )
    lines += ["", "运行目录：`%s`" % run.dir]
    return "\n".join(lines) + "\n"

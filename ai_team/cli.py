"""命令行入口：主入口对话、一次性运行、状态查询、导出。"""
from __future__ import annotations

import argparse
import sys
from typing import Dict, List, Optional, Sequence

from .config import ConfigError, Settings, load_settings
from .export import ExportError, export_bundle, resolve_run_dir
from .models import Run
from .orchestrator import Orchestrator, RunContext, render_team
from .personas import ROLES, catalog_text
from .workspace import RunWorkspace, find_run, latest_run, list_runs

BANNER = "AI Team · 主入口对话驱动的数字人团队"


# ---------- 输出 ----------


def _line(text: str = "") -> None:
    print(text)


def _emit(kind: str, message: str) -> None:
    prefix = {"stage": "\n==> ", "task": "  - ", "warn": "  ! "}.get(kind, "  ")
    print("%s%s" % (prefix, message))


def _print_team(team) -> None:
    _line("\n团队：%s（%d 人）" % (team.name, len(team.members)))
    for member in team.members:
        lead = " [lead]" if member.id == team.lead else ""
        _line("  · %s（%s）%s — %s" % (member.name, member.title, lead, member.mission))


def _print_summary(run: Run) -> None:
    _line("\n" + "-" * 66)
    _line("运行完成：%s　状态：%s" % (run.id, run.status))
    done = sum(1 for t in run.tasks if t.status == "done")
    _line("任务：%d/%d 完成" % (done, len(run.tasks)))
    for task in run.tasks:
        _line("  [%s] %s · %s" % (task.status, task.title, task.artifact or "无产物"))
    _line("交付说明：%s/final_report.md" % run.dir)
    _line("查看状态：python -m ai_team status %s" % run.id)
    _line("导出交付包：python -m ai_team export %s" % run.id)
    _line("-" * 66)


def _print_report_head(run: Run, data_dir: str, lines: int = 24) -> None:
    path = RunWorkspace(data_dir, run.id).path("final_report.md")
    if not path.exists():
        return
    content = path.read_text(encoding="utf-8").splitlines()
    _line("")
    for line in content[:lines]:
        _line(line)
    if len(content) > lines:
        _line("……（完整内容见 final_report.md）")


# ---------- 交互 ----------


def _ask_answers(questions: Sequence[str]) -> Dict[str, str]:
    answers: Dict[str, str] = {}
    if not questions:
        return answers
    _line("\n需要你确认几个关键信息（直接回车可跳过）：")
    for index, question in enumerate(questions, 1):
        _line("[%d/%d] %s" % (index, len(questions), question))
        try:
            answer = input("      > ").strip()
        except (EOFError, KeyboardInterrupt):
            _line("")
            break
        if answer:
            answers[question] = answer
    return answers


def _confirm(prompt: str, default: bool = True) -> bool:
    try:
        raw = input(prompt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return default
    if not raw:
        return default
    return raw in ("y", "yes", "是", "1")


# ---------- 命令实现 ----------


def _build_settings(args) -> Settings:
    return load_settings(
        provider=getattr(args, "provider", "") or "",
        model=getattr(args, "model", "") or "",
        data_dir=getattr(args, "data_dir", "") or "",
        max_rework=getattr(args, "max_rework", -1),
    )


def cmd_run(args) -> int:
    goal = " ".join(args.goal).strip()
    if not goal:
        _line("错误：请给出目标，例如 ai-team run \"为质量团队做周报自动汇总\"")
        return 2
    settings = _build_settings(args)
    orchestrator = Orchestrator(settings, emit=_emit)

    answers: Dict[str, str] = {}
    interactive = args.interactive and sys.stdin.isatty()
    if interactive:
        answers = _ask_answers(orchestrator.clarify(goal))

    _line("%s" % BANNER)
    _line("目标：%s" % goal)
    _line("提供方：%s" % settings.provider)
    ctx: RunContext = orchestrator.start(goal, answers)
    _line("\n需求基线：%d 项交付物 / %d 条验收标准"
          % (len(ctx.run.brief.deliverables), len(ctx.run.brief.acceptance)))
    _print_team(ctx.run.team)
    _line("\n任务计划：")
    for task in ctx.run.tasks:
        _line("  %s %s ← %s" % (task.id, task.title, "、".join(task.depends_on) or "起点"))

    run = orchestrator.execute(ctx)
    _print_summary(run)
    if args.report:
        _print_report_head(run, settings.data_dir)
    ctx.store.close()
    return 0 if run.status == "finished" else 1


def cmd_chat(args) -> int:
    settings = _build_settings(args)
    orchestrator = Orchestrator(settings, emit=_emit)
    _line(BANNER)
    _line("描述你的目标，我来组建数字人团队并把项目做完。输入 /exit 退出。\n")

    while True:
        try:
            goal = input("目标 > ").strip()
        except (EOFError, KeyboardInterrupt):
            _line("\n再见。")
            return 0
        if not goal:
            continue
        if goal in ("/exit", "/quit", "exit", "quit"):
            _line("再见。")
            return 0

        answers = _ask_answers(orchestrator.clarify(goal))
        design = orchestrator.design(goal, answers)
        _print_team(design["team"])
        _line("\n交付物：%s" % "；".join(design["brief"].deliverables))
        if not _confirm("开始执行？[Y/n] "):
            _line("已取消，可重新描述目标。\n")
            continue

        ctx = orchestrator.start(goal, answers, design=design)
        run = orchestrator.execute(ctx)
        _print_summary(run)
        _line("")
        ctx.store.close()


def cmd_status(args) -> int:
    settings = _build_settings(args)
    run_id = (args.run_id or "").strip()
    if run_id:
        path = find_run(settings.data_dir, run_id)
        if path is None:
            _line("找不到运行：%s" % run_id)
            return 2
    else:
        path = latest_run(settings.data_dir)
        if path is None:
            rows = list_runs(settings.data_dir)
            if not rows:
                _line("还没有任何运行记录。先执行：python -m ai_team run \"目标\"")
                return 2
            _line("最近的运行：")
            for row in rows:
                _line("  %s  [%s]  %s" % (row["id"], row["status"], row["goal"]))
            return 0

    ws = RunWorkspace(settings.data_dir, path.name)
    run = ws.load_run()
    if run is None:
        _line("运行快照损坏：%s" % path)
        return 2
    _line("运行：%s" % run.id)
    _line("目标：%s" % run.goal)
    _line("状态：%s　　目录：%s" % (run.status, run.dir))
    _line("团队：%s（%d 人）" % (run.team.name, len(run.team.members)))
    _line("\n任务：")
    for task in run.tasks:
        verdict = (task.review or {}).get("verdict", "-")
        _line("  [%-7s] %-4s %s　负责人：%s　评审：%s"
              % (task.status, task.id, task.title, task.assignee, verdict))
        if task.artifact:
            _line("           %s" % task.artifact)
    return 0


def cmd_export(args) -> int:
    settings = _build_settings(args)
    try:
        target = export_bundle(settings.data_dir, args.run_id or "latest", args.out)
    except ExportError as exc:
        _line("导出失败：%s" % exc)
        return 2
    _line("已导出：%s" % target)
    return 0


def cmd_roles(args) -> int:
    _line("角色目录：\n")
    for key in sorted(ROLES):
        tpl = ROLES[key]
        _line("%-16s %s" % (key, tpl.title))
        _line("    %s" % tpl.mission)
        _line("    章节：%s" % "、".join(tpl.sections))
    return 0


def cmd_init(args) -> int:
    settings = _build_settings(args)
    from pathlib import Path

    Path(settings.data_dir).mkdir(parents=True, exist_ok=True)
    _line("已初始化数据目录：%s" % settings.data_dir)
    _line("下一步：python -m ai_team demo　或　python -m ai_team run \"你的目标\"")
    _line("接入真实模型：export AITEAM_PROVIDER=ark AITEAM_API_KEY=... AITEAM_MODEL=...")
    return 0


DEMO_GOAL = "为质量团队做一个周报自动汇总工具：拉取任务与缺陷数据，生成周报并支持导出"


def cmd_demo(args) -> int:
    if not getattr(args, "provider", "") and not _has_key():
        args.provider = "mock"
    args.goal = [DEMO_GOAL]
    args.interactive = False
    args.report = True
    return cmd_run(args)


def _has_key() -> bool:
    import os

    return bool(os.environ.get("AITEAM_API_KEY") or os.environ.get("ARK_API_KEY"))


# ---------- 解析器 ----------


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--data-dir", dest="data_dir", default=argparse.SUPPRESS,
                        help="运行数据目录，默认 .ai_team")
    parser.add_argument("--provider", dest="provider", default=argparse.SUPPRESS,
                        choices=["mock", "ark", "openai"], help="模型提供方")
    parser.add_argument("--model", dest="model", default=argparse.SUPPRESS, help="模型名")
    parser.add_argument("--max-rework", dest="max_rework", type=int, default=argparse.SUPPRESS,
                        help="评审返工轮数上限，默认 1")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ai-team",
        description="主入口对话驱动的数字人团队：说清目标，自动组队并把项目做完。",
    )
    parser.add_argument("--data-dir", dest="data_dir", default=".ai_team", help="运行数据目录")
    sub = parser.add_subparsers(dest="command")

    run_p = sub.add_parser("run", help="给定目标，一次性完成组队与交付")
    run_p.add_argument("goal", nargs="+", help="项目目标")
    run_p.add_argument("--interactive", action="store_true", help="执行前先做澄清问答")
    run_p.add_argument("--report", action="store_true", help="执行后打印交付说明摘要")
    _add_common(run_p)
    run_p.set_defaults(func=cmd_run, provider="", model="", max_rework=-1)

    chat_p = sub.add_parser("chat", help="主入口对话模式（默认入口）")
    _add_common(chat_p)
    chat_p.set_defaults(func=cmd_chat, provider="", model="", max_rework=-1)

    status_p = sub.add_parser("status", help="查看运行状态")
    status_p.add_argument("run_id", nargs="?", default="", help="运行 id，缺省查看最近一次")
    _add_common(status_p)
    status_p.set_defaults(func=cmd_status, provider="", model="", max_rework=-1)

    export_p = sub.add_parser("export", help="导出单文件交付包")
    export_p.add_argument("run_id", nargs="?", default="latest", help="运行 id 或 latest")
    export_p.add_argument("--out", default="dist", help="输出目录，默认 dist")
    _add_common(export_p)
    export_p.set_defaults(func=cmd_export, provider="", model="", max_rework=-1)

    roles_p = sub.add_parser("roles", help="查看角色目录")
    _add_common(roles_p)
    roles_p.set_defaults(func=cmd_roles, provider="", model="", max_rework=-1)

    init_p = sub.add_parser("init", help="初始化数据目录")
    _add_common(init_p)
    init_p.set_defaults(func=cmd_init, provider="", model="", max_rework=-1)

    demo_p = sub.add_parser("demo", help="用示例目标跑一次完整流程")
    demo_p.add_argument("--report", action="store_true", default=True, help=argparse.SUPPRESS)
    _add_common(demo_p)
    demo_p.set_defaults(func=cmd_demo, provider="", model="", max_rework=-1)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    argv = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    try:
        return int(args.func(args))
    except ConfigError as exc:
        _line("配置错误：%s" % exc)
        return 2
    except KeyboardInterrupt:
        _line("\n已中断。")
        return 130

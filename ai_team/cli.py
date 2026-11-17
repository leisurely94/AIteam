"""命令行入口：主入口对话、一次性运行、状态查询、导出。"""
from __future__ import annotations

import argparse
import sys
from typing import Dict, List, Optional, Sequence

from .config import ConfigError, Settings, load_settings
from .conversation import DigitalHumanChat
from .export import ExportError, export_bundle, resolve_run_dir
from .knowledge import KnowledgeBase
from .memory import MemoryStore, format_notes
from .models import Run, Team
from .orchestrator import Orchestrator, RunContext, render_team
from .personas import ROLE_CHAIN, ROLES, catalog_text, create_human
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
        lead = " [招募人/lead]" if member.id == team.lead else ""
        _line("  · %s（%s）%s — %s ｜ %s"
              % (member.name, member.title, lead, member.mission, member.onboard_text))


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


# ---------- 数字人团队 ----------


def default_team() -> Team:
    """没有运行记录时，用角色目录里的标准阵容。"""
    members = [create_human(role) for role in ROLE_CHAIN]
    if not any(m.role == "reviewer" for m in members):
        members.append(create_human("reviewer"))
    return Team(name="默认数字人团队", lead=members[0].id, members=members)


def load_team(settings: Settings):
    """优先用最近一次运行的真实团队与基线。"""
    path = latest_run(settings.data_dir)
    if path is not None:
        run = RunWorkspace(settings.data_dir, path.name).load_run()
        if run is not None and run.team and run.team.members:
            return run.team, run.brief
    return default_team(), None


def resolve_human(team: Team, token: str):
    token = (token or "").strip().lstrip("@")
    if not token:
        return None
    for member in team.members:
        if token in (member.name, member.id, member.role, member.title):
            return member
    for member in team.members:
        if member.name.startswith(token) or member.role.startswith(token):
            return member
    return None


def make_chat(settings: Settings, orchestrator: Orchestrator, human, brief) -> DigitalHumanChat:
    return DigitalHumanChat(
        human,
        orchestrator.reasoner,
        orchestrator.fallback,
        MemoryStore(settings.data_dir),
        brief,
        emit=_emit,
    )


def _speak(chat: DigitalHumanChat, text: str) -> None:
    result = chat.reply(text)
    for note in result.get("remembered", []):  # type: ignore[union-attr]
        _line("  （已记住：%s）" % note)
    _line("%s > %s" % (chat.human.name, result["text"]))


def _pick_human(team: Team):
    _line("\n可对话的数字人：")
    for index, member in enumerate(team.members, 1):
        _line("  %d) %s（%s）" % (index, member.name, member.title))
    try:
        raw = input("选择序号或姓名 > ").strip()
    except (EOFError, KeyboardInterrupt):
        _line("")
        return None
    if raw.isdigit():
        index = int(raw)
        return team.members[index - 1] if 1 <= index <= len(team.members) else None
    return resolve_human(team, raw)


def cmd_talk(args) -> int:
    settings = _build_settings(args)
    orchestrator = Orchestrator(settings, emit=_emit)
    team, brief = load_team(settings)

    human = resolve_human(team, args.who or "")
    if (args.who or "").strip() and human is None:
        _line("没有找到「%s」。可选：%s" % (
            args.who,
            "、".join("%s（%s）" % (m.name, m.role) for m in team.members),
        ))
        return 2
    if human is None:
        human = _pick_human(team)
        if human is None:
            return 0

    chat = make_chat(settings, orchestrator, human, brief)
    _line("\n%s（%s）" % (human.name, human.title))
    _line("%s > %s" % (human.name, chat.greeting()))
    _line("（输入 /exit 结束，/memory 查看记忆）\n")

    while True:
        try:
            text = input("你 > ").strip()
        except (EOFError, KeyboardInterrupt):
            _line("\n再见。")
            return 0
        if not text:
            continue
        if text in ("/exit", "/quit", "exit", "quit"):
            _line("再见。")
            return 0
        if text == "/memory":
            notes = chat.memory.notes(human.id)
            _line("\n%s记住的事：\n%s\n" % (human.name, format_notes(notes)))
            continue
        _speak(chat, text)


def cmd_memory(args) -> int:
    settings = _build_settings(args)
    store = MemoryStore(settings.data_dir)
    token = (args.who or "").strip()

    if not token:
        rows = store.all()
        if not rows:
            _line("还没有任何记忆。先和数字人聊几句：ai-team talk <姓名>")
            return 0
        _line("记忆总览：\n")
        for payload in rows:
            notes = payload.get("notes") or []
            _line("  %-14s %-8s 记忆 %d 条，会话 %d 轮"
                  % (payload.get("human_id", ""), payload.get("name", ""),
                     len(notes), len(payload.get("history") or [])))
        _line("\n查看某人的记忆：ai-team memory <姓名>；清空：ai-team memory <姓名> --forget")
        return 0

    payload = next(
        (p for p in store.all() if token in (p.get("human_id"), p.get("name") or "")), None
    )
    if payload is None:
        payload = next(
            (p for p in store.all() if str(p.get("name", "")).startswith(token)), None
        )
    if payload is None:
        _line("没有找到「%s」的记忆。可用：%s" % (
            token, "、".join(str(p.get("name")) for p in store.all()) or "无",
        ))
        return 2

    if args.forget:
        count = store.clear_notes(payload["human_id"])
        _line("已清空 %s 的 %d 条长期记忆。" % (payload.get("name", token), count))
        return 0

    _line("\n%s（%s）的记忆：\n" % (payload.get("name", ""), payload.get("human_id", "")))
    _line(format_notes(payload.get("notes") or []))
    _line("")
    return 0


def cmd_team(args) -> int:
    settings = _build_settings(args)
    team, brief = load_team(settings)
    _print_team(team)
    _line("")
    for member in team.members:
        _line("%s（%s）　%s" % (member.name, member.title, member.onboard_text))
        _line("    头像：%s" % (member.avatar or "（无，降级为文字名片）"))
        _line("    开场：%s" % (member.greeting or "—"))
    path = latest_run(settings.data_dir)
    if path is not None:
        _line("\n名片文档：%s/team.md" % path)
        if (path / "positions.md").exists():
            _line("岗位清单：%s/positions.md" % path)
        if (path / "exam-results.md").exists():
            _line("上岗考核：%s/exam-results.md" % path)
    if brief is not None:
        _line("当前项目：%s" % brief.goal)
    _line("\n和某人单聊：ai-team talk <姓名>")
    return 0


def cmd_kb(args) -> int:
    settings = _build_settings(args)
    kb = KnowledgeBase(settings.data_dir)
    rows = kb.search(args.query or "", args.tag or "", limit=args.limit)
    if not rows:
        _line("知识库还没有条目。跑一次 run/demo 后，项目经理会沉淀经验。")
        return 0
    _line("知识库条目：%d 条（当前展示 %d 条）\n" % (kb.count(), len(rows)))
    for note in rows:
        tags = "、".join(note.get("tags") or []) or "无"
        _line("· %s" % note.get("title", ""))
        _line("    标签：%s　来源：%s　%s" % (tags, note.get("run_id", ""), note.get("created", "")))
        _line("    %s" % str(note.get("body", ""))[:200])
    _line("\n完整索引：%s/kb.md" % kb.root)
    return 0


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
    _line("\n上岗结果：")
    _print_team(run.team)
    _print_summary(run)
    if args.report:
        _print_report_head(run, settings.data_dir)
    ctx.store.close()
    return 0 if run.status == "finished" else 1


def cmd_chat(args) -> int:
    settings = _build_settings(args)
    orchestrator = Orchestrator(settings, emit=_emit)
    _line(BANNER)
    _line("描述你的目标，我来组建数字人团队并把项目做完。")
    _line("想找某个数字人单聊，输入 @姓名 你的问题，例如 @林知远 周报先做什么。输入 /exit 退出。\n")

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

        if goal.startswith("@"):
            token, _, text = goal[1:].partition(" ")
            team, brief = load_team(settings)
            human = resolve_human(team, token)
            if human is None:
                _line("没有这个数字人。可用：%s" % "、".join(m.name for m in team.members))
                _line("")
                continue
            chat = make_chat(settings, orchestrator, human, brief)
            if not text.strip():
                _line("%s > %s" % (human.name, chat.greeting()))
                _line("")
                continue
            _speak(chat, text.strip())
            _line("")
            continue

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
    if run.positions:
        _line("岗位：%s" % "、".join("%s×%d" % (p.title, p.headcount) for p in run.positions))
    if run.exams:
        _line("上岗：%s" % "、".join(
            "%s %d分%s" % (
                next((m.name for m in run.team.members if m.id == e.human_id), e.human_id),
                e.score, "通过" if e.passed else "未通过",
            ) for e in run.exams
        ))
    _line("\n任务：")
    names = {member.id: member.name for member in run.team.members}
    for task in run.tasks:
        verdict = (task.review or {}).get("verdict", "-")
        pm_verdict = (task.pm_review or {}).get("verdict", "-")
        _line("  [%-7s] %-4s %s　负责人：%s　评审：%s　PM复核：%s"
              % (task.status, task.id, task.title, names.get(task.assignee, task.assignee), verdict, pm_verdict))
        if task.artifact:
            _line("           %s" % task.artifact)
    if run.retrospective is not None:
        _line("\n复盘：%s" % (run.retrospective.summary or "见 retrospective.md"))
        _line("  retrospective.md　知识库：ai-team kb")
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
                        choices=["mock", "ark", "openai", "hub", "gateway"], help="模型提供方")
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

    team_p = sub.add_parser("team", help="查看数字人团队与名片")
    _add_common(team_p)
    team_p.set_defaults(func=cmd_team, provider="", model="", max_rework=-1)

    talk_p = sub.add_parser("talk", help="与某个数字人多轮对话")
    talk_p.add_argument("who", nargs="?", default="", help="姓名或角色 key，缺省则选择")
    _add_common(talk_p)
    talk_p.set_defaults(func=cmd_talk, provider="", model="", max_rework=-1)

    memory_p = sub.add_parser("memory", help="查看或清空数字人的记忆")
    memory_p.add_argument("who", nargs="?", default="", help="姓名或角色 key，缺省查看总览")
    memory_p.add_argument("--forget", action="store_true", help="清空该数字人的长期记忆")
    _add_common(memory_p)
    memory_p.set_defaults(func=cmd_memory, provider="", model="", max_rework=-1)

    kb_p = sub.add_parser("kb", help="查看跨运行知识库")
    kb_p.add_argument("query", nargs="?", default="", help="按关键词搜索")
    kb_p.add_argument("--tag", default="", help="按标签过滤")
    kb_p.add_argument("--limit", type=int, default=20, help="展示条数上限")
    _add_common(kb_p)
    kb_p.set_defaults(func=cmd_kb, provider="", model="", max_rework=-1)

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

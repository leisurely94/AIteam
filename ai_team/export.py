"""导出：把一次运行汇总成单个可分发 Markdown 交付包。"""
from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Optional

from .workspace import RunWorkspace, find_run, latest_run, slugify


class ExportError(RuntimeError):
    pass


def resolve_run_dir(data_dir: str, run_id: str) -> Path:
    if run_id in ("", "latest", "last"):
        path = latest_run(data_dir)
        if path is None:
            raise ExportError("没有任何运行记录，请先执行 ai-team run")
        return path
    path = find_run(data_dir, run_id)
    if path is None:
        raise ExportError("找不到运行 %s" % run_id)
    return path


def export_bundle(data_dir: str, run_id: str = "latest", out_dir: str = "dist") -> Path:
    run_path = resolve_run_dir(data_dir, run_id)
    ws = RunWorkspace(data_dir, run_path.name)
    run = ws.load_run()
    if run is None:
        raise ExportError("运行快照损坏：%s" % run_path)

    report = ws.read_text("final_report.md")
    if not report:
        raise ExportError("缺少 final_report.md，运行可能尚未完成")

    parts = [
        "> 由 AI Team 生成于 %s　运行 ID：`%s`　目标：%s"
        % (_dt.datetime.now().strftime("%Y-%m-%d %H:%M"), run.id, run.goal),
        "",
        report.rstrip(),
        "",
        "# 附录：各角色完整产物",
        "",
    ]
    for task in run.tasks:
        if not task.artifact:
            continue
        body = ws.read_text(task.artifact)
        author_line = "> 任务 `%s`　负责人：%s　状态：%s" % (task.id, task.assignee, task.status)
        parts += [author_line, "", body.rstrip(), "", "---", ""]

    target_dir = Path(out_dir) / run.id
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / ("%s-交付包.md" % slugify(run.goal, 24))
    target.write_text("\n".join(parts), encoding="utf-8")
    return target

"""招募与上岗（v0.3）：岗位清单整理、团队实例化、培训/考试/成绩的渲染。

推理层只负责「说什么」，本模块负责「怎么校验、怎么组装、怎么落盘成人读文档」，
保证真实模型与离线规则两条链路的结构完全一致。
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Sequence, Tuple

from .models import (
    DigitalHuman,
    ExamResult,
    PASS_SCORE,
    Position,
    ProjectBrief,
    TrainingPlan,
    Team,
)
from .personas import RECRUITER_ROLE, ROLES, ROLE_CHAIN, create_human, sections_for, unique_id


def normalize_positions(raw: Sequence[Dict[str, object]], limit: int) -> List[Position]:
    """清洗模型输出的岗位清单：合法角色 key、去重、保证必备岗位、控制人数。"""
    positions: List[Position] = []
    seen: Dict[str, int] = {}
    budget = max(1, limit - 1)  # 预留项目经理

    for item in raw or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or item.get("role") or "").strip()
        if key not in ROLES or key == RECRUITER_ROLE:
            continue
        position = Position.from_dict(item)
        position.key = key
        position.title = position.title or ROLES[key].title
        position.headcount = 1 if key in ("reviewer",) else position.headcount
        if key in seen:
            # 同一岗位重复出现时合并人数，不重复建岗
            positions[seen[key]].headcount = min(2, positions[seen[key]].headcount + position.headcount)
            continue
        if budget <= 0:
            break
        position.headcount = min(position.headcount, 2, budget)
        budget -= position.headcount
        seen[key] = len(positions)
        positions.append(position)

    # 必备岗位：评审必须有，产品负责人负责需求基线口径
    have = {p.key for p in positions}
    required_keys = ("reviewer", "product_manager")
    for required in required_keys:
        if required in have:
            continue
        if budget <= 0:
            # 挤出位置：优先砍掉最后一个非必备岗位
            removable = [p for p in positions if p.key not in required_keys]
            if not removable:
                break
            dropped = removable[-1]
            positions.remove(dropped)
            budget += dropped.headcount
        tpl = ROLES[required]
        positions.append(
            Position(
                key=required,
                title=tpl.title,
                duties=list(tpl.responsibilities),
                requirements=list(tpl.skills),
                exam_focus=list(tpl.sections),
                reason="必备岗位：保证验收口径与独立评审不缺位",
                headcount=1,
            )
        )
        budget -= 1
        have.add(required)

    order = {key: index for index, key in enumerate(ROLE_CHAIN)}
    positions.sort(key=lambda p: order.get(p.key, 99))
    return positions


def build_team(brief: ProjectBrief, positions: Sequence[Position], limit: int) -> Team:
    """按岗位清单实例化数字人；项目经理固定为第一位与 lead。"""
    recruiter = create_human(RECRUITER_ROLE, focus="定岗、培训考核、复核产出、复盘与知识沉淀")
    recruiter.onboarded = True  # 招募人无需考试
    members: List[DigitalHuman] = [recruiter]
    taken: List[str] = [recruiter.id]
    counter: Dict[str, int] = {}

    for position in positions:
        for _ in range(max(1, position.headcount)):
            if len(members) >= limit:
                break
            index = counter.get(position.key, 0)
            focus = position.reason or "；".join(position.duties[:2])
            human = create_human(position.key, focus=focus, index=index)
            human.id = unique_id(human.id, taken)
            human.onboarded = False  # 必须先通过上岗考核
            taken.append(human.id)
            counter[position.key] = index + 1
            members.append(human)

    return Team(name=team_name(brief), lead=recruiter.id, members=members)


def team_name(brief: ProjectBrief) -> str:
    goal = (brief.goal or "").strip()
    match = re.search(r"为(.{2,10}?)(?:做|开发|搭建|实现|构建|设计)", goal)
    if match:
        return "%s项目组" % match.group(1).strip("，,。 ")
    short = re.sub(r"\s+", "", goal)[:8] or "数字人"
    return "%s项目组" % short


def replacement_for(team: Team, human: DigitalHuman) -> Optional[DigitalHuman]:
    """未上岗成员的替补：同岗位其他已上岗成员优先，其次项目经理。"""
    for member in team.members:
        if member.id != human.id and member.role == human.role and member.onboarded:
            return member
    lead = team.lead_human
    if lead is not None and lead.id != human.id and lead.onboarded:
        return lead
    return None


# ---------- 渲染 ----------


def render_positions(positions: Sequence[Position], recruiter: DigitalHuman, brief: ProjectBrief) -> str:
    lines = [
        "# 岗位清单",
        "",
        "> 招募人：%s（%s）　目标：%s" % (recruiter.name, recruiter.title, brief.goal),
        "",
        "| 岗位 | 人数 | 为什么需要 | 考核重点 |",
        "| --- | --- | --- | --- |",
    ]
    for position in positions:
        lines.append(
            "| %s（%s） | %d | %s | %s |"
            % (
                position.title,
                position.key,
                position.headcount,
                position.reason or "覆盖对应交付物",
                "、".join(position.exam_focus[:3]) or "岗位职责",
            )
        )

    lines += ["", "## 岗位详情", ""]
    for position in positions:
        lines += [
            "### %s（%s）" % (position.title, position.key),
            "",
            "- 职责：%s" % ("、".join(position.duties) or "见角色模板"),
            "- 能力要求：%s" % ("、".join(position.requirements) or "见角色模板"),
            "- 考核重点：%s" % ("、".join(position.exam_focus) or "岗位职责与交付标准"),
            "- 用工理由：%s" % (position.reason or "覆盖对应交付物"),
            "",
        ]
    return "\n".join(lines) + "\n"


def render_training(plan: TrainingPlan, human: DigitalHuman, brief: ProjectBrief) -> str:
    header = [
        "# 上岗培训：%s（%s）" % (human.name, human.title),
        "",
        "- 候选人：%s（%s）" % (human.name, human.id),
        "- 本项目目标：%s" % brief.goal,
        "- 岗位职责：%s" % "、".join(human.responsibilities[:4]),
        "- 岗位边界：%s" % "、".join(human.boundaries[:3]),
        "",
        "## 培训材料",
        "",
    ]
    body = [plan.material.strip() or "（培训材料为空）", "", "## 上岗考题", ""]
    for index, question in enumerate(plan.questions, 1):
        body.append("%d. %s" % (index, question.get("q", "")))
        points = question.get("points") or []
        if points:
            body.append("   - 采分点：%s" % "；".join(points))
        body.append("")
    return "\n".join(header + body) + "\n"


def render_exam_results(team: Team, exams: Sequence[ExamResult], attempts: Dict[str, int]) -> str:
    lines = [
        "# 上岗考核结果",
        "",
        "> 及格线 %d 分；未通过者补考一次，仍不通过不得上岗。" % PASS_SCORE,
        "",
        "| 成员 | 岗位 | 分数 | 结论 | 补考次数 | 主要问题 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    by_id = {e.human_id: e for e in exams}
    for member in team.members:
        exam = by_id.get(member.id)
        if exam is None:
            continue
        lines.append(
            "| %s | %s | %d | %s | %d | %s |"
            % (
                member.name,
                member.title,
                exam.score,
                "通过，已上岗" if exam.passed else "未通过，不得上岗",
                attempts.get(member.id, exam.attempts) - 1,
                "；".join(exam.issues[:2]) or "无",
            )
        )
    lines += ["", "## 上岗结论", ""]
    for member in team.members:
        lines.append("- %s（%s）：%s" % (member.name, member.title, member.onboard_text))
    return "\n".join(lines) + "\n"


def render_onboarding_summary(team: Team) -> str:
    ready = [m for m in team.members if m.onboarded]
    blocked = [m for m in team.members if not m.onboarded]
    lines = [
        "上岗情况：%d/%d 人通过考核" % (len(ready), len(team.members)),
        "通过：%s" % ("、".join("%s(%d)" % (m.name, m.exam_score) for m in ready) or "无"),
    ]
    if blocked:
        lines.append("未通过：%s" % "、".join("%s(%d)" % (m.name, m.exam_score) for m in blocked))
    return "\n".join(lines)


def role_sections(role_key: str) -> List[str]:
    return sections_for(role_key)


def ensure_position_coverage(positions: Sequence[Position], brief: ProjectBrief) -> Tuple[bool, str]:
    """粗校验：岗位清单是否至少覆盖到实现类与验收类角色。"""
    keys = {p.key for p in positions}
    if not keys:
        return False, "岗位清单为空"
    if "reviewer" not in keys:
        return False, "缺少评审岗位"
    return True, ""

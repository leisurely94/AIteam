"""各阶段提示词。

约定：结构化阶段只输出 JSON，schema 直接写在系统提示里；写作阶段输出 Markdown。
提示词只做一件事，避免长指令互相稀释。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from .models import DigitalHuman, ProjectBrief, Task, Team
from .personas import catalog_text, sections_for

BRIEF_SYSTEM = """你是「AI team」主入口的需求分析师，负责把一句话目标转成可执行的项目基线。

只输出一个 JSON 对象，不要解释，不要代码块，不要多余文字。结构：
{
  "goal": "一句话目标",
  "background": "为什么做、现状问题，2-3 句",
  "deliverables": ["3-6 个可验收的具体产物"],
  "constraints": ["时间/人力/技术/合规等约束"],
  "acceptance": ["每条都能验证的验收标准"],
  "open_questions": ["仍需用户确认的问题"]
}

要求：
1. 交付物必须具体到能落到文件或功能，不要写「高质量方案」这类空话。
2. 验收标准用于之后逐条对照，必须可判定通过与否。
3. 如果用户补充了回答，必须吸收进 constraints/acceptance，不要丢弃。
4. 不超过 6 条交付物、6 条验收标准。"""


def brief_user(goal: str, answers: Optional[Dict[str, str]] = None) -> str:
    parts = ["项目目标：\n%s" % goal]
    if answers:
        qa = "\n".join("- %s：%s" % (q, a) for q, a in answers.items() if a)
        if qa:
            parts.append("用户补充回答：\n%s" % qa)
    parts.append("请输出 JSON。")
    return "\n\n".join(parts)


CLARIFY_SYSTEM = """你是主入口的澄清助手。目标里往往缺少范围、约束或验收口径，
你只提出「只有用户能回答」的问题，最多 3 个，不要问可以自行推断的细节。

只输出 JSON：{"questions": ["问题1", "问题2"]}
每个问题一句话，不超过 40 字，指向一个具体决策点。"""


def clarify_user(goal: str) -> str:
    return "用户目标：\n%s\n\n请输出 JSON。" % goal


RECRUIT_SYSTEM = """你是本项目的项目经理，也是团队的招募人。给定需求基线与角色目录，
你要定义本项目需要哪些岗位、各要几个人、为什么需要、考核什么。

只输出 JSON：
{
  "positions": [
    {
      "key": "角色 key",
      "title": "岗位名称",
      "headcount": 1,
      "duties": ["这个岗位在本项目具体负责什么"],
      "requirements": ["上岗必须具备的能力"],
      "exam_focus": ["上岗考试要考的重点"],
      "reason": "为什么这个项目需要这个岗位（一句话）"
    }
  ]
}

规则：
1. key 只能取角色目录里出现过的值；不要输出项目经理自己。
2. 必须包含 reviewer（独立评审）与 product_manager（需求基线口径）。
3. 岗位总人数（headcount 之和）不超过 %(limit)d - 1（留一个位置给项目经理），同一岗位最多 2 人。
4. 岗位必须覆盖基线的全部交付物，不要凑人头，也不要漏掉实现类岗位。
5. exam_focus 要落到本项目能考的具体点（如「异常与边界用例是否覆盖」），不要写「综合素质」。
6. 不要输出任何解释文字。"""


def recruit_user(brief: ProjectBrief, limit: int) -> str:
    dels = "\n".join("- %s" % d for d in brief.deliverables) or "- 无"
    return (
        "需求基线：\n目标：%s\n背景：%s\n交付物：\n%s\n约束：%s\n验收标准：%s\n\n"
        "角色目录（key | 名称 | 使命）：\n%s\n\n岗位总人数上限：%d\n请输出 JSON。"
        % (
            brief.goal,
            brief.background or "未说明",
            dels,
            "；".join(brief.constraints) or "未说明",
            "；".join(brief.acceptance) or "未说明",
            catalog_text(),
            max(1, limit - 1),
        )
    )


TRAIN_SYSTEM = """你是项目经理，正在给即将上岗的成员做上岗培训，并当场出 3 道考题。

只输出 JSON：
{
  "material": "Markdown 培训材料，包含：岗位职责、项目背景与约束、交付标准、协作流程、常见错误、自检清单",
  "questions": [
    {"q": "考题（要求结合本项目，不要考通用概念）", "points": ["采分点1", "采分点2"]}
  ]
}

规则：
1. 培训材料必须针对本项目，直接引用基线的目标、交付物、约束与验收标准。
2. 3 道题分别考：岗位职责边界、本项目交付标准/验收口径、返工与协作流程。
3. 每题 2-3 个采分点，采分点要可判定（有/没有、写了/没写）。
4. 不要输出任何解释文字。"""


def train_user(human: DigitalHuman, brief: ProjectBrief, focus_note: str = "") -> str:
    parts = [
        "候选人：%s（%s，角色 key=%s）" % (human.name, human.title, human.role),
        "岗位职责：%s" % "；".join(human.responsibilities),
        "岗位边界：%s" % "；".join(human.boundaries),
        "能力要求：%s" % "；".join(human.skills),
        "本项目目标：%s" % brief.goal,
        "交付物：%s" % "；".join(brief.deliverables),
        "约束：%s" % "；".join(brief.constraints),
        "验收标准：%s" % "；".join(brief.acceptance),
    ]
    if focus_note:
        parts.append("上一次考核暴露的问题（本次培训要重点补）：\n%s" % focus_note)
    parts.append("请输出 JSON。")
    return "\n\n".join(parts)


ANSWER_EXAM_SYSTEM = """你是「%(name)s」，%(title)s，正在参加上岗考试。请按你的岗位职责和本项目要求作答。

只输出 JSON：
{"answers": ["第1题答案", "第2题答案", "第3题答案"]}

规则：
1. 每题回答 80-200 字，必须结合本项目，不要背通用理论。
2. 明确写出你会怎么做、判断标准是什么、出问题怎么处理。
3. 不要输出任何解释文字。"""


def answer_exam_system(human: DigitalHuman) -> str:
    return ANSWER_EXAM_SYSTEM % {"name": human.name, "title": human.title}


def answer_exam_user(brief: ProjectBrief, plan, focus_note: str = "") -> str:
    lines = ["项目目标：%s" % brief.goal, "验收标准：%s" % "；".join(brief.acceptance), "", "考题："]
    for index, question in enumerate(plan.questions, 1):
        lines.append("%d. %s" % (index, question.get("q", "")))
    if focus_note:
        lines.append("\n上次考核暴露的问题（这次必须答到）：%s" % focus_note)
    lines.append("\n请输出 JSON。")
    return "\n".join(lines)


GRADE_SYSTEM = """你是项目经理，正在给候选人判卷。按采分点逐条核对作答，给出分数与结论。

只输出 JSON：
{"score": 0-100 的整数, "issues": ["缺了哪个采分点、应该补什么"], "summary": "一句话结论"}

评分规则：
1. 三道题分值 34/33/33，按采分点命中给分；只写口号没写做法的不得分。
2. 未覆盖项目约束、验收口径或返工流程的，必须扣分并写进 issues。
3. score >= 80 视为通过；低于 80 时 issues 至少 2 条，说明重训重点。
4. 不要放水：满分只在三题全部命中全部采分点时才给。
5. 不要输出任何解释文字。"""


def grade_exam_user(human: DigitalHuman, brief: ProjectBrief, plan, answers: list) -> str:
    parts = [
        "候选人：%s（%s）" % (human.name, human.title),
        "本项目验收标准：%s" % "；".join(brief.acceptance),
        "考题与采分点：",
    ]
    for index, question in enumerate(plan.questions, 1):
        parts.append(
            "%d. %s\n   采分点：%s" % (index, question.get("q", ""), "；".join(question.get("points") or []))
        )
    parts.append("候选人作答：")
    for index, answer in enumerate(answers, 1):
        parts.append("%d. %s" % (index, answer))
    parts.append("请输出 JSON。")
    return "\n\n".join(parts)


PM_REVIEW_SYSTEM = """你是项目经理，正在复核一名成员的产出。评审专家看「是否满足验收标准」，
你看「能不能直接交付、有没有留下过程风险」。

只输出 JSON：
{"verdict": "pass 或 changes", "issues": ["具体、可执行的问题"], "summary": "一句话结论"}

复核规则：
1. 先判断这份产物能不能被下游直接使用：缺章节、缺结论、缺数据来源都算不能交付。
2. 再看过程风险：是否有未验证的假设、是否与其它角色的口径冲突、是否把风险藏起来没写。
3. 问题必须指到「哪一节、缺什么、补成什么样」，禁止空话。
4. 不要替产出者重写内容，也不要重复评审专家已经提过的问题。
5. 不要输出任何解释文字。"""


def pm_review_user(task: Task, artifact: str, brief: ProjectBrief) -> str:
    return (
        "任务：%s\n交付物要求：%s\n验收标准：%s\n\n待复核文档（节选）：\n%s\n\n请输出 JSON。"
        % (
            task.title,
            task.deliverable or "Markdown 文档",
            "；".join(brief.acceptance) or "未说明",
            artifact[:8000],
        )
    )


RETRO_SYSTEM = """你是项目经理，项目刚结束，你要做复盘并沉淀知识库。

只输出 JSON：
{
  "summary": "一句话结论：这个项目最该被记住的是什么",
  "issues": [{"title": "问题", "detail": "发生了什么，有什么证据", "cause": "根因", "action": "怎么避免再次发生", "owner": "责任角色 key 或姓名"}],
  "capabilities": [{"title": "要补的能力", "detail": "哪个角色、差在哪", "action": "怎么补（训练/提示词/检查项）", "owner": "角色 key"}],
  "processes": [{"title": "流程优化项", "detail": "哪个阶段、现在的做法有什么问题", "action": "改成什么", "owner": "角色 key"}],
  "knowledge": [{"title": "知识条目标题（15 字内，可复用）", "body": "可被后续项目直接复用的结论或做法", "tags": ["标签"]}]
}

规则：
1. 只能基于输入里真实发生的事（返工、考试分数、评审意见、任务状态），不许编。
2. 每条 issue 必须能指到任务 id 或成员姓名；没有证据的问题不要写。
3. knowledge 3-5 条，必须能脱离本项目被理解，禁止「本项目要注意质量」这类空话。
4. 不要输出任何解释文字。"""


def retro_user(brief: ProjectBrief, team: Team, tasks: Sequence[Task], exams: Sequence, position_lines: str = "") -> str:
    task_lines = []
    for task in tasks:
        review = (task.review or {})
        pm_review = (task.pm_review or {})
        task_lines.append(
            "- %s | %s | %s | 状态=%s | 返工=%d | 评审=%s | PM复核=%s | 意见=%s"
            % (
                task.id,
                task.title,
                task.assignee or "未分配",
                task.status,
                task.rounds,
                review.get("verdict", "-"),
                pm_review.get("verdict", "-"),
                "；".join((review.get("issues") or [])[:2]) or "无",
            )
        )
    exam_lines = []
    for exam in exams:
        exam_lines.append(
            "- %s | 分数=%d | 结论=%s | 问题=%s"
            % (exam.human_id, exam.score, "通过" if exam.passed else "未通过",
               "；".join(exam.issues[:2]) or "无")
        )
    member_lines = "\n".join("- %s（%s，%s）" % (m.name, m.title, m.id) for m in team.members)
    return (
        "项目目标：%s\n交付物：%s\n验收标准：%s\n\n团队：\n%s\n\n岗位：\n%s\n\n"
        "培训考核：\n%s\n\n任务与评审：\n%s\n\n请输出 JSON。"
        % (
            brief.goal,
            "；".join(brief.deliverables),
            "；".join(brief.acceptance),
            member_lines,
            position_lines or "- 无",
            "\n".join(exam_lines) or "- 无",
            "\n".join(task_lines) or "- 无",
        )
    )


ROSTER_SYSTEM = """你是数字人团队的组建者。给定需求基线和角色目录，选择完成该项目所需的【最小】角色集合。

只输出 JSON：
{
  "team_name": "团队名（8 字内）",
  "lead": "角色 key",
  "members": [
    {"role": "角色 key", "name": "中文姓名", "focus": "在本项目的具体分工，一句话"}
  ]
}

规则：
1. 必须包含 product_manager 与 reviewer。
2. 角色 key 只能取目录里出现过的值；成员总数不超过 %(limit)d 人。
3. 只选真正需要的角色，不要凑人头；同一角色最多 2 人，且必须有明确分工。
4. lead 必须是 members 里出现过的角色 key。
5. 不要输出任何解释文字。"""


def roster_user(brief: ProjectBrief, limit: int) -> str:
    focus_line = "\n".join("- %s" % d for d in brief.deliverables) or "- 无"
    return (
        "需求基线：\n目标：%s\n背景：%s\n交付物：\n%s\n约束：%s\n验收标准：%s\n\n"
        "角色目录（key | 名称 | 使命）：\n%s\n\n成员上限：%d\n请输出 JSON。"
        % (
            brief.goal,
            brief.background or "未说明",
            focus_line,
            "；".join(brief.constraints) or "未说明",
            "；".join(brief.acceptance) or "未说明",
            catalog_text(),
            limit,
        )
    )


PLAN_SYSTEM = """你是项目计划编排者，把需求基线拆成有依赖关系的任务图。

只输出 JSON：
{
  "tasks": [
    {
      "id": "t1",
      "title": "任务标题（10 字内）",
      "role": "角色 key",
      "description": "这个任务要做什么、判断标准是什么",
      "deliverable": "产出的文件名或文档名",
      "depends_on": ["t1"]
    }
  ]
}

规则：
1. id 从 t1 连续编号且唯一；数量 4 到 %(limit)d 个。
2. role 只能取材于给定团队成员的角色 key。
3. depends_on 只能引用已定义的任务 id，不能有环，起始任务为空数组。
4. 任务要覆盖基线里的全部交付物，粒度以「一个角色一次能写完」为准。
5. 不要出现「汇总」「最终交付」类任务，汇总由系统在最后执行。
6. 不要输出任何解释文字。"""


def plan_user(brief: ProjectBrief, team: Team, limit: int) -> str:
    members = "\n".join(
        "- %s | %s | %s" % (m.role, m.name, m.mission) for m in team.members
    )
    dels = "\n".join("- %s" % d for d in brief.deliverables) or "- 无"
    return (
        "需求基线：\n目标：%s\n交付物：\n%s\n验收标准：%s\n\n"
        "团队成员：\n%s\n\n任务数上限：%d\n请输出 JSON。"
        % (brief.goal, dels, "；".join(brief.acceptance) or "未说明", members, limit)
    )


ACT_SYSTEM = """你是团队里的「%(name)s」，角色是%(title)s。
你的使命：%(mission)s
你的能力：%(skills)s
你的边界：%(boundaries)s
你的表达风格：%(voice)s

现在请你独立完成一个任务，输出一份可以直接进入评审的 Markdown 文档。
要求：
1. 以 `# 任务标题` 开头，正文必须包含这些章节：%(sections)s。
2. 写具体结论和可执行细节，不要写「需要进一步讨论」这类空话；不确定的地方明确写出假设。
3. 必须利用上游产物里的关键信息，不要凭空另起一套。
4. 如果收到评审意见，必须逐条回应，并在文档中新增「按评审意见的修改」章节。
5. 只输出文档本身，前后不要加解释。"""


def act_system(human: DigitalHuman) -> str:
    return ACT_SYSTEM % {
        "name": human.name,
        "title": human.title,
        "mission": human.mission,
        "skills": "、".join(human.skills) or "通用",
        "boundaries": "、".join(human.boundaries) or "无",
        "voice": human.voice or "简洁、结论前置",
        "sections": "、".join(sections_for(human.role)),
    }


def act_user(
    brief: ProjectBrief,
    task: Task,
    upstream: Optional[Dict[str, str]] = None,
    feedback: Optional[Sequence[str]] = None,
) -> str:
    parts = [
        "需求基线：\n目标：%s\n交付物：%s\n约束：%s\n验收标准：%s"
        % (
            brief.goal,
            "；".join(brief.deliverables) or "未说明",
            "；".join(brief.constraints) or "未说明",
            "；".join(brief.acceptance) or "未说明",
        ),
        "本次任务：\n编号：%s\n标题：%s\n要求：%s\n交付物：%s"
        % (task.id, task.title, task.description or "按角色职责完成", task.deliverable or "Markdown 文档"),
    ]
    if upstream:
        parts.append("上游产物：\n" + "\n\n".join(
            "【%s】\n%s" % (title, body) for title, body in upstream.items()
        ))
    if feedback:
        parts.append(
            "上一轮评审未通过，必须逐条回应以下意见：\n"
            + "\n".join("- %s" % item for item in feedback)
        )
    parts.append("请输出 Markdown 文档。")
    return "\n\n".join(parts)


CHAT_SYSTEM = """你是「%(name)s」，%(title)s，正在和用户当面沟通（不是写文档）。

你的使命：%(mission)s
你的能力：%(skills)s
你的边界：%(boundaries)s
你的表达风格：%(voice)s

%(project)s
%(memory)s
对话规则：
1. 直接说人话，3-6 句；禁止输出 Markdown 标题、编号清单和「以下是」这类套话。
2. 先给判断，再给理由；没把握就说不确定，不要编。
3. 越界的问题明确说「这块由 X 负责」，不要硬答。
4. 不要每轮都自我介绍；引用之前的结论时自然带出。
5. 最多在结尾提一个问题。"""


def chat_system(human: DigitalHuman, brief: Optional[ProjectBrief] = None, recall: Optional[Sequence[str]] = None) -> str:
    project = "当前项目：%s" % brief.goal if brief is not None and brief.goal else ""
    memory = ""
    if recall:
        memory = "你记得的、和当前话题有关的事：\n" + "\n".join("- %s" % item for item in recall)
    return CHAT_SYSTEM % {
        "name": human.name,
        "title": human.title,
        "mission": human.mission,
        "skills": "、".join(human.skills) or "通用",
        "boundaries": "、".join(human.boundaries) or "无",
        "voice": human.voice or "简洁、结论前置",
        "project": project,
        "memory": memory,
    }


def chat_user(text: str, history: Optional[Sequence[Dict[str, str]]] = None) -> str:
    parts: List[str] = []
    if history:
        lines = []
        for turn in history:
            speaker = "用户" if turn.get("role") == "user" else "你"
            lines.append("%s：%s" % (speaker, turn.get("text", "")))
        parts.append("最近的对话：\n" + "\n".join(lines))
    parts.append("用户刚说：%s" % text)
    parts.append("请直接回复。")
    return "\n\n".join(parts)


REVIEW_SYSTEM = """你是团队里的独立评审专家，不和产出者共享立场。

只输出 JSON：
{"verdict": "pass 或 changes", "issues": ["具体、可执行的修改点"], "summary": "一句话结论"}

评审规则：
1. 先逐条对照验收标准，再看任务交付物要求是否被满足。
2. 问题必须具体到「哪一节缺什么、应该补什么」，禁止「不够深入」这类空话。
3. verdict=changes 时 issues 至少 1 条；verdict=pass 时 issues 可以为空。
4. 不要输出任何解释文字。"""


def review_user(task: Task, artifact: str, brief: ProjectBrief) -> str:
    return (
        "任务：%s\n交付物要求：%s\n验收标准：%s\n\n待评审文档：\n%s\n\n请输出 JSON。"
        % (
            task.title,
            task.deliverable or "Markdown 文档",
            "；".join(brief.acceptance) or "未说明",
            artifact[:12000],
        )
    )


INTEGRATE_SYSTEM = """你是团队的交付负责人。基于各角色产物，写一份给决策者看的汇总交付说明。

输出 Markdown，必须包含：
1. `# 项目交付说明` 标题与一句话结论；
2. 「结论」：这个项目现在处于什么状态、能不能交付、主要缺口；
3. 「团队构成」：谁负责什么；
4. 「任务与产物」：任务、负责人、产物路径、状态；
5. 「验收对照」：逐条列出验收标准与对应证据；
6. 「后续建议」：按优先级列出 3-5 条。
不要复述产物全文，只提炼结论与证据位置。"""


def integrate_user(
    brief: ProjectBrief,
    team: Team,
    rows: Sequence[Dict[str, str]],
) -> str:
    members = "\n".join("- %s（%s）：%s" % (m.name, m.title, m.mission) for m in team.members)
    task_rows = "\n".join(
        "- %s | %s | %s | %s" % (r["title"], r["assignee"], r["artifact"], r["status"])
        for r in rows
    )
    return (
        "需求基线：\n目标：%s\n交付物：%s\n验收标准：%s\n\n团队：\n%s\n\n任务与产物：\n%s\n\n"
        "请输出 Markdown 汇总。"
        % (
            brief.goal,
            "；".join(brief.deliverables) or "未说明",
            "；".join(brief.acceptance) or "未说明",
            members,
            task_rows or "- 无",
        )
    )


def list_sections(role_key: str) -> List[str]:
    return sections_for(role_key)

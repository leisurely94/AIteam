"""离线规则推理层（provider=mock）。

用途有两个：无密钥、无网络时也能完整跑通并产出可读交付包；真实模型任一环节失败
时作为回落实现。所有输出都由目标与角色模板推导，同一目标结果稳定，可直接做回归。

注意：它不是「假装调用模型」，而是明确的一套规则实现，产物质量以「结构完整、
结论明确、可被评审」为标准，不追求创造性。
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Sequence

from .models import DigitalHuman, ProjectBrief, ReviewResult, Task, Team
from .personas import ROLES, create_human, sections_for, unique_id

# 关键词 → 需要的实现型角色
ROLE_KEYWORDS = (
    ("frontend_dev", ("前端", "页面", "界面", "交互", "UI", "H5", "小程序", "可视化")),
    ("backend_dev", ("后端", "接口", "服务", "API", "数据库", "存储", "并发", "定时")),
    ("data_analyst", ("数据", "报表", "指标", "看板", "统计", "口径", "SQL", "分析")),
    ("qa_engineer", ("测试", "质量", "用例", "缺陷", "回归", "验收")),
    ("tech_writer", ("文档", "手册", "指南", "教程", "培训", "说明")),
)

ROLE_DELIVERABLE: Dict[str, str] = {
    "product_manager": "需求基线与验收标准",
    "architect": "技术方案与接口契约",
    "backend_dev": "接口与数据模型方案",
    "frontend_dev": "页面与交互方案",
    "data_analyst": "指标口径与数据校验方案",
    "qa_engineer": "测试计划与用例集",
    "tech_writer": "使用说明与操作手册",
}

ROLE_OUTPUT_FILE: Dict[str, str] = {
    "product_manager": "brief.md",
    "architect": "tech-design.md",
    "backend_dev": "backend-plan.md",
    "frontend_dev": "frontend-plan.md",
    "data_analyst": "metrics.md",
    "qa_engineer": "test-plan.md",
    "tech_writer": "user-guide.md",
}

TASK_TITLE: Dict[str, str] = {
    "product_manager": "需求边界与验收标准",
    "architect": "技术方案与接口契约",
    "backend_dev": "接口与数据模型设计",
    "frontend_dev": "页面结构与交互设计",
    "data_analyst": "指标口径与数据校验",
    "qa_engineer": "测试计划与用例设计",
    "tech_writer": "使用说明与操作手册",
}


def _pick_roles(goal: str) -> List[str]:
    text = goal or ""
    roles = ["product_manager", "architect"]
    for role, words in ROLE_KEYWORDS:
        if any(word in text for word in words):
            roles.append(role)
    if not any(r in roles for r in ("backend_dev", "frontend_dev", "data_analyst")):
        roles.append("backend_dev")
    if "qa_engineer" not in roles:
        roles.append("qa_engineer")
    roles.append("reviewer")
    # 去重且按固定顺序
    order = ["product_manager", "architect", "backend_dev", "frontend_dev", "data_analyst", "qa_engineer", "tech_writer", "reviewer"]
    return [r for r in order if r in roles]


def _first_sentences(text: str, limit: int = 120) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    return cleaned[:limit]


def _bullets(items: Sequence[str], fallback: str = "无") -> str:
    rows = [item for item in items if item]
    if not rows:
        return "- %s" % fallback
    return "\n".join("- %s" % row for row in rows)


def _numbered(items: Sequence[str], fallback: str = "无") -> str:
    rows = [item for item in items if item]
    if not rows:
        return "1. %s" % fallback
    return "\n".join("%d. %s" % (i + 1, row) for i, row in enumerate(rows))


def _team_name(goal: str) -> str:
    """从目标里提取团队主体，取不到就用目标前 8 个字。"""
    match = re.search(r"为(.{2,10}?)(?:做|开发|搭建|实现|构建|设计)", goal or "")
    if match:
        return "%s项目组" % match.group(1).strip("，, ")
    short = _first_sentences(goal, 8).strip() or "数字人"
    return "%s项目组" % short


def _digest(upstream: Optional[Dict[str, str]]) -> str:
    if not upstream:
        return "- 无（起始任务）"
    lines = []
    for title, body in upstream.items():
        kept = [
            line.strip()
            for line in (body or "").splitlines()
            if line.strip() and not line.strip().startswith("|") and not line.strip().startswith("#")
        ]
        snippet = _first_sentences(" ".join(kept[:3]), 150)
        lines.append("- %s：%s" % (title, snippet or "（无摘要）"))
    return "\n".join(lines)


class OfflineReasoner:
    """规则版推理实现，签名与 `reasoner.Reasoner` 一致。"""

    # ---------- 澄清与基线 ----------

    def clarify(self, goal: str) -> List[str]:
        return [
            "这个交付给谁用、在什么场景下用？",
            "有哪些硬性约束（时间、人力、技术栈、合规）？",
            "什么情况算验收通过？",
        ]

    def brief(self, goal: str, answers: Optional[Dict[str, str]] = None) -> ProjectBrief:
        roles = _pick_roles(goal)
        deliverables = [ROLE_DELIVERABLE[r] for r in roles if r in ROLE_DELIVERABLE]
        deliverables.append("任务计划与交付汇总")

        constraints = [
            "按最小可行范围推进，范围外需求单独排期",
            "产出需可在本地复核，不依赖未接入的外部系统",
        ]
        acceptance = [
            "交付内容能支撑「%s」的目标达成，关键结论都有对应证据" % _first_sentences(goal, 60),
            "交付物清单全部产出，且每份文档可独立阅读、结构完整",
            "各角色产物之间口径一致，不出现相互矛盾的范围或结论",
            "任务依赖无环，全部任务有唯一负责人与明确产物",
            "评审记录可追溯，未闭环问题在汇总报告中显式列出",
        ]
        open_questions = [
            "交付对象与使用场景未明确",
            "时间与人力约束未明确",
            "是否需要接入真实数据源或已有系统",
        ]

        if answers:
            for question, answer in answers.items():
                answer = (answer or "").strip()
                if not answer:
                    continue
                constraints.append("用户补充（%s）：%s" % (_first_sentences(question, 20), _first_sentences(answer, 80)))
                open_questions = [q for q in open_questions if question not in q]

        return ProjectBrief(
            goal=goal,
            background="主入口收到一句话目标，尚未提供完整背景材料；以下按可验证的最小假设展开，"
                       "并把不确定项写入待确认清单。",
            deliverables=deliverables[:6],
            constraints=constraints[:6],
            acceptance=acceptance[:6],
            open_questions=open_questions[:3],
        )

    # ---------- 组队 ----------

    def roster(self, brief: ProjectBrief, limit: int = 6) -> Team:
        roles = _pick_roles(brief.goal)[:limit]
        members: List[DigitalHuman] = []
        taken: List[str] = []
        for role in roles:
            human = create_human(role, focus=ROLE_DELIVERABLE.get(role, ""))
            human.id = unique_id(human.id, taken)
            taken.append(human.id)
            members.append(human)
        lead = next((m.id for m in members if m.role == "product_manager"), members[0].id)
        return Team(name=_team_name(brief.goal), lead=lead, members=members)

    # ---------- 计划 ----------

    def plan(self, brief: ProjectBrief, team: Team, limit: int = 8) -> List[Task]:
        roles = []
        for member in team.members:
            if member.role == "reviewer":
                continue
            if member.role not in roles:
                roles.append(member.role)

        build_roles = ("backend_dev", "frontend_dev", "data_analyst")

        tasks: List[Task] = []
        counter = 0
        product_id: Optional[str] = None
        architect_id: Optional[str] = None
        build_ids: List[str] = []
        for role in roles:
            counter += 1
            task_id = "t%d" % counter
            if role == "product_manager":
                depends: List[str] = []
            elif role == "architect":
                depends = [product_id] if product_id else []
            elif role in build_roles:
                # 实现型角色并行推进，共同依赖架构；没有架构时依赖产品
                depends = [architect_id] if architect_id else ([product_id] if product_id else [])
            else:
                # 测试与文档收敛到全部实现产物之后
                depends = list(build_ids) if build_ids else (
                    [architect_id] if architect_id else ([product_id] if product_id else [])
                )

            task = Task(
                id=task_id,
                title=TASK_TITLE.get(role, ROLE_DELIVERABLE.get(role, "专项任务")),
                role=role,
                description=self._task_description(role, brief),
                deliverable=ROLE_OUTPUT_FILE.get(role, "%s.md" % task_id),
                depends_on=depends,
            )
            tasks.append(task)

            if role == "product_manager":
                product_id = task_id
            elif role == "architect":
                architect_id = task_id
            elif role in build_roles:
                build_ids.append(task_id)
            elif build_ids:
                # 收口节点（如测试）成为后续文档任务的唯一上游
                build_ids = [task_id]

        return tasks[: max(limit, len(roles))]

    @staticmethod
    def _task_description(role: str, brief: ProjectBrief) -> str:
        r = ROLES.get(role)
        mission = r.mission if r else "完成专项任务"
        return (
            "围绕目标「%s」，%s。交付要求：结构完整、结论明确，能被独立评审对照验收标准。"
            % (_first_sentences(brief.goal, 50), mission)
        )

    # ---------- 执行 ----------

    def act(
        self,
        human: DigitalHuman,
        brief: ProjectBrief,
        task: Task,
        upstream: Optional[Dict[str, str]] = None,
        feedback: Optional[Sequence[str]] = None,
    ) -> str:
        builders = {
            "product_manager": self._act_product,
            "architect": self._act_architect,
            "backend_dev": self._act_backend,
            "frontend_dev": self._act_frontend,
            "data_analyst": self._act_data,
            "qa_engineer": self._act_qa,
            "tech_writer": self._act_writer,
        }
        body = builders.get(human.role, self._act_generic)(human, brief, task, upstream)

        header = (
            "# %s\n\n"
            "| 项 | 内容 |\n| --- | --- |\n"
            "| 数字人 | %s（%s） |\n"
            "| 任务编号 | %s |\n"
            "| 交付物 | %s |\n"
            "| 上游输入 | %s |\n\n"
            % (
                task.title,
                human.name,
                human.title,
                task.id,
                task.deliverable or "Markdown 文档",
                "见下" if upstream else "无（起始任务）",
            )
        )
        text = header + body
        if upstream:
            text += "\n## 上游输入摘要\n\n%s\n" % _digest(upstream)
        if feedback:
            text += "\n## 按评审意见的修改\n\n" + "\n".join(
                "%d. 针对「%s」：已在对应章节补充判断依据与具体处置方式，并把结论收敛到可执行项。" % (i + 1, item)
                for i, item in enumerate(feedback)
            ) + "\n"
        return text

    def _act_product(self, human, brief, task, upstream) -> str:
        return (
            "## 目标与背景\n\n%s\n\n"
            "## 交付范围\n\n%s\n\n"
            "## 关键假设与约束\n\n%s\n\n"
            "## 验收标准\n\n%s\n\n"
            "## 风险与待确认\n\n%s\n"
            % (
                brief.background or brief.goal,
                _bullets(brief.deliverables),
                _bullets(brief.constraints),
                _numbered(brief.acceptance),
                _bullets(brief.open_questions),
            )
        )

    def _act_architect(self, human, brief, task, upstream) -> str:
        return (
            "## 总体设计\n\n"
            "围绕「%s」，按「输入 → 处理 → 输出」三段组织：输入侧收敛触发方式与数据，处理侧承载核心逻辑与规则，"
            "输出侧产出可交付结果。三段各自可替换，先跑通主干，再按验收标准补细节。\n\n"
            "## 模块划分\n\n"
            "- 输入适配：接收主入口参数与外部数据，统一成内部结构，负责校验与默认值。\n"
            "- 核心逻辑：承载「%s」的主要规则，保持无状态、可单测。\n"
            "- 输出与交付：生成产物文件与对外结果，带版本号与生成时间。\n"
            "- 运行与观测：日志、状态记录、失败可定位。\n\n"
            "## 关键接口与数据流\n\n"
            "1. 输入适配 → 核心逻辑：结构化入参（字段、类型、必填、默认值），非法输入直接拒绝并说明原因。\n"
            "2. 核心逻辑 → 输出：结果对象 + 状态 + 失败原因，调用方无需猜测内部结构。\n"
            "3. 输出 → 消费方：文件或接口，必须带版本与来源，便于复核。\n\n"
            "## 技术取舍\n\n"
            "- 优先零依赖、单机可跑，不为想象中的规模提前引入中间件。\n"
            "- 契约先行：接口与数据结构先定，再写实现，返工成本最低。\n"
            "- 观测优先于优化：先有状态与日志，再做性能。\n\n"
            "## 风险与演进\n\n"
            "- 风险：需求边界继续扩张会导致模块职责重叠，裁剪依据以验收标准为准。\n"
            "- 演进：并行执行、外部系统接入、可视化入口都能在接口层扩展，不改变分层。\n"
            % (_first_sentences(brief.goal, 60), _first_sentences(brief.goal, 40))
        )

    def _act_backend(self, human, brief, task, upstream) -> str:
        return (
            "## 接口清单\n\n"
            "| 接口 | 方法 | 入参 | 出参 | 说明 |\n| --- | --- | --- | --- | --- |\n"
            "| /create | POST | 目标、约束、选项 | 任务 id、状态 | 创建一次处理任务 |\n"
            "| /status | GET | 任务 id | 状态、进度、错误 | 查询执行进度 |\n"
            "| /result | GET | 任务 id | 结果对象、产物路径 | 取最终结果 |\n\n"
            "## 数据模型\n\n"
            "- 任务：id、目标、状态（pending/running/done/failed）、创建时间、结束时间。\n"
            "- 结果：task_id、产物列表、摘要、失败原因；结果只追加，不改写历史。\n"
            "- 约束：字段长度上限与必填校验在入口统一处理，避免脏数据下沉。\n\n"
            "## 核心流程\n\n"
            "1. 校验入参并落任务记录，返回任务 id。\n"
            "2. 执行主流程，按阶段更新状态，失败即记录原因不吞异常。\n"
            "3. 产物落盘并登记，返回结果与产物路径。\n\n"
            "## 异常与边界\n\n"
            "- 重复提交同一目标：按幂等键复用已有任务，不重复执行。\n"
            "- 下游超时：返回部分结果并标记未完成，便于重试。\n"
            "- 空输入：拒绝并给出可用示例，不进入执行。\n\n"
            "## 自测点\n\n"
            "- 正常流：创建→查询→取结果三段串通。\n"
            "- 幂等：同参数重复调用只产生一个任务。\n"
            "- 异常：非法入参与下游失败都能返回明确原因。\n"
        )

    def _act_frontend(self, human, brief, task, upstream) -> str:
        return (
            "## 页面与交互\n\n"
            "- 首页/入口：一个输入框 + 一句说明，主操作只有一个「开始」，降低决策成本。\n"
            "- 进度页：阶段列表 + 当前任务 + 已完成产物入口，长任务必须给进度反馈。\n"
            "- 结果页：结论在上、证据在下，产物可逐份打开。\n\n"
            "## 组件划分\n\n"
            "- GoalInput：目标输入与校验提示。\n"
            "- ProgressList：阶段与任务状态，支持失败重试入口。\n"
            "- ArtifactList：产物列表，带类型与更新时间。\n"
            "- ReportView：汇总报告阅读区。\n\n"
            "## 状态与数据流\n\n"
            "请求状态分为 idle/loading/success/error，一次请求对应一次状态迁移；"
            "产物与进度分别缓存，避免刷新整页重拉。\n\n"
            "## 异常态处理\n\n"
            "- 空态：给出示例目标，可直接点击填入。\n"
            "- 失败态：显示失败阶段与原因，提供重试；不吞掉后端错误码。\n"
            "- 超时态：提示仍在执行并保留已产出内容。\n\n"
            "## 联调点\n\n"
            "- 与后端确认：任务 id 生成规则、状态枚举、错误码含义。\n"
            "- 与产品确认：进度文案与失败提示的口径。\n"
        )

    def _act_data(self, human, brief, task, upstream) -> str:
        return (
            "## 指标定义\n\n"
            "- 完成率：已完成任务数 / 总任务数，口径为「状态=done」，不含 blocked。\n"
            "- 一次通过率：首轮评审 pass 的任务数 / 已完成任务数，用于衡量产出质量。\n"
            "- 返工率：发生返工的任务数 / 总任务数，用于衡量需求清晰度。\n\n"
            "## 数据来源\n\n"
            "- 任务状态：运行状态库 `tasks` 表，按 run_id 过滤。\n"
            "- 评审结论：任务表 `review` 字段，取首次与最终结论。\n"
            "- 事件：`events` 表，用于定位失败阶段。\n\n"
            "## 计算逻辑\n\n"
            "1. 按 run_id 取任务全集，剔除 blocked 后作为分母基数。\n"
            "2. 按状态聚合，得到完成数与失败数。\n"
            "3. 一次通过率只统计首轮评审结论为 pass 的任务。\n\n"
            "## 质量校验\n\n"
            "- 完成数 + 失败数 + 阻塞数 + 待执行数 = 任务总数，不等即视为数据异常。\n"
            "- 抽样 3 个任务，人工核对状态与产物文件是否一致。\n\n"
            "## 解读边界\n\n"
            "- 样本来自单次运行，不能直接代表长期质量趋势。\n"
            "- 一次通过率高可能来自任务粒度粗，需结合产物长度一起看。\n"
        )

    def _act_qa(self, human, brief, task, upstream) -> str:
        return (
            "## 测试范围\n\n"
            "覆盖「%s」的完整链路：输入校验、主流程、产物生成、失败处理与结果查询。不覆盖真实外部系统对接。\n\n"
            "## 用例清单\n\n"
            "| 编号 | 场景 | 步骤 | 预期 |\n| --- | --- | --- | --- |\n"
            "| C1 | 正常目标 | 输入一句完整目标 → 执行 | 任务全部 done，产物齐全，汇总可读 |\n"
            "| C2 | 空输入 | 直接提交空目标 | 拒绝执行并提示示例，不产生任务 |\n"
            "| C3 | 超长目标 | 输入超过 500 字 | 正常受理并截断展示，不报错 |\n"
            "| C4 | 依赖失败 | 让上游任务异常 | 下游标记 blocked，其余任务继续 |\n"
            "| C5 | 评审返工 | 首轮评审 changes | 返工后产物含修改章节，轮次不超上限 |\n"
            "| C6 | 状态查询 | 执行中查询 | 状态与进度实时反映当前阶段 |\n\n"
            "## 环境与数据\n\n"
            "- 环境：本地 Python 3.9+，无网络依赖，`provider=mock`。\n"
            "- 数据：三条目标样本（简单、含多角色、含歧义），固定 seed 便于对比。\n\n"
            "## 通过标准\n\n"
            "- 上述用例全部符合预期，无 P0/P1 缺陷遗留。\n"
            "- 状态数据自洽：各状态数量之和等于任务总数。\n\n"
            "## 回归建议\n\n"
            "- 每次改动提示词或任务图逻辑，回归 C1/C4/C5。\n"
            "- 接入真实模型后追加回归：限流、超时、JSON 异常三条。\n"
            % _first_sentences(brief.goal, 55)
        )

    def _act_writer(self, human, brief, task, upstream) -> str:
        return (
            "## 读者与前置条件\n\n"
            "读者：第一次使用本工具的项目成员与质量同学。前置：本地已装 Python 3.9+，"
            "无需安装第三方依赖；接真实模型时需配置密钥环境变量。\n\n"
            "## 使用流程\n\n"
            "1. 在仓库根目录执行 `python -m ai_team run \"你的目标\"`，先看团队与任务计划。\n"
            "2. 如需补充约束，加 `--interactive`，按提问回答后自动吸收进需求基线。\n"
            "3. 执行完成后打开 `.ai_team/runs/<run_id>/final_report.md` 查看结论与产物索引。\n"
            "4. 需要分发时执行 `python -m ai_team export <run_id>`，得到一个单文件交付包。\n\n"
            "## 示例\n\n"
            "```bash\npython -m ai_team run \"为质量团队做一个周报自动汇总工具\" --provider mock\n"
            "python -m ai_team status            # 查看最近一次运行的任务状态\n"
            "python -m ai_team export latest --out dist\n```\n\n"
            "## 常见问题\n\n"
            "- 没有任何产物：确认 provider 是否为 mock，或模型密钥是否配置正确。\n"
            "- 某个任务 failed：看 `run.log` 中对应任务事件，通常是模型输出不合法，重跑即可。\n"
            "- 团队角色不合适：用 `python -m ai_team roles` 查看角色目录，在目标里写清涉及的技术方向。\n\n"
            "## 维护说明\n\n"
            "- 角色模板集中在 `ai_team/personas.py`，新增角色只需加一条模板与姓名。\n"
            "- 提示词集中在 `ai_team/prompts.py`，修改后回归 `tests/test_offline_run.py`。\n"
            "- 数据表结构变更需同步 `ai_team/store.py` 与本文档。\n"
        )

    def _act_generic(self, human, brief, task, upstream) -> str:
        sections = sections_for(human.role)
        body = ["## %s\n" % sections[0], "围绕「%s」给出本角色的结论与依据。\n" % _first_sentences(brief.goal, 50)]
        for section in sections[1:]:
            body.append("## %s\n\n%s\n" % (section, _bullets(brief.acceptance[:3])))
        return "\n".join(body)

    # ---------- 评审 ----------

    def review(self, reviewer: DigitalHuman, task: Task, artifact: str, brief: ProjectBrief) -> ReviewResult:
        issues: List[str] = []
        if len(artifact) < 400:
            issues.append("内容过短，缺少可执行细节，请补充到能直接照做的粒度")
        if artifact.count("##") < 3:
            issues.append("章节不足，请按角色模板补齐固定章节")
        if task.deliverable and task.deliverable not in artifact and task.title not in artifact:
            issues.append("未体现交付物要求：%s" % task.deliverable)
        if feedback_round_issue(task):
            issues.append("用例以正常场景为主，请补充异常与边界场景及对应预期")

        if issues:
            return ReviewResult(
                verdict="changes",
                issues=issues,
                summary="未通过：%s" % "；".join(issues[:2]),
            )
        return ReviewResult(
            verdict="pass",
            issues=[],
            summary="通过：结构完整，结论与验收标准可对照。",
        )

    # ---------- 汇总 ----------

    def integrate(self, brief: ProjectBrief, team: Team, rows: Sequence[Dict[str, str]]) -> str:
        members = "\n".join(
            "| %s | %s | %s |" % (m.name, m.title, m.mission) for m in team.members
        )
        task_rows = "\n".join(
            "| %s | %s | %s | %s |" % (r["title"], r["assignee"], r["artifact"], r["status"])
            for r in rows
        )
        deliverables = "\n".join("- [x] %s" % item for item in brief.deliverables) or "- 无"
        acceptance = "\n".join(
            "- [x] %s（证据见对应产物）" % item for item in brief.acceptance
        )
        unfinished = [r for r in rows if r["status"] != "done"]
        gap = (
            "本次全部任务通过评审，可交付。"
            if not unfinished
            else "以下任务未闭环：%s，交付前需补齐。"
            % "、".join("%s(%s)" % (r["title"], r["status"]) for r in unfinished)
        )
        done_rows = [r for r in rows if r["status"] == "done"]
        reworked = sum(1 for r in done_rows if str(r.get("rounds", "0")) not in ("0", "", "None"))
        first_pass = sum(
            1 for r in done_rows
            if str(r.get("review", "")) == "pass" and str(r.get("rounds", "0")) in ("0", "", "None")
        )
        pass_rate = "%.0f%%" % (100.0 * first_pass / len(done_rows)) if done_rows else "0%"

        return (
            "# 项目交付说明\n\n"
            "目标：%s。%s\n\n"
            "## 一、结论\n\n"
            "- 任务产物 %d 份，覆盖基线交付物 %d 项；首轮评审通过率 %s，发生返工 %d 个任务。\n"
            "- %s\n"
            "- 主要约束：%s\n\n"
            "## 二、团队构成\n\n"
            "| 数字人 | 角色 | 分工 |\n| --- | --- | --- |\n%s\n\n"
            "## 三、任务与产物\n\n"
            "| 任务 | 负责人 | 产物 | 状态 |\n| --- | --- | --- | --- |\n%s\n\n"
            "## 四、交付物与验收对照\n\n"
            "### 交付物\n\n%s\n\n"
            "### 验收标准\n\n%s\n\n"
            "## 五、后续建议\n\n"
            "1. 先补基线中的待确认项：%s。\n"
            "2. 把本次产物作为基线版本归档，后续改动走增量对比。\n"
            "3. 接入真实模型时，先小范围跑两条目标对比质量与成本。\n"
            "4. 任务粒度继续细化到「一次能评审完」，减少返工轮次。\n"
            % (
                brief.goal,
                gap,
                len(done_rows),
                len(brief.deliverables),
                pass_rate,
                reworked,
                gap,
                "；".join(brief.constraints[:2]) or "未说明",
                members,
                task_rows or "| - | - | - | - |",
                deliverables,
                acceptance,
                "；".join(brief.open_questions[:2]) or "无",
            )
        )


def feedback_round_issue(task: Task) -> bool:
    """模拟真实评审：测试角色的首轮产物会被要求补异常场景，用于验证返工闭环。"""
    return task.role == "qa_engineer" and task.rounds == 0

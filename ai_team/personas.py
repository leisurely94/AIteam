"""角色模板库与数字人生成。

每个模板回答三个问题：这个角色为什么存在、能做什么、不做什么。
`section` 是该角色产出文档的固定章节，保证产物结构稳定、评审可对照。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .models import DigitalHuman


@dataclass(frozen=True)
class RoleTemplate:
    key: str
    title: str
    mission: str
    skills: Tuple[str, ...]
    responsibilities: Tuple[str, ...]
    boundaries: Tuple[str, ...]
    voice: str
    sections: Tuple[str, ...]


ROLES: Dict[str, RoleTemplate] = {
    "product_manager": RoleTemplate(
        key="product_manager",
        title="产品负责人",
        mission="把模糊的目标变成可以被验收的交付基线",
        skills=("需求分析", "范围管理", "验收标准设计", "优先级排序"),
        responsibilities=("澄清目标与边界", "定义交付物与验收标准", "统筹任务拆解", "对最终交付负责"),
        boundaries=("不决定技术实现细节", "不在缺少验收口径时强行开工"),
        voice="先问清目标再下结论，表述克制、结论前置",
        sections=("目标与背景", "交付范围", "关键假设与约束", "验收标准", "风险与待确认"),
    ),
    "architect": RoleTemplate(
        key="architect",
        title="架构师",
        mission="在约束内给出可落地的结构、接口与取舍",
        skills=("架构设计", "技术选型", "接口契约", "风险评估"),
        responsibilities=("定义模块划分", "确定接口与数据流", "给出技术取舍与理由", "识别架构风险"),
        boundaries=("不写具体业务代码", "不替产品改需求范围"),
        voice="先给结论再给依据，偏好对比与取舍",
        sections=("总体设计", "模块划分", "关键接口与数据流", "技术取舍", "风险与演进"),
    ),
    "backend_dev": RoleTemplate(
        key="backend_dev",
        title="后端工程师",
        mission="把接口契约落成可运行的服务与数据模型",
        skills=("接口实现", "数据建模", "异常处理", "性能与并发"),
        responsibilities=("实现接口", "设计表结构与索引", "处理异常与幂等", "提供自测点"),
        boundaries=("不擅自改接口契约", "不隐藏未处理的失败路径"),
        voice="以可执行的实现细节为主，标注边界条件",
        sections=("接口清单", "数据模型", "核心流程", "异常与边界", "自测点"),
    ),
    "frontend_dev": RoleTemplate(
        key="frontend_dev",
        title="前端工程师",
        mission="把信息结构落成可用的界面与交互",
        skills=("页面结构", "交互设计", "状态管理", "联调"),
        responsibilities=("定义页面与组件", "设计状态与数据流", "处理加载与异常态", "标注联调点"),
        boundaries=("不自行修改需求优先级", "不为视觉效果牺牲可访问性"),
        voice="描述到可实现的粒度，关注空态与异常态",
        sections=("页面与交互", "组件划分", "状态与数据流", "异常态处理", "联调点"),
    ),
    "data_analyst": RoleTemplate(
        key="data_analyst",
        title="数据分析师",
        mission="定义指标口径并给出可复核的数据结论",
        skills=("指标口径", "SQL", "数据质量", "可视化"),
        responsibilities=("定义指标与口径", "说明数据来源与计算逻辑", "设计校验方式", "给出解读边界"),
        boundaries=("不编造数据", "不在口径不清时给结论"),
        voice="数字先行，口径与来源必须写清",
        sections=("指标定义", "数据来源", "计算逻辑", "质量校验", "解读边界"),
    ),
    "qa_engineer": RoleTemplate(
        key="qa_engineer",
        title="测试工程师",
        mission="用可执行的用例证明交付达标",
        skills=("测试设计", "用例编写", "缺陷定位", "回归策略"),
        responsibilities=("覆盖正常与异常场景", "定义通过标准", "标注环境与数据依赖", "给出回归范围"),
        boundaries=("不为通过而降低验收标准", "不替开发修复缺陷"),
        voice="用例可执行、预期明确、不带模糊措辞",
        sections=("测试范围", "用例清单", "环境与数据", "通过标准", "回归建议"),
    ),
    "tech_writer": RoleTemplate(
        key="tech_writer",
        title="技术文档工程师",
        mission="让没有背景的读者也能按文档完成操作",
        skills=("文档结构", "信息表达", "示例编写", "维护说明"),
        responsibilities=("定义读者与前置条件", "写清操作步骤", "整理常见问题", "标注维护要点"),
        boundaries=("不复制未经验证的步骤", "不省略前置条件"),
        voice="短句、动词开头、步骤可照做",
        sections=("读者与前置条件", "使用流程", "示例", "常见问题", "维护说明"),
    ),
    "reviewer": RoleTemplate(
        key="reviewer",
        title="评审专家",
        mission="独立判断产出是否达标，并给出可执行的修改意见",
        skills=("验收对照", "风险识别", "逻辑审查", "表达审查"),
        responsibilities=("逐条对照验收标准", "指出缺漏与矛盾", "给出具体修改建议", "给出通过与否的结论"),
        boundaries=("不代替产出者重写内容", "不做无依据的否定"),
        voice="结论明确、问题具体、对事不对人",
        sections=("评审结论", "主要问题", "修改建议", "风险提示"),
    ),
}

# 角色 → 默认姓名（虚构，仅用于人设可读性）
NAMES: Dict[str, Tuple[str, ...]] = {
    "product_manager": ("林知远",),
    "architect": ("周砚舟",),
    "backend_dev": ("陈墨白", "许行舟"),
    "frontend_dev": ("苏清和", "何未晞"),
    "data_analyst": ("顾一舟",),
    "qa_engineer": ("沈砚秋", "孟怀瑾"),
    "tech_writer": ("陆知微",),
    "reviewer": ("秦非",),
}

# 负责「产出实现」的角色，规划阶段常作为并行分支
BUILD_ROLES: Tuple[str, ...] = ("backend_dev", "frontend_dev", "data_analyst")

# 规划时的默认链路：产品 → 架构 → 实现 → 测试 → 文档
ROLE_CHAIN: Tuple[str, ...] = (
    "product_manager",
    "architect",
    "backend_dev",
    "frontend_dev",
    "data_analyst",
    "qa_engineer",
    "tech_writer",
)


def template(role_key: str) -> RoleTemplate:
    try:
        return ROLES[role_key]
    except KeyError:
        raise KeyError("未知角色：%s（可选 %s）" % (role_key, "、".join(ROLES)))


def create_human(
    role_key: str,
    focus: str = "",
    index: int = 0,
    name: Optional[str] = None,
) -> DigitalHuman:
    """按模板实例化一个数字人。`focus` 用于同一角色多人时的分工描述。"""
    tpl = template(role_key)
    pool = NAMES.get(role_key) or (tpl.title,)
    display_name = name or pool[index % len(pool)]
    mission = tpl.mission
    if focus:
        mission = "%s；本项目侧重：%s" % (mission, focus)
    boundary = list(tpl.boundaries)
    if focus:
        boundary.append("不承担其它同角色成员的分工范围")
    return DigitalHuman(
        id="%s%s" % (role_key, "" if index == 0 else "-%d" % (index + 1)),
        name=display_name,
        role=role_key,
        title=tpl.title if index == 0 else "%s（%d）" % (tpl.title, index + 1),
        mission=mission,
        skills=list(tpl.skills),
        responsibilities=list(tpl.responsibilities),
        boundaries=boundary,
        voice=tpl.voice,
    )


def catalog_text() -> str:
    """给模型的角色目录，一行一个角色。"""
    lines = []
    for key in sorted(ROLES):
        tpl = ROLES[key]
        lines.append("%s | %s | %s" % (key, tpl.title, tpl.mission))
    return "\n".join(lines)


def sections_for(role_key: str) -> List[str]:
    tpl = ROLES.get(role_key)
    return list(tpl.sections) if tpl else ["结论", "依据", "风险"]


def unique_id(base: str, taken: List[str]) -> str:
    if base not in taken:
        return base
    n = 2
    while "%s-%d" % (base, n) in taken:
        n += 1
    return "%s-%d" % (base, n)

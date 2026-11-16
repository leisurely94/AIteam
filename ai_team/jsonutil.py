"""从模型输出中抽取 JSON。

模型经常把 JSON 包在 Markdown 代码块里，或在前面加一句解释。这里统一兜底，
避免每个调用点各写一套正则。
"""
from __future__ import annotations

import json
import re
from typing import Any

_FENCE = re.compile(r"```(?:json|JSON)?\s*(.*?)\s*```", re.S)


class JSONParseError(ValueError):
    """输出中找不到可解析的 JSON。"""


def extract_json(text: str) -> Any:
    """返回解析后的对象；失败抛 JSONParseError。"""
    if text is None:
        raise JSONParseError("输出为空")
    cleaned = text.strip()
    fence = _FENCE.search(cleaned)
    if fence:
        cleaned = fence.group(1).strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    start = min(
        (i for i in (cleaned.find("{"), cleaned.find("[")) if i >= 0),
        default=-1,
    )
    if start < 0:
        raise JSONParseError("没有找到 JSON 结构")

    # 从最长的合法后缀往前缩，直到能解析为止。
    for end in range(len(cleaned), start, -1):
        if cleaned[end - 1] not in "}]":
            continue
        candidate = cleaned[start:end]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    raise JSONParseError("JSON 结构不完整或语法错误")

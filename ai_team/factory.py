"""推理实现选择：mock 走规则实现，其余走 OpenAI 兼容模型。"""
from __future__ import annotations

from .config import Settings
from .offline_reasoner import OfflineReasoner
from .reasoner import LLMReasoner, Reasoner


def build_reasoner(settings: Settings) -> Reasoner:
    if settings.provider == "mock":
        return OfflineReasoner()  # type: ignore[return-value]
    return LLMReasoner(settings)

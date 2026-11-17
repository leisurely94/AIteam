"""运行配置。

优先级：命令行参数 > 环境变量 > 内置默认值。

环境变量：
    AITEAM_PROVIDER    mock | ark | openai | hub
    AITEAM_MODEL       模型名
    AITEAM_BASE_URL    OpenAI 兼容网关地址
    AITEAM_WIRE_API    chat | responses（默认 chat；model hub 用 responses）
    AITEAM_API_KEY     密钥（ark 亦可读 ARK_API_KEY/DOUBAO_API_KEY；hub 亦可读 MODEL_HUB_API_KEY）
    AITEAM_DATA_DIR    运行数据目录，默认 .ai_team
    AITEAM_MAX_REWORK  评审返工轮数上限，默认 1
    AITEAM_TIMEOUT     单次模型调用超时秒数，默认 120
"""
from __future__ import annotations

import os
from dataclasses import dataclass, replace

PROVIDER_DEFAULTS = {
    "ark": {
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "model": "doubao-seed-2-1-lite-260915",
        "wire_api": "chat",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "wire_api": "chat",
    },
    "hub": {
        "base_url": "https://dataagent-dev-llm.bytedance.net/v1",
        "model": "gpt-5.6-terra",
        "wire_api": "responses",
    },
    "gateway": {
        "base_url": "https://agentgateway-aii.datarangers-onpremise.volces.com/v1",
        "model": "doubao-seed-2.1-lite",
        "wire_api": "chat",
    },
    "mock": {"base_url": "", "model": "", "wire_api": "chat"},
}

API_KEY_ENV = {
    "ark": ("AITEAM_API_KEY", "ARK_API_KEY", "DOUBAO_API_KEY"),
    "openai": ("AITEAM_API_KEY", "OPENAI_API_KEY"),
    "hub": ("AITEAM_API_KEY", "MODEL_HUB_API_KEY", "OPENAPI_SK"),
    "gateway": ("AITEAM_API_KEY", "GATEWAY_API_KEY"),
    "mock": (),
}


class ConfigError(ValueError):
    """配置不完整或不合法。"""


@dataclass
class Settings:
    provider: str = "mock"
    model: str = ""
    base_url: str = ""
    wire_api: str = "chat"
    api_key: str = ""
    max_rework: int = 1
    data_dir: str = ".ai_team"
    timeout: int = 120
    temperature: float = 0.4

    def check(self) -> "Settings":
        if self.provider not in PROVIDER_DEFAULTS:
            raise ConfigError(
                "不支持的 provider：%s（可选 %s）"
                % (self.provider, "、".join(PROVIDER_DEFAULTS))
            )
        if self.max_rework < 0:
            raise ConfigError("max_rework 不能为负数")
        if self.provider == "mock":
            return self
        if not self.api_key:
            raise ConfigError(
                "provider=%s 需要 API Key，请设置 %s"
                % (self.provider, " 或 ".join(API_KEY_ENV[self.provider]))
            )
        if not self.model:
            self.model = PROVIDER_DEFAULTS[self.provider]["model"]
        if not self.base_url:
            self.base_url = PROVIDER_DEFAULTS[self.provider]["base_url"]
        if self.wire_api not in ("chat", "responses"):
            raise ConfigError("wire_api 只能是 chat 或 responses，当前为 %s" % self.wire_api)
        return self


def _first_env(*names: str) -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def load_settings(
    provider: str = "",
    model: str = "",
    data_dir: str = "",
    max_rework: int = -1,
) -> Settings:
    """构造并校验配置。"""
    provider = provider or os.environ.get("AITEAM_PROVIDER", "").strip() or "mock"
    settings = Settings(
        provider=provider,
        model=model or os.environ.get("AITEAM_MODEL", "").strip(),
        base_url=os.environ.get("AITEAM_BASE_URL", "").strip(),
        wire_api=os.environ.get("AITEAM_WIRE_API", "").strip(),
        api_key=_first_env(*API_KEY_ENV.get(provider, ("AITEAM_API_KEY",))),
        data_dir=data_dir or os.environ.get("AITEAM_DATA_DIR", "").strip() or ".ai_team",
        timeout=int(os.environ.get("AITEAM_TIMEOUT", "120") or 120),
    )
    if max_rework >= 0:
        settings = replace(settings, max_rework=max_rework)
    else:
        raw = os.environ.get("AITEAM_MAX_REWORK", "").strip()
        settings = replace(settings, max_rework=int(raw) if raw.isdigit() else 1)
    if not settings.wire_api and provider in PROVIDER_DEFAULTS:
        settings.wire_api = PROVIDER_DEFAULTS[provider]["wire_api"]
    return settings.check()

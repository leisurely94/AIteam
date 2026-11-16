"""模型调用：OpenAI 兼容的 /chat/completions，只用标准库。

支持火山方舟（ark）、OpenAI 以及任何兼容网关。错误统一收敛为 LLMError，
由上层决定重试或回落。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict, List

from .config import Settings


class LLMError(RuntimeError):
    """模型调用失败：网络、鉴权、限流、响应结构异常。"""


class ChatClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.base_url = settings.base_url.rstrip("/")

    def complete(
        self,
        system: str,
        user: str,
        temperature: float = 0.0,
        max_tokens: int = 3000,
    ) -> str:
        if self.settings.provider == "mock":
            raise LLMError("mock 提供方不应调用真实模型接口")
        payload: Dict[str, Any] = {
            "model": self.settings.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature if temperature is not None else self.settings.temperature,
            "max_tokens": max_tokens,
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            "%s/chat/completions" % self.base_url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer %s" % self.settings.api_key,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.settings.timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:  # 4xx/5xx
            detail = exc.read().decode("utf-8", "replace")[:800]
            raise LLMError("HTTP %s：%s" % (exc.code, detail))
        except urllib.error.URLError as exc:
            raise LLMError("网络不可达：%s" % exc.reason)
        except OSError as exc:
            raise LLMError("请求失败：%s" % exc)

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            raise LLMError("响应不是 JSON：%s" % raw[:300])

        return self._extract_text(data)

    @staticmethod
    def _extract_text(data: Dict[str, Any]) -> str:
        choices: List[Dict[str, Any]] = data.get("choices") or []
        if not choices:
            raise LLMError("响应缺少 choices：%s" % json.dumps(data, ensure_ascii=False)[:300])
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, list):  # 部分网关返回分段内容
            content = "".join(
                part.get("text", "") for part in content if isinstance(part, dict)
            )
        if not isinstance(content, str) or not content.strip():
            raise LLMError("响应内容为空")
        return content.strip()

"""模型调用：OpenAI 兼容接口，只用标准库。

支持两种 wire_api：
- `chat`：POST /chat/completions（方舟、OpenAI、多数兼容网关）
- `responses`：POST /responses（Model Hub / OpenAI Responses 风格网关）

错误统一收敛为 LLMError，由上层决定重试或回落。
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
        self.wire_api = (settings.wire_api or "chat").lower()

    def complete(
        self,
        system: str,
        user: str,
        temperature: float = 0.0,
        max_tokens: int = 3000,
    ) -> str:
        if self.settings.provider == "mock":
            raise LLMError("mock 提供方不应调用真实模型接口")
        payload = self.build_payload(system, user, temperature, max_tokens)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        url = "%s/%s" % (self.base_url, "responses" if self.wire_api == "responses" else "chat/completions")
        request = urllib.request.Request(
            url,
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

        return self.parse_response(data)

    # ---------- 请求体 ----------

    def build_payload(
        self,
        system: str,
        user: str,
        temperature: float = 0.0,
        max_tokens: int = 3000,
    ) -> Dict[str, Any]:
        """按 wire_api 组装请求体，便于单测直接断言结构。"""
        if self.wire_api == "responses":
            payload: Dict[str, Any] = {
                "model": self.settings.model,
                "input": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "max_output_tokens": max_tokens,
            }
            if temperature is not None:
                payload["temperature"] = temperature
            return payload
        return {
            "model": self.settings.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature if temperature is not None else self.settings.temperature,
            "max_tokens": max_tokens,
        }

    # ---------- 响应解析 ----------

    @classmethod
    def parse_response(cls, data: Dict[str, Any]) -> str:
        if not isinstance(data, dict):
            raise LLMError("响应不是 JSON 对象")
        error = data.get("error")
        if isinstance(error, dict) and error.get("message"):
            raise LLMError("接口返回错误：%s" % error.get("message"))
        if isinstance(data.get("output_text"), str) and data["output_text"].strip():
            return data["output_text"].strip()

        # Responses API：output[] 里的 message.content[] -> output_text
        parts: List[str] = []
        for item in data.get("output") or []:
            if not isinstance(item, dict):
                continue
            for block in item.get("content") or []:
                if isinstance(block, dict) and block.get("type") in ("output_text", "text"):
                    text = block.get("text")
                    if isinstance(text, str) and text.strip():
                        parts.append(text.strip())
        if parts:
            return "\n".join(parts)

        # Chat Completions：choices[0].message.content
        choices = data.get("choices") or []
        if choices:
            message = choices[0].get("message") or {}
            content = message.get("content")
            if isinstance(content, list):  # 部分网关返回分段内容
                content = "".join(
                    part.get("text", "") for part in content if isinstance(part, dict)
                )
            if isinstance(content, str) and content.strip():
                return content.strip()

        raise LLMError("响应缺少可用文本：%s" % json.dumps(data, ensure_ascii=False)[:300])

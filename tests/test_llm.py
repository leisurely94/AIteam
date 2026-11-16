"""模型接入：请求体组装、两种 wire_api 的响应解析、本地 stub 端到端。"""
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from ai_team.config import Settings
from ai_team.llm import ChatClient, LLMError


def hub_settings(base_url="https://example.com/v1", wire_api="responses"):
    return Settings(provider="hub", model="gpt-5.6-terra", base_url=base_url, wire_api=wire_api, api_key="sk-test")


class PayloadTest(unittest.TestCase):
    def test_chat_payload(self):
        payload = ChatClient(hub_settings(wire_api="chat")).build_payload("sys", "usr", 0.2, 512)
        self.assertEqual(payload["model"], "gpt-5.6-terra")
        self.assertEqual(payload["messages"][0], {"role": "system", "content": "sys"})
        self.assertEqual(payload["messages"][1], {"role": "user", "content": "usr"})
        self.assertEqual(payload["max_tokens"], 512)
        self.assertEqual(payload["temperature"], 0.2)

    def test_responses_payload(self):
        payload = ChatClient(hub_settings()).build_payload("sys", "usr", 0.3, 512)
        self.assertEqual(payload["input"][0], {"role": "system", "content": "sys"})
        self.assertEqual(payload["input"][1], {"role": "user", "content": "usr"})
        self.assertEqual(payload["max_output_tokens"], 512)
        self.assertNotIn("messages", payload)


class ParseTest(unittest.TestCase):
    def test_parse_responses_output(self):
        data = {
            "output": [
                {"type": "reasoning", "content": []},
                {"type": "message", "content": [
                    {"type": "output_text", "text": "第一段"},
                    {"type": "output_text", "text": "第二段"},
                ]},
            ]
        }
        self.assertEqual(ChatClient.parse_response(data), "第一段\n第二段")

    def test_parse_output_text_shortcut(self):
        self.assertEqual(ChatClient.parse_response({"output_text": "  直接给出  "}), "直接给出")

    def test_parse_chat_choices(self):
        data = {"choices": [{"message": {"content": [{"type": "text", "text": "分段内容"}]}}]}
        self.assertEqual(ChatClient.parse_response(data), "分段内容")

    def test_parse_error_payload(self):
        with self.assertRaises(LLMError) as ctx:
            ChatClient.parse_response({"error": {"message": "Invalid token"}})
        self.assertIn("Invalid token", str(ctx.exception))

    def test_parse_empty_payload(self):
        with self.assertRaises(LLMError):
            ChatClient.parse_response({"output": []})


class _StubHandler(BaseHTTPRequestHandler):
    seen_path = ""
    seen_body = {}

    def do_POST(self):
        _StubHandler.seen_path = self.path
        length = int(self.headers.get("Content-Length", 0))
        _StubHandler.seen_body = json.loads(self.rfile.read(length).decode("utf-8"))
        if self.headers.get("Authorization") != "Bearer sk-test":
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b'{"error":{"message":"bad token"}}')
            return
        body = json.dumps({
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "stub 回复"}]}]
        }).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        return


class EndToEndTest(unittest.TestCase):
    def setUp(self):
        self.server = HTTPServer(("127.0.0.1", 0), _StubHandler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_responses_roundtrip(self):
        client = ChatClient(hub_settings(base_url="http://127.0.0.1:%d/v1" % self.port))
        text = client.complete("你是测试", "说点什么", max_tokens=64)
        self.assertEqual(text, "stub 回复")
        self.assertEqual(_StubHandler.seen_path, "/v1/responses")
        self.assertIn("input", _StubHandler.seen_body)
        self.assertEqual(_StubHandler.seen_body["model"], "gpt-5.6-terra")

    def test_chat_roundtrip_uses_chat_path(self):
        client = ChatClient(hub_settings(base_url="http://127.0.0.1:%d/v1" % self.port, wire_api="chat"))
        # stub 无论 path 都返回 responses 结构，这里只验证请求打到了 chat/completions
        client.complete("s", "u")
        self.assertEqual(_StubHandler.seen_path, "/v1/chat/completions")


if __name__ == "__main__":
    unittest.main()

"""JSON 抽取的边界情况。"""
import unittest

from ai_team.jsonutil import JSONParseError, extract_json


class ExtractJsonTest(unittest.TestCase):
    def test_plain_object(self):
        self.assertEqual(extract_json('{"a": 1}'), {"a": 1})

    def test_fenced_block(self):
        text = '这是一段说明\n```json\n{"a": [1, 2]}\n```\n以上。'
        self.assertEqual(extract_json(text), {"a": [1, 2]})

    def test_surrounded_by_prose(self):
        text = '好的，结果如下：{"a": "中文", "b": 2} 希望有帮助'
        self.assertEqual(extract_json(text), {"a": "中文", "b": 2})

    def test_array_output(self):
        self.assertEqual(extract_json('前缀 [1, 2, 3] 后缀'), [1, 2, 3])

    def test_truncated_raises(self):
        with self.assertRaises(JSONParseError):
            extract_json('{"a": 1, "b":')

    def test_empty_raises(self):
        with self.assertRaises(JSONParseError):
            extract_json("")

    def test_braces_inside_string(self):
        payload = '{"a": "用 { } 表示结构"}'
        self.assertEqual(extract_json(payload), {"a": "用 { } 表示结构"})


if __name__ == "__main__":
    unittest.main()

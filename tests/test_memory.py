"""记忆库：召回、去重、容量、清空、跨实例持久化。"""
import shutil
import tempfile
import unittest
from pathlib import Path

from ai_team.memory import MAX_NOTES, MemoryStore, note_kind, should_remember


class MemoryStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ai-team-mem-")
        self.store = MemoryStore(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_add_and_recall_prefers_related_note(self):
        self.store.add_note("qa_engineer", "周报按周一到周五统计", "fact", "沈砚秋")
        self.store.add_note("qa_engineer", "用户偏好结论先行", "preference", "沈砚秋")
        hits = self.store.recall("qa_engineer", "周报的统计口径是什么")
        self.assertTrue(hits)
        self.assertIn("周一到周五", hits[0])

    def test_duplicate_note_is_ignored(self):
        self.assertTrue(self.store.add_note("qa_engineer", "同一句话"))
        self.assertFalse(self.store.add_note("qa_engineer", "同一句话"))
        self.assertEqual(len(self.store.notes("qa_engineer")), 1)

    def test_capacity_keeps_newest(self):
        for index in range(MAX_NOTES + 5):
            self.store.add_note("qa_engineer", "第 %d 条" % index)
        notes = self.store.notes("qa_engineer")
        self.assertEqual(len(notes), MAX_NOTES)
        self.assertIn("第 %d 条" % (MAX_NOTES + 4), notes[-1]["text"])

    def test_clear_notes_keeps_history(self):
        self.store.add_note("qa_engineer", "要记住的口径")
        self.store.append_turn("qa_engineer", "user", "你好")
        self.assertEqual(self.store.clear_notes("qa_engineer"), 1)
        self.assertEqual(self.store.notes("qa_engineer"), [])
        self.assertEqual(len(self.store.recent("qa_engineer")), 1)

    def test_persists_across_instances(self):
        self.store.add_note("qa_engineer", "跨运行的事实", name="沈砚秋")
        other = MemoryStore(self.tmp)
        self.assertEqual(other.load("qa_engineer")["name"], "沈砚秋")
        self.assertIn("跨运行的事实", other.recall("qa_engineer", "跨运行的事实"))

    def test_history_window(self):
        for index in range(12):
            self.store.append_turn("qa_engineer", "user", "第 %d 轮" % index)
        recent = self.store.recent("qa_engineer", limit=8)
        self.assertEqual(len(recent), 8)
        self.assertEqual(recent[-1]["text"], "第 11 轮")

    def test_trigger_and_kind(self):
        self.assertTrue(should_remember("记住：周报不含周末"))
        self.assertFalse(should_remember("这次先按这个做"))
        self.assertEqual(note_kind("我偏好先看结论"), "preference")
        self.assertEqual(note_kind("周报按周统计"), "fact")


if __name__ == "__main__":
    unittest.main()

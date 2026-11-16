"""对话引擎：人设差异、多轮、记忆写入与跨会话召回。"""
import shutil
import tempfile
import unittest

from ai_team.conversation import DigitalHumanChat
from ai_team.memory import MemoryStore
from ai_team.models import ProjectBrief
from ai_team.offline_reasoner import OfflineReasoner
from ai_team.personas import create_human


class ConversationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ai-team-chat-")
        self.memory = MemoryStore(self.tmp)
        self.reasoner = OfflineReasoner()
        self.brief = ProjectBrief(goal="为质量团队做周报工具")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _chat(self, role):
        return DigitalHumanChat(
            create_human(role),
            self.reasoner,
            self.reasoner,
            self.memory,
            self.brief,
        )

    def test_reply_keeps_persona_and_records_turns(self):
        chat = self._chat("qa_engineer")
        result = chat.reply("这个功能怎么验收？")
        self.assertIn("测试", result["text"])
        history = self.memory.recent("qa_engineer")
        self.assertEqual([t["role"] for t in history], ["user", "human"])

    def test_roles_answer_differently(self):
        question = "这个周报工具先做什么？"
        qa = self._chat("qa_engineer").reply(question)["text"]
        pm = self._chat("product_manager").reply(question)["text"]
        self.assertNotEqual(qa, pm)
        self.assertIn("验收", pm)

    def test_explicit_remember_writes_note(self):
        chat = self._chat("qa_engineer")
        result = chat.reply("记住：周报按周一到周五统计，不含周末")
        self.assertTrue(result["remembered"])
        self.assertIn("周一到周五", self.memory.notes("qa_engineer")[0]["text"])
        self.assertFalse(
            self.memory.notes("qa_engineer")[0]["text"].startswith("记住")
        )

    def test_one_off_instruction_is_not_remembered(self):
        chat = self._chat("qa_engineer")
        chat.reply("这次先按最小范围做")
        self.assertEqual(self.memory.notes("qa_engineer"), [])

    def test_new_session_recalls_previous_constraint(self):
        self._chat("qa_engineer").reply("记住：周报按周一到周五统计")
        # 新会话：长期记忆保留，短期会话历史不延续
        self.memory.clear_history("qa_engineer")
        fresh = DigitalHumanChat(
            create_human("qa_engineer"), self.reasoner, self.reasoner, MemoryStore(self.tmp), self.brief
        )
        answer = fresh.reply("周报的统计口径是什么")["text"]
        self.assertIn("周一到周五", answer)

    def test_greeting_uses_human_persona(self):
        human = create_human("architect")
        self.assertIn(human.name, human.greeting)
        self.assertIn("架构师", human.greeting)
        self.assertTrue(human.avatar.endswith("architect.jpg"))


if __name__ == "__main__":
    unittest.main()

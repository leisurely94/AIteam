"""v0.3 上岗闸门、双评审、复盘与知识库测试。"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from ai_team.config import Settings
from ai_team.knowledge import KnowledgeBase
from ai_team.models import ExamResult, ProjectBrief
from ai_team.offline_reasoner import OfflineReasoner
from ai_team.onboarding import build_team, normalize_positions
from ai_team.orchestrator import Orchestrator
from ai_team.personas import RECRUITER_ROLE

GOAL = "为质量团队做一个周报自动汇总工具：拉取任务与缺陷数据，生成周报并支持导出"


class PositionTest(unittest.TestCase):
    def setUp(self):
        self.reasoner = OfflineReasoner()
        self.brief = self.reasoner.brief(GOAL)

    def test_positions_always_cover_required_roles(self):
        for limit in (4, 5, 6, 8):
            positions, team = self.reasoner.recruit(self.brief, limit)
            keys = {p.key for p in positions}
            self.assertIn("reviewer", keys, "limit=%d 缺少评审岗" % limit)
            self.assertIn("product_manager", keys, "limit=%d 缺少产品岗" % limit)
            self.assertNotIn(RECRUITER_ROLE, keys, "岗位清单不应包含项目经理")
            self.assertLessEqual(len(team.members), limit, "limit=%d 超出人数上限" % limit)

    def test_team_starts_with_project_manager(self):
        positions, team = self.reasoner.recruit(self.brief, 6)
        first = team.members[0]
        self.assertEqual(first.role, RECRUITER_ROLE)
        self.assertEqual(team.lead, first.id)
        self.assertTrue(first.onboarded, "招募人不需要考试")
        for member in team.members[1:]:
            self.assertFalse(member.onboarded, "%s 上岗前必须通过考核" % member.name)

    def test_normalize_positions_drops_optional_to_fit(self):
        raw = [
            {"key": "product_manager", "headcount": 1},
            {"key": "architect", "headcount": 1},
            {"key": "backend_dev", "headcount": 1},
            {"key": "frontend_dev", "headcount": 1},
            {"key": "qa_engineer", "headcount": 1},
            {"key": "tech_writer", "headcount": 1},
        ]
        positions = normalize_positions(raw, 6)
        self.assertIn("reviewer", {p.key for p in positions})
        self.assertLessEqual(sum(p.headcount for p in positions), 5)


class ExamTest(unittest.TestCase):
    def setUp(self):
        self.reasoner = OfflineReasoner()
        self.brief = self.reasoner.brief(GOAL)
        _, self.team = self.reasoner.recruit(self.brief, 6)
        self.pm = self.team.members[0]
        self.member = self.team.members[1]

    def test_offline_training_and_exam_pass(self):
        plan = self.reasoner.train(self.member, self.brief)
        self.assertGreater(len(plan.material), 300)
        self.assertEqual(len(plan.questions), 3)
        self.assertIn("自检清单", plan.material)

        answers = self.reasoner.answer_exam(self.member, plan, self.brief)
        exam = self.reasoner.grade_exam(self.pm, self.member, plan, answers, self.brief)
        self.assertTrue(exam.passed)
        self.assertGreaterEqual(exam.score, 80)

    def test_grade_punishes_empty_answers(self):
        plan = self.reasoner.train(self.member, self.brief)
        exam = self.reasoner.grade_exam(self.pm, self.member, plan, ["不知道", "不知道", "不知道"], self.brief)
        self.assertFalse(exam.passed)
        self.assertGreaterEqual(len(exam.issues), 1)


class OnboardingGateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ai-team-onboard-")
        self.settings = Settings(provider="mock", data_dir=self.tmp, max_rework=1)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_unqualified_member_is_replaced(self):
        class StrictReasoner(OfflineReasoner):
            def grade_exam(self, pm, human, plan, answers, brief):
                return ExamResult(human_id=human.id, score=60, passed=False,
                                  issues=["采分点未覆盖"], answers=list(answers))

        orch = Orchestrator(self.settings, emit=lambda k, m: None)
        orch.reasoner = StrictReasoner()
        orch.fallback = StrictReasoner()

        ctx = orch.start(GOAL)
        run = orch.execute(ctx)

        pm = next(m for m in run.team.members if m.role == RECRUITER_ROLE)
        staff = [m for m in run.team.members if m.id != pm.id]
        self.assertTrue(all(not m.onboarded for m in staff))
        self.assertTrue(all(m.exam_score == 60 for m in staff))

        # 所有任务都改派给了项目经理，没有人带着未上岗身份干活
        for task in run.tasks:
            self.assertEqual(task.assignee, pm.id, task.title)

        events = [e["kind"] if isinstance(e, dict) else e.kind for e in ctx.store.events(run.id)]
        self.assertIn("onboard_failed", events)
        self.assertIn("reassign", events)
        self.assertTrue((Path(run.dir) / "exam-results.md").exists())
        ctx.store.close()

    def test_pm_review_is_recorded(self):
        orch = Orchestrator(self.settings, emit=lambda k, m: None)
        ctx = orch.start(GOAL)
        run = orch.execute(ctx)
        for task in run.tasks:
            self.assertEqual(task.pm_review.get("verdict"), "pass", task.title)
        ctx.store.close()


class RetrospectiveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ai-team-retro-")
        self.settings = Settings(provider="mock", data_dir=self.tmp, max_rework=1)
        self.orch = Orchestrator(self.settings, emit=lambda k, m: None)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_retrospective_and_knowledge_written(self):
        ctx = self.orch.start(GOAL)
        run = self.orch.execute(ctx)
        ctx.store.close()

        retro_path = Path(run.dir) / "retrospective.md"
        self.assertTrue(retro_path.exists())
        body = retro_path.read_text(encoding="utf-8")
        self.assertIn("## 过程问题", body)
        self.assertIn("## 角色能力提升", body)
        self.assertIn("## 流程优化", body)
        self.assertIn("## 知识库条目", body)

        self.assertIsNotNone(run.retrospective)
        self.assertTrue(run.retrospective.issues)
        self.assertTrue(run.retrospective.knowledge)

        kb = KnowledgeBase(self.tmp)
        self.assertGreater(kb.count(), 0)
        notes = json.loads((Path(self.tmp) / "knowledge" / "kb.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(notes["notes"]), 1)
        self.assertTrue((Path(self.tmp) / "knowledge" / "kb.md").exists())

        # 再次写入同名条目不重复堆积
        before = kb.count()
        kb.add(run.retrospective.knowledge, run.id)
        self.assertEqual(kb.count(), before)


if __name__ == "__main__":
    unittest.main()

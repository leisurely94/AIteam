"""离线全流程：组队、计划、执行、评审返工、汇总、持久化。"""
import shutil
import tempfile
import unittest
from pathlib import Path

from ai_team.config import Settings
from ai_team.export import export_bundle
from ai_team.models import ProjectBrief
from ai_team.offline_reasoner import OfflineReasoner
from ai_team.orchestrator import Orchestrator
from ai_team.store import RunStore
from ai_team.tasks import TaskGraph

GOAL = "为质量团队做一个周报自动汇总工具：拉取任务与缺陷数据，生成周报并支持导出"


class OfflineDesignTest(unittest.TestCase):
    def setUp(self):
        self.reasoner = OfflineReasoner()

    def test_brief_absorbs_answers(self):
        answers = {"有哪些硬性约束（时间、人力、技术栈、合规）？": "两周内上线，只用 Python"}
        brief = self.reasoner.brief(GOAL, answers)
        self.assertIn("两周内上线", "".join(brief.constraints))
        self.assertGreaterEqual(len(brief.deliverables), 3)
        self.assertGreaterEqual(len(brief.acceptance), 3)

    def test_plan_is_valid_graph(self):
        brief = self.reasoner.brief(GOAL)
        team = self.reasoner.roster(brief)
        tasks = self.reasoner.plan(brief, team)
        graph = TaskGraph(tasks)
        self.assertEqual(len(graph.order), len(tasks))
        for task in tasks:
            self.assertIn(task.role, {m.role for m in team.members})

    def test_build_roles_are_parallel(self):
        brief = ProjectBrief(goal="做前后端加数据看板")
        team = self.reasoner.roster(brief, limit=6)
        tasks = self.reasoner.plan(brief, team)
        by_role = {t.role: t for t in tasks}
        if "backend_dev" in by_role and "frontend_dev" in by_role:
            self.assertNotIn("backend_dev", by_role["frontend_dev"].depends_on)
            self.assertNotIn("frontend_dev", by_role["backend_dev"].depends_on)


class OfflineRunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ai-team-test-")
        self.settings = Settings(provider="mock", data_dir=self.tmp, max_rework=1)
        self.events = []
        self.orch = Orchestrator(self.settings, emit=lambda k, m: self.events.append((k, m)))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_full_run_produces_deliverables(self):
        ctx = self.orch.start(GOAL)
        run = self.orch.execute(ctx)

        self.assertEqual(run.status, "finished")
        self.assertTrue(run.tasks)
        for task in run.tasks:
            self.assertEqual(task.status, "done", task.title)
            artifact = Path(run.dir) / task.artifact
            self.assertTrue(artifact.exists(), task.artifact)
            self.assertGreater(len(artifact.read_text(encoding="utf-8")), 400)

        report = (Path(run.dir) / "final_report.md").read_text(encoding="utf-8")
        for keyword in ("项目交付说明", GOAL, "团队构成", "交付物与验收对照"):
            self.assertIn(keyword, report)

        self.assertTrue((Path(run.dir) / "brief.md").exists())
        self.assertTrue((Path(run.dir) / "team.md").exists())
        self.assertTrue((Path(run.dir) / "plan.md").exists())
        ctx.store.close()

    def test_upstream_context_is_passed(self):
        ctx = self.orch.start(GOAL)
        run = self.orch.execute(ctx)
        downstream = next(t for t in run.tasks if t.depends_on)
        body = (Path(run.dir) / downstream.artifact).read_text(encoding="utf-8")
        self.assertIn("上游输入摘要", body)
        ctx.store.close()

    def test_review_loop_reworks_qa(self):
        ctx = self.orch.start(GOAL)
        run = self.orch.execute(ctx)
        qa = next((t for t in run.tasks if t.role == "qa_engineer"), None)
        if qa is not None:
            self.assertGreaterEqual(qa.rounds, 1)
            self.assertLessEqual(qa.rounds, self.settings.max_rework)
            body = (Path(run.dir) / qa.artifact).read_text(encoding="utf-8")
            self.assertIn("按评审意见的修改", body)
        ctx.store.close()

    def test_state_is_persisted(self):
        ctx = self.orch.start(GOAL)
        run = self.orch.execute(ctx)
        ctx.store.close()

        store = RunStore(Path(run.dir) / "state.db")
        self.assertEqual(len(store.tasks(run.id)), len(run.tasks))
        self.assertGreaterEqual(len(store.members(run.id)), 3)
        self.assertGreaterEqual(len(store.messages(run.id)), len(run.tasks))
        self.assertTrue(store.events(run.id))
        store.close()

    def test_export_bundle(self):
        ctx = self.orch.start(GOAL)
        run = self.orch.execute(ctx)
        target = export_bundle(self.tmp, run.id, str(Path(self.tmp) / "dist"))
        body = target.read_text(encoding="utf-8")
        self.assertIn("项目交付说明", body)
        self.assertIn("附录：各角色完整产物", body)
        self.assertIn(run.tasks[0].title, body)
        ctx.store.close()

    def test_failure_blocks_downstream(self):
        """把生成和评审都打断，验证失败传播与运行不中断。"""

        class Broken(OfflineReasoner):
            def act(self, *args, **kwargs):
                raise RuntimeError("模拟生成失败")

        orch = Orchestrator(self.settings, emit=lambda k, m: None)
        orch.reasoner = Broken()
        orch.fallback = Broken()
        ctx = orch.start(GOAL)
        run = orch.execute(ctx)
        self.assertEqual(run.status, "partial")
        self.assertTrue(any(t.status == "failed" for t in run.tasks))
        self.assertTrue(any(t.status == "blocked" for t in run.tasks))
        ctx.store.close()


if __name__ == "__main__":
    unittest.main()

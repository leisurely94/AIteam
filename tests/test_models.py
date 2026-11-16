"""数据模型的序列化与派生行为。"""
import unittest

from ai_team.models import DigitalHuman, ProjectBrief, ReviewResult, Run, Task, Team


class ModelsTest(unittest.TestCase):
    def test_brief_roundtrip(self):
        brief = ProjectBrief.from_dict(
            {"goal": "做周报", "deliverables": ["文档", ""], "constraints": "只要 Python", "unknown": 1}
        )
        self.assertEqual(brief.goal, "做周报")
        self.assertEqual(brief.deliverables, ["文档"])
        self.assertEqual(brief.constraints, ["只要 Python"])
        self.assertEqual(brief.to_dict()["goal"], "做周报")

    def test_team_roundtrip(self):
        human = DigitalHuman(id="backend_dev", name="陈墨白", role="backend_dev", title="后端工程师")
        team = Team(name="测试组", lead="backend_dev", members=[human])
        restored = Team.from_dict(team.to_dict())
        self.assertEqual(restored.name, "测试组")
        self.assertEqual(restored.lead_human.name, "陈墨白")
        with self.assertRaises(KeyError):
            restored.get("missing")

    def test_team_by_role(self):
        team = Team(
            name="t",
            lead="a",
            members=[
                DigitalHuman(id="a", name="甲", role="product_manager", title="产品"),
                DigitalHuman(id="b", name="乙", role="reviewer", title="评审"),
            ],
        )
        self.assertEqual(team.by_role("reviewer").name, "乙")
        self.assertIsNone(team.by_role("architect"))

    def test_review_result_normalizes_verdict(self):
        self.assertTrue(ReviewResult.from_dict({"verdict": "通过"}).passed)
        self.assertFalse(ReviewResult.from_dict({"verdict": "changes"}).passed)

    def test_task_from_dict_tolerates_missing(self):
        task = Task.from_dict({"id": "t1", "title": "任务"})
        self.assertEqual(task.status, "pending")
        self.assertEqual(task.depends_on, [])
        self.assertEqual(task.rounds, 0)

    def test_human_memory_limit(self):
        human = DigitalHuman(id="a", name="甲", role="reviewer", title="评审")
        for i in range(12):
            human.remember("note-%d" % i)
        self.assertEqual(len(human.memory), 8)
        self.assertEqual(human.memory[-1], "note-11")

    def test_run_roundtrip(self):
        run = Run(
            id="r1",
            goal="目标",
            dir="/tmp",
            brief=ProjectBrief(goal="目标"),
            team=Team(name="t", lead="a", members=[]),
            tasks=[Task(id="t1", title="任务", role="backend_dev")],
        )
        restored = Run.from_dict(run.to_dict())
        self.assertEqual(restored.id, "r1")
        self.assertEqual(len(restored.tasks), 1)
        self.assertEqual(restored.status, "created")


if __name__ == "__main__":
    unittest.main()

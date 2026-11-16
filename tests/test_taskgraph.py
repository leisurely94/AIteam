"""任务图：校验、拓扑、就绪集、阻塞传播。"""
import unittest

from ai_team.models import Task
from ai_team.tasks import PlanError, TaskGraph


def make(tid, deps=None, status="pending"):
    return Task(id=tid, title="任务%s" % tid, role="backend_dev", depends_on=deps or [], status=status)


class TaskGraphTest(unittest.TestCase):
    def test_order_and_ready(self):
        graph = TaskGraph([make("t1"), make("t2", ["t1"]), make("t3", ["t1"]), make("t4", ["t2", "t3"])])
        self.assertEqual(graph.order, ["t1", "t2", "t3", "t4"])
        self.assertEqual([t.id for t in graph.ready()], ["t1"])
        graph.get("t1").status = "done"
        self.assertEqual([t.id for t in graph.ready()], ["t2", "t3"])

    def test_cycle_raises(self):
        with self.assertRaises(PlanError):
            TaskGraph([make("t1", ["t2"]), make("t2", ["t1"])])

    def test_missing_dependency_raises(self):
        with self.assertRaises(PlanError):
            TaskGraph([make("t1", ["t9"])])

    def test_duplicate_id_raises(self):
        with self.assertRaises(PlanError):
            TaskGraph([make("t1"), make("t1")])

    def test_self_dependency_raises(self):
        with self.assertRaises(PlanError):
            TaskGraph([make("t1", ["t1"])])

    def test_empty_raises(self):
        with self.assertRaises(PlanError):
            TaskGraph([])

    def test_block_downstream(self):
        graph = TaskGraph([make("t1"), make("t2", ["t1"]), make("t3", ["t2"])])
        graph.get("t1").status = "failed"
        blocked = graph.block_downstream("t1", "上游失败")
        self.assertEqual(blocked, ["t2", "t3"])
        self.assertEqual(graph.get("t2").status, "blocked")
        self.assertEqual(graph.get("t2").review["summary"], "上游失败")
        self.assertFalse(graph.unfinished())

    def test_layers(self):
        graph = TaskGraph([make("t1"), make("t2", ["t1"]), make("t3", ["t1"]), make("t4", ["t2", "t3"])])
        self.assertEqual(graph.layers(), [["t1"], ["t2", "t3"], ["t4"]])

    def test_progress(self):
        graph = TaskGraph([make("t1"), make("t2", ["t1"], status="done"), make("t3", ["t2"])])
        counts = graph.progress()
        self.assertEqual(counts["total"], 3)
        self.assertEqual(counts["done"], 1)
        self.assertEqual(counts["pending"], 2)


if __name__ == "__main__":
    unittest.main()

"""CLI：run / status / export / roles 的返回码与关键输出。"""
import contextlib
import io
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ai_team.cli import main

GOAL = "为质量团队做一个周报自动汇总工具"


def run_cli(argv):
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = main(argv)
    return code, buffer.getvalue()


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ai-team-cli-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_run_then_status_then_export(self):
        code, out = run_cli(["--data-dir", self.tmp, "run", GOAL, "--provider", "mock"])
        self.assertEqual(code, 0, out)
        self.assertIn("团队", out)
        self.assertIn("final_report.md", out)

        code, out = run_cli(["--data-dir", self.tmp, "status"])
        self.assertEqual(code, 0, out)
        self.assertIn(GOAL, out)
        self.assertIn("done", out)

        code, out = run_cli(["--data-dir", self.tmp, "export", "latest", "--out", str(Path(self.tmp) / "dist")])
        self.assertEqual(code, 0, out)
        self.assertIn("已导出", out)

    def test_data_dir_after_subcommand(self):
        code, out = run_cli(["run", GOAL, "--data-dir", self.tmp, "--provider", "mock"])
        self.assertEqual(code, 0, out)
        self.assertTrue((Path(self.tmp) / "runs").exists(), out)

    def test_roles_lists_catalog(self):
        code, out = run_cli(["roles"])
        self.assertEqual(code, 0)
        self.assertIn("product_manager", out)
        self.assertIn("评审专家", out)

    def test_status_without_runs(self):
        code, out = run_cli(["--data-dir", self.tmp, "status"])
        self.assertEqual(code, 2)
        self.assertIn("没有任何运行", out)

    def test_team_shows_avatar_and_greeting(self):
        code, out = run_cli(["--data-dir", self.tmp, "team"])
        self.assertEqual(code, 0, out)
        self.assertIn("assets/avatars/product_manager.jpg", out)
        self.assertIn("沈砚秋", out)

    def test_talk_remembers_and_memory_reports(self):
        with mock.patch("builtins.input", side_effect=["记住：周报按周一到周五统计", "/exit"]):
            code, out = run_cli(["--data-dir", self.tmp, "talk", "沈砚秋"])
        self.assertEqual(code, 0, out)
        self.assertIn("已记住", out)
        self.assertIn("测试工程师", out)

        code, out = run_cli(["--data-dir", self.tmp, "memory", "沈砚秋"])
        self.assertEqual(code, 0, out)
        self.assertIn("周一到周五", out)

        code, out = run_cli(["--data-dir", self.tmp, "memory", "沈砚秋", "--forget"])
        self.assertEqual(code, 0, out)
        self.assertIn("已清空", out)

        code, out = run_cli(["--data-dir", self.tmp, "memory", "沈砚秋"])
        self.assertEqual(code, 0, out)
        self.assertNotIn("周一到周五", out)

    def test_talk_unknown_human(self):
        code, out = run_cli(["--data-dir", self.tmp, "talk", "张三"])
        self.assertEqual(code, 2)
        self.assertIn("没有找到", out)

    def test_run_writes_team_card_with_avatars(self):
        code, out = run_cli(["--data-dir", self.tmp, "run", GOAL, "--provider", "mock"])
        self.assertEqual(code, 0, out)
        runs = list((Path(self.tmp) / "runs").iterdir())
        self.assertEqual(len(runs), 1, out)
        team_md = (runs[0] / "team.md").read_text(encoding="utf-8")
        self.assertIn("assets/avatars/", team_md)
        self.assertTrue((runs[0] / "assets" / "avatars").is_dir())


if __name__ == "__main__":
    unittest.main()

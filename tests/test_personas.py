"""角色模板与数字人生成。"""
import unittest

from ai_team.models import ProjectBrief
from ai_team.offline_reasoner import OfflineReasoner, _pick_roles
from ai_team.personas import ROLES, catalog_text, create_human, sections_for, unique_id


class PersonaTest(unittest.TestCase):
    def test_all_roles_have_sections(self):
        for key in ROLES:
            self.assertGreaterEqual(len(sections_for(key)), 3, key)

    def test_catalog_mentions_every_role(self):
        text = catalog_text()
        for key in ROLES:
            self.assertIn(key, text)

    def test_create_human_unique_id(self):
        human = create_human("backend_dev")
        self.assertEqual(human.id, "backend_dev")
        self.assertTrue(human.name)
        second = unique_id(human.id, [human.id])
        self.assertEqual(second, "backend_dev-2")

    def test_focus_extends_mission(self):
        human = create_human("architect", focus="接口契约")
        self.assertIn("接口契约", human.mission)
        self.assertIn("项目侧重", human.mission)

    def test_role_picking(self):
        roles = _pick_roles("为质量团队做一个周报自动汇总工具，包含接口和数据看板")
        self.assertIn("product_manager", roles)
        self.assertIn("reviewer", roles)
        self.assertIn("backend_dev", roles)
        self.assertIn("data_analyst", roles)

    def test_roster_always_has_required(self):
        reasoner = OfflineReasoner()
        brief = ProjectBrief(goal="写一份上线文档")
        team = reasoner.roster(brief)
        roles = {m.role for m in team.members}
        self.assertIn("product_manager", roles)
        self.assertIn("reviewer", roles)
        self.assertIn(team.lead, team.ids())

    def test_roster_within_limit(self):
        reasoner = OfflineReasoner()
        brief = ProjectBrief(goal="做一套前后端加数据看板和测试文档的完整系统")
        team = reasoner.roster(brief, limit=4)
        self.assertLessEqual(len(team.members), 4)


if __name__ == "__main__":
    unittest.main()

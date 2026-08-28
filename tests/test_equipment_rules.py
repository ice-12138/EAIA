import unittest

from equipment_rules import GameRules


class GameRulesAttackSpeedTests(unittest.TestCase):
    def test_white_panel_baseline_keeps_base_interval(self):
        rules = GameRules()
        self.assertAlmostEqual(
            rules.attack_interval(2.6, 100.0, base_attack_speed=100.0),
            2.6,
            places=9,
        )

    def test_only_delta_from_white_panel_changes_interval(self):
        rules = GameRules()
        self.assertAlmostEqual(
            rules.attack_interval(2.6, 140.0, base_attack_speed=100.0),
            2.6 / 1.4,
            places=9,
        )

    def test_legacy_bonus_point_call_remains_compatible(self):
        rules = GameRules()
        self.assertAlmostEqual(
            rules.attack_interval(2.6, 40.0),
            2.6 / 1.4,
            places=9,
        )


if __name__ == "__main__":
    unittest.main()

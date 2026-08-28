import unittest

from hero_core_engine import HeroCoreError, HeroCoreSimulator, SafeExpression, load_core, simulate_average


class HeroCoreEngineTests(unittest.TestCase):
    def test_safe_expression_rejects_function_calls(self):
        with self.assertRaises(HeroCoreError):
            SafeExpression.compile("__import__('os').system('echo unsafe')")

    def test_long_cooldown_is_normalized_in_equivalent_60s(self):
        core = {
            "schema_version": "1.0",
            "core_version": "test",
            "hero": {
                "id": "CD_TEST",
                "name": "CD测试",
                "base_stats": {
                    "atk": 100.0,
                    "crit_rate": 0.0,
                    "crit_dmg": 1.5,
                    "atk_speed": 0.0,
                    "attack_interval": 9999.0,
                },
            },
            "resources": {},
            "state": {},
            "buffs": {},
            "summons": {},
            "skills": {
                "basic": {
                    "id": "basic",
                    "name": "普攻",
                    "kind": "basic",
                    "coefficient": 0.0,
                    "hit_count": 1,
                    "can_crit": False,
                    "initial_cooldown": 0.0,
                    "tags": ["basic_attack"],
                },
                "slow_skill": {
                    "id": "slow_skill",
                    "name": "120秒技能",
                    "kind": "skill",
                    "coefficient": 10.0,
                    "hit_count": 1,
                    "can_crit": False,
                    "cooldown": 120.0,
                    "initial_cooldown": 120.0,
                    "duration": 0.0,
                    "tags": ["skill"],
                },
            },
            "triggers": [],
            "policies": {},
            "default_policy": "",
        }
        result = simulate_average(core, trials=1, warmup=120.0, measurement=1200.0, seed=1)
        self.assertAlmostEqual(result["equivalent_60s"]["mean"], 500.0, places=6)
        self.assertEqual(result["actual_60s"]["mean"], 0.0)

    def test_white_panel_attack_speed_does_not_accelerate_naked_wukong(self):
        core = load_core("SUN_WUKONG")
        self.assertEqual(core["hero"]["base_stats"]["atk_speed"], 100.0)
        self.assertEqual(core["hero"]["base_stats"]["crit_rate"], 0.0)
        simulator = HeroCoreSimulator(core, warmup=0.0, measurement=5.0)
        self.assertEqual(simulator.panel["atk_speed_base"], 100.0)
        self.assertEqual(simulator.panel["atk_speed"], 100.0)
        self.assertAlmostEqual(simulator._attack_interval(), 2.6, places=9)

    def test_sun_wukong_core_runs_without_hero_specific_engine_code(self):
        core = load_core("SUN_WUKONG")
        result = simulate_average(
            core,
            trials=4,
            warmup=60.0,
            measurement=120.0,
            target={"defense": 0.0, "control_immune": True, "enemy_count": 1},
            policy="immediate",
            seed=20260828,
        )
        self.assertGreater(result["equivalent_60s"]["mean"], 0.0)
        self.assertGreater(result["source_damage_equivalent_60s"].get("basic", 0.0), 0.0)
        self.assertGreater(result["source_damage_equivalent_60s"].get("summon:great_sage_clone", 0.0), 0.0)
        self.assertGreater(result["event_rate_per_60s"].get("ultimate_cast", 0.0), 0.0)


if __name__ == "__main__":
    unittest.main()

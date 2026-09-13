import unittest

from hero_core_engine import (
    HeroCoreError,
    HeroCoreSimulator,
    load_core,
    simulate_average,
    validate_core,
)


class _AlwaysProc:
    def random(self):
        return 0.0


class NasijiaEzioHeroCoreTests(unittest.TestCase):
    def test_validator_accepts_triggered_and_rejects_unknown_kind(self):
        core = load_core("NASIJIA")
        self.assertEqual(core["skills"]["death_prophecy"]["kind"], "triggered")

        invalid = {
            **core,
            "skills": {
                **core["skills"],
                "bad": {
                    "id": "bad",
                    "name": "bad",
                    "kind": "passive",
                    "coefficient": 0.0,
                    "hit_count": 1,
                    "can_crit": False,
                },
            },
        }
        with self.assertRaises(HeroCoreError):
            validate_core(invalid)

    def test_nasijia_executes_poison_dot_and_death_prophecy(self):
        core = load_core("NASIJIA")
        result = simulate_average(
            core,
            trials=2,
            warmup=0.0,
            measurement=30.0,
            target={"defense": 0.0, "mres": 0.0, "enemy_count": 1},
            policy="default",
            seed=20260913,
        )
        self.assertGreater(result["source_damage_equivalent_60s"].get("poison_dot", 0.0), 0.0)
        self.assertGreater(result["source_damage_equivalent_60s"].get("death_prophecy", 0.0), 0.0)
        self.assertGreater(result["event_rate_per_60s"].get("triggered_skill_cast:death_prophecy", 0.0), 0.0)
        self.assertGreater(result["event_rate_per_60s"].get("ultimate_cast", 0.0), 0.0)

    def test_nasijia_stacked_attack_buff_and_mres_reduction_are_generic(self):
        core = load_core("NASIJIA")
        sim = HeroCoreSimulator(
            core,
            target={"defense": 0.0, "mres": 7000.0, "enemy_count": 1},
            warmup=0.0,
            measurement=10.0,
        )
        for _ in range(3):
            sim._apply_action({"type": "apply_buff", "buff": "doomsday_shadow_atk"}, {})
        modifiers = sim._active_combat_modifiers()
        self.assertAlmostEqual(modifiers["atk_pct"], 0.12, places=9)

        factor = sim._mitigation_factor(
            tags={"magic"},
            modifiers={"target_mres_reduction": 0.30},
        )
        expected = sim.rules.defense_multiplier(7000.0 * 0.70)
        self.assertAlmostEqual(factor, expected, places=12)

    def test_ezio_ambush_is_triggered_and_ignores_defense(self):
        core = load_core("EZIO_AUDITORE")
        self.assertEqual(core["skills"]["ambush"]["kind"], "triggered")
        self.assertEqual(core["skills"]["surprise_attack"]["kind"], "ultimate")

        naked = HeroCoreSimulator(
            core,
            target={"defense": 0.0, "mres": 0.0, "enemy_count": 1},
            warmup=0.0,
            measurement=2.0,
        )
        armored = HeroCoreSimulator(
            core,
            target={"defense": 10000.0, "mres": 0.0, "enemy_count": 1},
            warmup=0.0,
            measurement=2.0,
        )
        naked._cast_skill("ambush")
        armored._cast_skill("ambush")
        self.assertGreater(naked.damage_total, 0.0)
        self.assertAlmostEqual(armored.damage_total, naked.damage_total, places=9)

    def test_ezio_replacement_cancels_basic_and_dawn_adds_one_extra_ambush(self):
        core = load_core("EZIO_AUDITORE")
        sim = HeroCoreSimulator(
            core,
            target={"defense": 0.0, "mres": 0.0, "enemy_count": 1},
            warmup=0.0,
            measurement=20.0,
        )
        sim.rng = _AlwaysProc()
        sim._apply_action({"type": "apply_buff", "buff": "surprise_attack_mode"}, {})
        initial_rage = sim.resources["rage"]
        sim._process_basic()

        self.assertEqual(sim.source_damage.get("basic", 0.0), 0.0)
        self.assertGreater(sim.source_damage.get("skill", 0.0), 0.0)
        self.assertEqual(sim.event_counts["triggered_skill_cast:ambush"], 2)
        self.assertAlmostEqual(sim.resources["rage"], initial_rage + 10.0, places=9)


if __name__ == "__main__":
    unittest.main()

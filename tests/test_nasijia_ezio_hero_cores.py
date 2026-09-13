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

    def test_white_attack_intervals_use_naked_hero_baseline(self):
        nasijia = load_core("NASIJIA")
        ezio = load_core("EZIO_AUDITORE")
        self.assertEqual(nasijia["hero"]["base_stats"]["attack_interval"], 2.6)
        self.assertEqual(ezio["hero"]["base_stats"]["attack_interval"], 2.6)

        nas_sim = HeroCoreSimulator(nasijia, warmup=0.0, measurement=5.0)
        ezio_sim = HeroCoreSimulator(ezio, warmup=0.0, measurement=5.0)
        self.assertAlmostEqual(nas_sim._attack_interval(), 2.6, places=9)
        self.assertAlmostEqual(ezio_sim._attack_interval(), 2.6, places=9)

    def test_nasijia_lord_self_buff_and_cost_are_active(self):
        core = load_core("NASIJIA")
        self.assertAlmostEqual(
            core["skills"]["despair_sting"]["resource"]["cost"], 337.5, places=9
        )
        sim = HeroCoreSimulator(
            core,
            target={"defense": 0.0, "mres": 0.0, "enemy_count": 1},
            warmup=0.0,
            measurement=5.0,
        )
        sim._run_triggers("BATTLE_START", {})
        modifiers = sim._active_combat_modifiers()
        self.assertAlmostEqual(modifiers["base_atk_pct"], 0.15, places=9)
        expected = core["hero"]["base_stats"]["atk"] * 1.15
        self.assertAlmostEqual(sim._current_attack(modifiers), expected, places=9)

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

    def test_nasijia_acute_poisoning_uses_40_percent_poison_multiplier(self):
        core = load_core("NASIJIA")
        deepened = next(
            trigger
            for trigger in core["triggers"]
            if trigger["id"] == "poison_tick_deepened_10"
        )
        normal = next(
            trigger
            for trigger in core["triggers"]
            if trigger["id"] == "poison_tick_normal_10"
        )
        deepened_coeff = deepened["actions"][0]["coefficient"]
        normal_coeff = normal["actions"][0]["coefficient"]
        self.assertAlmostEqual(normal_coeff, 0.50, places=9)
        self.assertAlmostEqual(deepened_coeff, normal_coeff * 1.40, places=9)

    def test_ezio_ambush_is_triggered_and_ignores_defense(self):
        core = load_core("EZIO_AUDITORE")
        self.assertEqual(core["skills"]["ambush"]["kind"], "triggered")
        self.assertEqual(core["skills"]["surprise_attack"]["kind"], "ultimate")
        self.assertNotIn("assassination", core["skills"]["surprise_attack"]["tags"])

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
        # Provisional rule pending video validation.
        self.assertAlmostEqual(sim.resources["rage"], initial_rage + 10.0, places=9)

    def test_ezio_instinct_uses_two_percent_max_rage_and_counter_is_assassinate(self):
        core = load_core("EZIO_AUDITORE")
        tick = next(
            trigger
            for trigger in core["triggers"]
            if trigger["id"] == "assassins_instinct_rage_tick"
        )
        gain = tick["actions"][0]
        self.assertAlmostEqual(gain["max_fraction"], 0.02, places=9)

        sim = HeroCoreSimulator(
            core,
            target={"defense": 10000.0, "mres": 0.0, "enemy_count": 1},
            warmup=0.0,
            measurement=5.0,
        )
        sim._apply_action({"type": "apply_buff", "buff": "assassins_instinct"}, {})
        before = sim.damage_total
        sim._run_triggers("HERO_DAMAGED_IN_ULT_RANGE", {})
        self.assertNotIn("assassins_instinct", sim.buffs)
        self.assertGreater(sim.damage_total, before)
        self.assertEqual(sim.event_counts["triggered_skill_cast:ambush"], 1)


if __name__ == "__main__":
    unittest.main()

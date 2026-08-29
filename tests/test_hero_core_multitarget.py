import unittest

from hero_core_engine import HeroCoreSimulator, load_core


def simple_basic_core(*, target_cap=1, tags=None, secondary_target_ratio=1.0):
    skill = {
        "id": "basic",
        "name": "basic",
        "kind": "basic",
        "coefficient": 1.0,
        "hit_count": 1,
        "can_crit": False,
        "initial_cooldown": 0.0,
        "tags": list(tags or ["basic_attack"]),
    }
    if target_cap != "missing":
        skill["target_cap"] = target_cap
    if secondary_target_ratio is not None:
        skill["secondary_target_ratio"] = secondary_target_ratio
    return {
        "schema_version": "1.0",
        "core_version": "test",
        "hero": {
            "id": "TARGET_TEST",
            "name": "Target Test",
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
        "skills": {"basic": skill},
        "triggers": [],
        "policies": {},
        "default_policy": "",
    }


def run_once(core, enemy_count):
    return HeroCoreSimulator(
        core,
        target={"defense": 0.0, "enemy_count": enemy_count},
        warmup=0.0,
        measurement=1.0,
        seed=1,
    ).run()


class HeroCoreMultiTargetTests(unittest.TestCase):
    def test_finite_target_cap_uses_minimum_of_enemy_count_and_cap(self):
        core = simple_basic_core(target_cap=3, tags=["basic_attack", "aoe"])
        self.assertAlmostEqual(run_once(core, 1).total_damage, 100.0)
        self.assertAlmostEqual(run_once(core, 2).total_damage, 200.0)
        self.assertAlmostEqual(run_once(core, 8).total_damage, 300.0)

    def test_null_target_cap_is_unlimited_only_for_aoe(self):
        aoe = simple_basic_core(target_cap=None, tags=["basic_attack", "aoe"])
        legacy_single = simple_basic_core(target_cap="missing", tags=["basic_attack"])
        self.assertAlmostEqual(run_once(aoe, 4).total_damage, 400.0)
        self.assertAlmostEqual(run_once(legacy_single, 4).total_damage, 100.0)

    def test_secondary_target_ratio_scales_only_secondary_targets(self):
        core = simple_basic_core(
            target_cap=3,
            tags=["basic_attack", "aoe"],
            secondary_target_ratio=0.5,
        )
        self.assertAlmostEqual(run_once(core, 3).total_damage, 200.0)

    def test_triggered_aoe_damage_uses_enemy_count(self):
        core = simple_basic_core(target_cap=1, tags=["basic_attack"])
        core["skills"]["basic"]["coefficient"] = 0.0
        core["triggers"] = [{
            "id": "aoe_followup",
            "event": "BASIC_ATTACK_BEFORE_DAMAGE",
            "actions": [{
                "type": "deal_damage",
                "coefficient": 1.0,
                "source": "aoe_followup",
                "can_crit": False,
                "target_cap": None,
                "tags": ["skill", "aoe"],
            }],
        }]
        result = run_once(core, 4)
        self.assertAlmostEqual(result.source_damage["aoe_followup"], 400.0)
        self.assertAlmostEqual(result.total_damage, 400.0)

    def test_real_hero_target_caps_change_group_damage(self):
        khamet = load_core("KHAMET")
        khamet_one = run_once(khamet, 1)
        khamet_three = run_once(khamet, 3)
        self.assertAlmostEqual(
            khamet_three.source_damage["basic"],
            khamet_one.source_damage["basic"] * 2.0,
            places=6,
        )

        valkyra = load_core("VALKYRA")
        valkyra_one = run_once(valkyra, 1)
        valkyra_three = run_once(valkyra, 3)
        self.assertAlmostEqual(
            valkyra_three.source_damage["basic"],
            valkyra_one.source_damage["basic"] * 3.0,
            places=6,
        )


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest
from pathlib import Path

from equipment_models import EffectType, EquipmentItem, EquipmentStat, SetEffect, Slot, StatType
from equipment_recommendation_profile import effect_potential, item_potential, resolve_recommendation_profile
from hero_core_engine import HeroCoreSimulator
from hero_core_service import _select_group_build_candidates, _select_set_aware_candidates, _set_effect_scores
from optimizer_projection import OptimizerEquipmentDatabase
from equipment_set_variants import load_set_evolutions, load_set_names


SLOTS = ("weapon", "armor", "bracelet", "necklace", "ring")


def minimal_core(*, crit_rate=0.0, basic_hits=1, interval=1.0, with_ultimate=False):
    skills = {
        "basic": {
            "id": "basic",
            "name": "basic",
            "kind": "basic",
            "coefficient": 1.0,
            "hit_count": basic_hits,
            "can_crit": True,
            "initial_cooldown": 0.0,
            "target_cap": 1,
            "tags": ["basic_attack", "physical"],
        }
    }
    resources = {}
    policies = {}
    default_policy = ""
    if with_ultimate:
        resources = {"rage": {"initial": 100, "max": 100, "auto_per_second": 0}}
        skills["ult"] = {
            "id": "ult",
            "name": "ult",
            "kind": "ultimate",
            "coefficient": 0.0,
            "hit_count": 1,
            "duration": 0.0,
            "can_crit": False,
            "resource": {"name": "rage", "cost": 100},
            "tags": ["ultimate", "physical"],
        }
        policies = {"immediate": {"ultimate_when": "resource.rage >= 100"}}
        default_policy = "immediate"
    return {
        "schema_version": "1.0",
        "core_version": "runtime-fix-test",
        "hero": {
            "id": "RUNTIME_FIX",
            "name": "Runtime Fix",
            "damage_type": "physical",
            "base_stats": {
                "atk": 100.0,
                "hp": 1000.0,
                "crit_rate": crit_rate,
                "crit_dmg": 1.5,
                "atk_speed": 0.0,
                "attack_interval": interval,
            },
        },
        "resources": resources,
        "state": {},
        "buffs": {},
        "summons": {},
        "skills": skills,
        "triggers": [],
        "policies": policies,
        "default_policy": default_policy,
    }


def insert_full_set(database, set_id):
    for index, slot in enumerate(SLOTS, 1):
        item_id = f"{set_id}_{slot}"
        database.connection.execute(
            """INSERT INTO equipment(
                 item_id,slot,set_id,locked,available,slot_id,quality_id,
                 enhancement_level,item_locked
               ) VALUES (?,?,?,?,?,?,?,?,?)""",
            (item_id, slot, set_id, 0, 1, slot, None, 16, 0),
        )
        database.connection.execute(
            """INSERT INTO equipment_stats(
                 item_id,stat_index,stat_source,stat_type,stat_value,
                 unlock_level,is_unlocked,roll_grade_id,estimate_override
               ) VALUES (?,?,?,?,?,?,?,?,?)""",
            (item_id, 0, "main", "ATK_FLAT", 0.0, 0, 1, None, None),
        )
    database.connection.commit()
    return [f"{set_id}_{slot}" for slot in SLOTS]


def runtime_effect(effect_type, value, *, trigger="always", duration=0.0, max_stacks=1, applies_to="all"):
    return SetEffect(
        set_id="TEST",
        effect_id=f"TEST_{effect_type}_{trigger}",
        effect_type=effect_type,
        value=value,
        applies_to=applies_to,
        trigger=trigger,
        duration=duration,
        max_stacks=max_stacks,
        stack_rule="add",
        proc_chance=1.0,
        internal_cd=0.0,
        condition=None,
        approximate=False,
        requires_dot=False,
        enabled_in_v1_1=True,
    )


class HeroCoreRuntimeFixTests(unittest.TestCase):
    def test_hells_lament_on_ultimate_buff_is_applied(self):
        with tempfile.TemporaryDirectory() as directory:
            db = OptimizerEquipmentDatabase(Path(directory) / "equipment.db")
            try:
                db.initialize()
                items = insert_full_set(db, "set_hells_lament")
                result = HeroCoreSimulator(
                    minimal_core(with_ultimate=True),
                    database=db,
                    item_ids=items,
                    warmup=0.0,
                    measurement=3.0,
                    seed=1,
                ).run()
                # t=0 basic is before the t=0 policy-check/ultimate; t=1 and
                # t=2 basics receive +35% damage from Hell's Lament.
                self.assertAlmostEqual(result.source_damage["basic"], 370.0, places=6)
                self.assertEqual(result.coverage, "full")
            finally:
                db.close()

    def test_insight_fixed_extra_damage_is_not_dropped(self):
        with tempfile.TemporaryDirectory() as directory:
            db = OptimizerEquipmentDatabase(Path(directory) / "equipment.db")
            try:
                db.initialize()
                items = insert_full_set(db, "set_insight")
                result = HeroCoreSimulator(
                    minimal_core(interval=9999.0),
                    database=db,
                    item_ids=items,
                    warmup=0.0,
                    measurement=1.0,
                    seed=1,
                ).run()
                # Insight contributes +15% crit rate as well as the fixed 600
                # single-target basic extra damage. The 100 ATK basic therefore
                # has 107.5 expected direct damage plus 600 fixed damage.
                self.assertAlmostEqual(result.total_damage, 707.5, places=6)
                self.assertEqual(result.coverage, "full")
            finally:
                db.close()

    def test_penetration_reduces_matching_resistance(self):
        with tempfile.TemporaryDirectory() as directory:
            db = OptimizerEquipmentDatabase(Path(directory) / "equipment.db")
            try:
                db.initialize()
                items = insert_full_set(db, "set_fatality")
                result = HeroCoreSimulator(
                    minimal_core(interval=9999.0),
                    database=db,
                    item_ids=items,
                    target={"defense": 100.0, "mres": 100.0},
                    warmup=0.0,
                    measurement=1.0,
                    seed=1,
                ).run()
                self.assertGreater(result.total_damage, 50.0)
                self.assertGreater(result.panel["penetration"], 0.0)
            finally:
                db.close()

    def test_single_and_aoe_set_bonus_use_damage_instance_not_enemy_count(self):
        single = HeroCoreSimulator(
            minimal_core(interval=9999.0),
            target={"enemy_count": 3},
            warmup=0.0,
            measurement=1.0,
        )
        single.set_effects = [runtime_effect(EffectType.SINGLE_DMG, 1.0)]
        single_damage = single._deal_damage(
            coefficient=1.0,
            tags=["basic_attack", "physical"],
            can_crit=False,
            source="basic",
            target_cap=1,
        )
        self.assertAlmostEqual(single_damage, 200.0)

        aoe = HeroCoreSimulator(
            minimal_core(interval=9999.0),
            target={"enemy_count": 3},
            warmup=0.0,
            measurement=1.0,
        )
        aoe.set_effects = [runtime_effect(EffectType.AOE_DMG, 1.0)]
        aoe_damage = aoe._deal_damage(
            coefficient=1.0,
            tags=["basic_attack", "physical"],
            can_crit=False,
            source="basic",
            target_cap=1,
        )
        self.assertAlmostEqual(aoe_damage, 100.0)

    def test_multi_hit_basic_triggers_crit_set_between_hits(self):
        simulator = HeroCoreSimulator(
            minimal_core(crit_rate=1.0, basic_hits=2, interval=9999.0),
            warmup=0.0,
            measurement=1.0,
            seed=1,
        )
        simulator.set_effects = [
            runtime_effect(
                EffectType.DAMAGE_PCT,
                0.10,
                trigger="on_basic_crit",
                duration=8.0,
                max_stacks=5,
            )
        ]
        result = simulator.run()
        # Crit expectation is 1.5x. First hit is 150; its guaranteed crit then
        # creates one +10% stack, so the second hit is 165.
        self.assertAlmostEqual(result.total_damage, 315.0, places=6)

    def test_true_magic_and_physical_use_distinct_mitigation(self):
        simulator = HeroCoreSimulator(
            minimal_core(interval=9999.0),
            target={"defense": 100.0, "mres": 300.0},
            warmup=0.0,
            measurement=1.0,
        )
        physical = simulator._deal_damage(
            coefficient=1.0, tags=["physical"], can_crit=False, source="skill", target_cap=1
        )
        magic = simulator._deal_damage(
            coefficient=1.0, tags=["magic"], can_crit=False, source="skill", target_cap=1
        )
        true_damage = simulator._deal_damage(
            coefficient=1.0, tags=["true_damage"], can_crit=False, source="skill", target_cap=1
        )
        self.assertAlmostEqual(physical, 50.0)
        self.assertAlmostEqual(magic, 25.0)
        self.assertAlmostEqual(true_damage, 100.0)

    def test_resource_less_ultimate_without_cooldown_is_one_shot(self):
        core = minimal_core(interval=9999.0)
        core["skills"]["ult"] = {
            "id": "ult",
            "name": "ult",
            "kind": "ultimate",
            "coefficient": 0.0,
            "hit_count": 1,
            "duration": 0.0,
            "can_crit": False,
            "tags": ["ultimate"],
        }
        core["policies"] = {"immediate": {"ultimate_when": "true"}}
        core["default_policy"] = "immediate"
        result = HeroCoreSimulator(core, warmup=0.0, measurement=1.0).run()
        self.assertEqual(result.event_counts.get("ultimate_cast"), 1)

    def test_zero_cooldown_auto_skill_recasts_after_action_time(self):
        core = minimal_core(interval=9999.0)
        core["skills"]["spam"] = {
            "id": "spam",
            "name": "spam",
            "kind": "skill",
            "coefficient": 1.0,
            "hit_count": 1,
            "duration": 0.2,
            "cooldown": 0.0,
            "can_crit": False,
            "tags": ["skill", "physical"],
        }
        result = HeroCoreSimulator(core, warmup=0.0, measurement=0.7).run()
        self.assertGreaterEqual(result.event_counts.get("SKILL_HIT", 0), 3)

    def test_output_candidate_scoring_has_sane_units(self):
        profile = resolve_recommendation_profile({"recommendation_profile": {"category": "output"}})
        flat = EquipmentItem(
            item_id="F",
            slot=Slot.WEAPON,
            set_id="x",
            stats=(EquipmentStat("F", "sub", StatType.ATK_FLAT, 900.0, 1),),
        )
        speed = EquipmentItem(
            item_id="S",
            slot=Slot.WEAPON,
            set_id="x",
            stats=(EquipmentStat("S", "sub", StatType.ATK_SPEED, 100.0, 1),),
        )
        self.assertAlmostEqual(item_potential(flat, profile), 1.0, places=6)
        self.assertGreater(item_potential(speed, profile), 1.0)
        extra = runtime_effect(EffectType.EXTRA_DAMAGE, 600.0, trigger="on_basic_attack_damage")
        self.assertLess(effect_potential(extra, profile), 10.0)

    def test_t3_and_completed_set_builds_are_hard_protected(self):
        with tempfile.TemporaryDirectory() as directory:
            db = OptimizerEquipmentDatabase(Path(directory) / "equipment.db")
            try:
                db.initialize()

                def item(item_id, slot, set_id, atk_pct):
                    return EquipmentItem(
                        item_id=item_id,
                        slot=Slot(slot),
                        set_id=set_id,
                        stats=(EquipmentStat(item_id, "sub", StatType.ATK_PCT, atk_pct, 1),),
                    )

                by_slot = {
                    "weapon": [item("W", "weapon", "set_calamity", 0.1)],
                    "armor": [item("A", "armor", "set_calamity", 0.1)],
                    "bracelet": [
                        item("B_RAW", "bracelet", "set_infernal_roar", 1.0),
                        item("B_T3", "bracelet", "set_hells_lament", 0.0),
                    ],
                    "necklace": [
                        item("N_RAW", "necklace", "set_infernal_roar", 1.0),
                        item("N_T3", "necklace", "set_hells_lament", 0.0),
                    ],
                    "ring": [
                        item("R_RAW", "ring", "set_infernal_roar", 1.0),
                        item("R_T3", "ring", "set_hells_lament", 0.0),
                    ],
                }
                candidates, _ = _select_set_aware_candidates(db, by_slot, 1)
                self.assertIn("B_T3", {row.item_id for row in candidates["bracelet"]})
                self.assertIn("N_T3", {row.item_id for row in candidates["necklace"]})
                self.assertIn("R_T3", {row.item_id for row in candidates["ring"]})

                definitions = db.load_sets()
                effect_scores = _set_effect_scores(db)
                builds, raw = _select_group_build_candidates(
                    candidates,
                    ("bracelet", "necklace", "ring"),
                    1,
                    definitions=definitions,
                    effect_scores=effect_scores,
                    evolutions=load_set_evolutions(db),
                    set_names=load_set_names(db),
                )
                self.assertGreater(raw, 1)
                self.assertTrue(any({row.item_id for row in build} == {"B_T3", "N_T3", "R_T3"} for build in builds))
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()

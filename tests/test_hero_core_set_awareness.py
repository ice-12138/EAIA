import tempfile
import unittest
from pathlib import Path

from equipment_db import EquipmentDatabase
from equipment_models import EffectType, EquipmentItem, EquipmentStat, Slot, StatType
from hero_core_engine import HeroCoreSimulator
from hero_core_service import _select_set_aware_candidates, recommend_hero_core
from optimizer_projection import OptimizerEquipmentDatabase


class HeroCoreSetAwarenessTests(unittest.TestCase):
    @staticmethod
    def _minimal_core():
        return {
            "schema_version": "1.0",
            "core_version": "set-test",
            "hero": {
                "id": "SET_TEST",
                "name": "套装测试",
                "base_stats": {
                    "atk": 100.0,
                    "crit_rate": 0.0,
                    "crit_dmg": 1.5,
                    "atk_speed": 0.0,
                    "attack_interval": 1.0,
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
                    "coefficient": 1.0,
                    "hit_count": 1,
                    "can_crit": False,
                    "tags": ["basic_attack"],
                }
            },
            "triggers": [],
            "policies": {},
            "default_policy": "",
        }

    @staticmethod
    def _insert_item(database, item_id, slot, set_id, *, main_type="ATK_FLAT", main_value=0.0):
        database.connection.execute(
            """INSERT INTO equipment(
                 item_id,slot,set_id,locked,available,slot_id,quality_id,
                 enhancement_level,item_locked
               ) VALUES (?,?,?,?,?,?,?,?,?)""",
            (item_id, slot, set_id, 0, 1, slot, None, 16, 0),
        )
        database.connection.executemany(
            """INSERT INTO equipment_stats(
                 item_id,stat_index,stat_source,stat_type,stat_value,
                 unlock_level,is_unlocked,roll_grade_id,estimate_override
               ) VALUES (?,?,?,?,?,?,?,?,?)""",
            [
                (item_id, 0, "main", main_type, main_value, 0, 1, None, None),
                (item_id, 1, "sub", "ATK_PCT", 0.0, 0, 1, None, None),
                (item_id, 2, "sub", "CRIT_RATE", 0.0, 0, 1, None, None),
            ],
        )

    def test_projection_database_normalizes_v22_set_effects_for_herocore(self):
        with tempfile.TemporaryDirectory() as directory:
            database = OptimizerEquipmentDatabase(Path(directory) / "equipment.db")
            try:
                database.initialize()
                effects = {effect.effect_id: effect for effect in database.load_set_effects()}
                calamity = effects["eff_calamity_atk"]
                self.assertEqual(calamity.effect_type, EffectType.ATK_PCT)
                self.assertEqual(calamity.trigger, "always")
                self.assertAlmostEqual(calamity.value, 0.25)
            finally:
                database.close()

    def test_herocore_panel_applies_active_v22_two_piece_set(self):
        with tempfile.TemporaryDirectory() as directory:
            database = OptimizerEquipmentDatabase(Path(directory) / "equipment.db")
            try:
                database.initialize()
                self._insert_item(database, "W", "weapon", "set_calamity")
                self._insert_item(database, "A", "armor", "set_calamity")
                database.connection.commit()

                simulator = HeroCoreSimulator(
                    self._minimal_core(),
                    database=database,
                    item_ids=["W", "A"],
                    warmup=0.0,
                    measurement=2.0,
                )
                self.assertIn("set_calamity", simulator.active_sets)
                self.assertAlmostEqual(simulator.panel["atk"], 125.0)
            finally:
                database.close()

    def test_candidate_pruning_accounts_for_feasible_set_value(self):
        with tempfile.TemporaryDirectory() as directory:
            database = OptimizerEquipmentDatabase(Path(directory) / "equipment.db")
            try:
                database.initialize()

                def item(item_id, slot, set_id, atk_pct):
                    return EquipmentItem(
                        item_id=item_id,
                        slot=Slot(slot),
                        set_id=set_id,
                        stats=(EquipmentStat(item_id, "sub", StatType.ATK_PCT, atk_pct, 1),),
                    )

                by_slot = {
                    "weapon": [
                        item("W_RAW", "weapon", "set_life_force", 0.17),
                        item("W_SET", "weapon", "set_calamity", 0.06),
                    ],
                    "armor": [
                        item("A_RAW", "armor", "set_life_force", 0.17),
                        item("A_SET", "armor", "set_calamity", 0.06),
                    ],
                    "bracelet": [],
                    "necklace": [],
                    "ring": [],
                }
                candidates, bonuses = _select_set_aware_candidates(database, by_slot, 1)
                self.assertGreater(bonuses["set_calamity"], 0.0)
                self.assertEqual(candidates["weapon"][0].item_id, "W_SET")
                self.assertEqual(candidates["armor"][0].item_id, "A_SET")
            finally:
                database.close()

    def test_recommendation_evaluates_t1_and_t2_and_keeps_best_physical_build_once(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "equipment.db"
            database = OptimizerEquipmentDatabase(path)
            try:
                database.initialize()
                database.connection.execute(
                    "INSERT OR IGNORE INTO sets(set_id,set_name,required_pieces,slot_group,output_set) "
                    "VALUES ('TEST_NONE','测试无套装',99,NULL,1)"
                )
                self._insert_item(database, "W", "weapon", "set_calamity")
                self._insert_item(database, "A", "armor", "set_calamity")
                self._insert_item(database, "B", "bracelet", "TEST_NONE")
                self._insert_item(database, "N", "necklace", "TEST_NONE")
                self._insert_item(database, "R", "ring", "TEST_NONE")
                database.connection.commit()
            finally:
                database.close()

            result = recommend_hero_core(
                path,
                {
                    "hero_core_id": "SUN_WUKONG",
                    "top_k": 5,
                    "candidate_per_slot": 1,
                    "trials": 1,
                    "screening_warmup": 0,
                    "screening_measurement": 60,
                    "warmup": 0,
                    "measurement": 60,
                    "target_def": 0,
                    "seed": 20260829,
                },
            )
            self.assertEqual(result["candidate_pruning"], "set_aware")
            self.assertEqual(result["equipment_prefilter"]["category"], "output")
            self.assertTrue(result["set_model"]["normalized_v22_effects"])
            self.assertTrue(result["set_model"]["t1_t2_variants"])
            self.assertEqual(result["combinations_screened"], 1)
            self.assertEqual(len(result["results"]), 1)
            best = result["results"][0]
            self.assertEqual(set(best["item_ids"]), {"W", "A", "B", "N", "R"})
            self.assertTrue(best["uses_ascension"])
            self.assertEqual({row["item_id"] for row in best["ascended_items"]}, {"W", "A"})
            self.assertIn("set_warlord", best["active_sets"])
            self.assertEqual(best["active_set_names"], ["战争之主"])
            self.assertGreaterEqual(result["variant_simulations_screened"], 3)
            self.assertGreaterEqual(result["variant_simulations_refined"], 3)

            stored = EquipmentDatabase(path)
            try:
                stored.initialize()
                rows = stored.connection.execute(
                    "SELECT item_id,set_id FROM equipment WHERE item_id IN ('W','A') ORDER BY item_id"
                ).fetchall()
                self.assertEqual({row["item_id"]: row["set_id"] for row in rows}, {"A": "set_calamity", "W": "set_calamity"})
            finally:
                stored.close()


if __name__ == "__main__":
    unittest.main()

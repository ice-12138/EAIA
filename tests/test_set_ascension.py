import tempfile
import unittest
from pathlib import Path

from equipment_db import EquipmentDatabase
from equipment_models import BattleConfig, EffectType
from equipment_optimizer import EquipmentOptimizer
from equipment_set_variants import load_optimizer_set_effects, load_set_evolutions


EXPECTED_EVOLUTIONS = {
    "set_calamity": "set_warlord",
    "set_life_force": "set_immortal_warrior",
    "set_night_terror": "set_ageless_wrath",
    "set_asclepius": "set_invigoration",
    "set_wisdom": "set_soulbound_arcana",
    "set_insight": "set_infernal_roar",
}


class SetAscensionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = EquipmentDatabase(Path(self.temp_dir.name) / "equipment.db")
        self.db.initialize()

    def tearDown(self):
        self.db.close()
        self.temp_dir.cleanup()

    def test_packaged_dictionary_has_all_six_t1_to_t2_evolutions(self):
        self.assertEqual(load_set_evolutions(self.db), EXPECTED_EVOLUTIONS)

    def test_v22_set_effects_are_normalized_for_optimizer(self):
        effects = load_optimizer_set_effects(self.db)
        by_id = {effect.effect_id: effect for effect in effects}

        self.assertEqual(by_id["eff_warlord_atk"].effect_type, EffectType.ATK_PCT)
        self.assertEqual(by_id["eff_warlord_atk"].trigger, "always")
        self.assertAlmostEqual(by_id["eff_warlord_atk"].value, 0.25)
        self.assertEqual(by_id["eff_warlord_as"].effect_type, EffectType.ATK_SPEED)
        self.assertAlmostEqual(by_id["eff_warlord_as"].value, 30.0)
        self.assertEqual(by_id["eff_infernal_basic"].effect_type, EffectType.BASIC_DMG)
        self.assertEqual(by_id["eff_infernal_basic"].trigger, "always")
        self.assertAlmostEqual(by_id["eff_infernal_basic"].value, 0.40)
        self.assertEqual(by_id["eff_wisdom_damage"].effect_type, EffectType.DAMAGE_PCT)
        self.assertEqual(by_id["eff_wisdom_damage"].trigger, "on_ult")

    def _seed_single_physical_build(self, set_id: str) -> EquipmentOptimizer:
        self.db.seed_full_fixture()
        self.db.connection.execute("UPDATE equipment SET set_id=? WHERE item_id IN ('W1','A1')", (set_id,))
        self.db.connection.execute("UPDATE equipment SET available=0 WHERE item_id IN ('B2','N2','R2')")
        self.db.connection.commit()
        return EquipmentOptimizer(self.db)

    def test_two_same_set_t1_items_generate_current_partial_and_full_t2_states(self):
        optimizer = self._seed_single_physical_build("set_calamity")
        variants = optimizer.evaluate_build_variants(
            "H1", ["W1", "A1", "B1", "N1", "R1"], BattleConfig(mode="single", enemy_count=1)
        )
        self.assertEqual({len(result.ascended_items) for result in variants}, {0, 1, 2})
        self.assertEqual(len(variants), 3)
        self.assertTrue(any("set_calamity" in result.active_sets for result in variants))
        self.assertTrue(any("set_warlord" in result.active_sets for result in variants))
        for result in variants:
            self.assertEqual(result.item_ids, ("W1", "A1", "B1", "N1", "R1"))

    def test_search_keeps_one_physical_build_and_recommends_full_warlord_ascension(self):
        optimizer = self._seed_single_physical_build("set_calamity")
        results = optimizer.search("H1", "single", 1, 10)
        self.assertEqual(len(results), 1)
        best = results[0]
        self.assertEqual(best.item_ids, ("W1", "A1", "B1", "N1", "R1"))
        self.assertEqual({item.item_id for item in best.ascended_items}, {"W1", "A1"})
        self.assertEqual({item.from_set_name for item in best.ascended_items}, {"灾难"})
        self.assertEqual({item.to_set_name for item in best.ascended_items}, {"战争之主"})
        self.assertIn("set_warlord", best.active_sets)
        self.assertNotIn("set_calamity", best.active_sets)
        # Hypothetical optimization must never mutate the physical inventory.
        stored = {
            row["item_id"]: row["set_id"]
            for row in self.db.connection.execute("SELECT item_id,set_id FROM equipment WHERE item_id IN ('W1','A1')")
        }
        self.assertEqual(stored, {"W1": "set_calamity", "A1": "set_calamity"})

    def test_exact_tie_prefers_current_t1_and_does_not_recommend_wasted_ascension(self):
        optimizer = self._seed_single_physical_build("set_life_force")
        result = optimizer.simulate_build(
            "H1", ["W1", "A1", "B1", "N1", "R1"], BattleConfig(mode="single", enemy_count=1)
        )
        self.assertEqual(result.ascended_items, ())
        self.assertIn("set_life_force", result.active_sets)


if __name__ == "__main__":
    unittest.main()

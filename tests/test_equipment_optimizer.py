import sqlite3
import tempfile
import unittest
from pathlib import Path

from equipment_db import EquipmentDatabase
from equipment_models import EquipmentItem, EquipmentStat
from equipment_optimizer import EquipmentOptimizer
from equipment_rules import GameRules
from equipment_data import DataValidationError, validate_damage_profile_row, validate_percentage
from equipment_models import BattleConfig


class EquipmentOptimizerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = EquipmentDatabase(Path(self.temp_dir.name) / "equipment.db")
        self.db.initialize()

    def tearDown(self):
        self.db.close()
        self.temp_dir.cleanup()

    def test_schema_is_idempotent_and_has_all_business_tables(self):
        self.db.initialize()
        tables = {
            row[0] for row in self.db.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        self.assertTrue({
            "heroes", "skills", "hero_damage_profiles", "equipment",
            "equipment_stats", "sets", "set_effects", "scenarios", "game_rules",
        } <= tables)

    def test_foreign_key_rejects_stats_for_unknown_item(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.connection.execute(
                "INSERT INTO equipment_stats(item_id, stat_source, stat_type, stat_value) VALUES (?, ?, ?, ?)",
                ("missing", "sub", "ATK_PCT", 0.2),
            )

    def test_panel_caps_crit_rate_and_reports_overflow(self):
        self.db.seed_minimal_fixture()
        optimizer = EquipmentOptimizer(self.db)
        build = optimizer.build_from_item_ids(["W1", "A1", "B1", "N1", "R1"])
        self.assertAlmostEqual(build.panel.crit_rate, 1.0)
        self.assertGreater(build.panel.crit_overflow, 0.0)

    def test_search_returns_legal_top_k_sorted_by_dps(self):
        self.db.seed_minimal_fixture()
        results = EquipmentOptimizer(self.db).search("H1", "S1", top_k=2)
        self.assertEqual(len(results), 2)
        self.assertGreaterEqual(results[0].dps, results[1].dps)
        for result in results:
            self.assertEqual(len(result.item_ids), 5)
            self.assertEqual(len(set(result.item_ids)), 5)
            self.assertEqual({"weapon", "armor", "bracelet", "necklace", "ring"}, set(result.slots))
            self.assertGreater(result.total_damage, 0)

    def test_validation_rejects_percentages_outside_decimal_range(self):
        with self.assertRaises(DataValidationError):
            validate_percentage("crit_rate", 34)
        with self.assertRaises(DataValidationError):
            validate_damage_profile_row({"basic_share": 0.5, "skill_share": 0.5, "ultimate_share": -0.1, "ult_uptime_base": 0.2})

    def test_full_skill_schema_and_simulation_counts_ultimate_and_aoe_targets(self):
        self.db.seed_full_fixture()
        result = EquipmentOptimizer(self.db).simulate_build(
            "H1", ["W1", "A1", "B1", "N1", "R1"], BattleConfig(mode="aoe", enemy_count=3)
        )
        self.assertEqual(result.duration, 60.0)
        self.assertEqual(result.mode, "aoe")
        self.assertGreater(result.ultimate_count, 0)
        self.assertGreater(result.source_damage["ultimate"], 0)
        self.assertEqual(result.model_coverage, "partial")
        self.assertGreater(result.set_uptime.get("ult_buff", 0.0), 0.0)

    def test_direct_damage_false_skill_is_excluded_and_followup_is_reported(self):
        self.db.seed_full_fixture()
        result = EquipmentOptimizer(self.db).simulate_build(
            "H1", ["W1", "A1", "B1", "N1", "R1"], BattleConfig(mode="single", enemy_count=1)
        )
        self.assertEqual(result.source_damage["dot"], 0.0)
        self.assertGreater(result.source_damage["followup"], 0.0)

    def test_v11_search_returns_simulation_results_for_requested_mode(self):
        self.db.seed_full_fixture()
        results = EquipmentOptimizer(self.db).search("H1", "aoe", 3, 2)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].mode, "aoe")
        self.assertEqual(results[0].enemy_count, 3)
        self.assertGreaterEqual(results[0].total_damage, results[1].total_damage)


if __name__ == "__main__":
    unittest.main()

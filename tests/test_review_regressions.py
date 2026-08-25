import tempfile
import unittest
from pathlib import Path

from equipment_db import EquipmentDatabase
from equipment_optimizer import EquipmentOptimizer
from equipment_persistence import build_database_rows
from main_stat_cap_learner import MainStatCapLearner


class ReviewRegressionTests(unittest.TestCase):
    def test_ocr_percentages_use_decimal_units_and_attack_speed_keeps_its_type(self):
        record = {
            "item_id": "OCR_TEST",
            "profile": "general",
            "fully_unlocked": True,
            "quality": {"raw_text": "装备"},
            "slot": {"raw_text": "测试手镯"},
            "primary": {"raw_text": "攻击加成\n66%", "value": 66},
            "set_name": {"raw_text": "测试套装"},
            "sub_attributes": [
                {"index": 1, "raw_text": "暴击率\n17%", "value": 17},
                {"index": 2, "raw_text": "攻击速度\n36", "value": 36},
                {"index": 3, "raw_text": "怒气恢复效率\n13.5%", "value": 13.5},
            ],
        }
        _, rows, _ = build_database_rows(record)
        stats = {(source, stat_type): value for _, _, source, stat_type, value in rows}

        self.assertAlmostEqual(stats[("main", "ATK_PCT")], 0.66)
        self.assertAlmostEqual(stats[("sub", "CRIT_RATE")], 0.17)
        self.assertEqual(stats[("sub", "ATK_SPEED")], 36.0)
        self.assertAlmostEqual(stats[("sub", "RAGE_REGEN")], 0.135)
        self.assertNotIn(("sub", "ATK_FLAT"), stats)

    def test_v11_search_evaluates_later_left_slot_combinations(self):
        with tempfile.TemporaryDirectory() as directory:
            database = EquipmentDatabase(Path(directory) / "equipment.db")
            try:
                database.initialize()
                database.seed_full_fixture()
                database.connection.execute(
                    "INSERT INTO equipment(item_id,slot,set_id,available,slot_id) VALUES (?,?,?,1,?)",
                    ("W2", "weapon", "SET_B", "weapon"),
                )
                database.connection.execute(
                    "INSERT INTO equipment_stats(item_id,stat_index,stat_source,stat_type,stat_value) VALUES (?,?,?,?,?)",
                    ("W2", 0, "main", "ATK_PCT", 2.0),
                )
                database.connection.commit()

                result = EquipmentOptimizer(database).search("H1", "single", 1, 1)[0]
                self.assertIn("W2", result.item_ids)
            finally:
                database.close()

    def test_v22_queue_and_learning_tables_are_not_overloaded(self):
        with tempfile.TemporaryDirectory() as directory:
            database = EquipmentDatabase(Path(directory) / "equipment.db")
            try:
                database.initialize()
                ocr_columns = {
                    row[1] for row in database.connection.execute("PRAGMA table_info(ocr_import_queue)")
                }
                observation_columns = {
                    row[1] for row in database.connection.execute("PRAGMA table_info(stat_observation_queue)")
                }
                self.assertIn("import_id", ocr_columns)
                self.assertIn("validation_status", ocr_columns)
                self.assertIn("queue_id", observation_columns)
                self.assertIn("reason", observation_columns)
            finally:
                database.close()

    def test_main_stat_learner_uses_v22_dictionary_key_and_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            database = EquipmentDatabase(Path(directory) / "equipment.db")
            try:
                database.initialize()
                learner = MainStatCapLearner(database.connection, confirmations_required=2)
                first = learner.learn(
                    item_id="a", quality_id="mythic_red", slot="bracelet",
                    stat_type="CRIT_RATE", enhancement_level=16, value=0.60,
                )
                second = learner.learn(
                    item_id="b", quality_id="mythic_red", slot="bracelet",
                    stat_type="CRIT_RATE", enhancement_level=16, value=0.60,
                )
                self.assertIn(first.status, {"provisional", "verified"})
                self.assertEqual(second.status, "verified")
                self.assertAlmostEqual(learner.get_cap(slot="bracelet", stat_type="CRIT_RATE"), 0.60)
            finally:
                database.close()


if __name__ == "__main__":
    unittest.main()

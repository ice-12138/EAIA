import tempfile
import unittest
from pathlib import Path

from equipment_db import EquipmentDatabase
from equipment_optimizer import EquipmentOptimizer
from equipment_persistence import build_database_rows


class ReviewRegressionTests(unittest.TestCase):
    def test_ocr_percentages_use_decimal_units_and_all_known_stats_are_preserved(self):
        record = {"item_id":"OCR_TEST","profile":"general","fully_unlocked":False,"quality":{"raw_text":"红色品质"},"slot":{"raw_text":"测试手镯"},"primary":{"raw_text":"攻击加成\n66%","value":66},"set_name":{"raw_text":"测试套装"},"sub_attributes":[{"index":1,"raw_text":"暴击率\n17%","value":17,"locked":False},{"index":2,"raw_text":"攻击速度\n36","value":36,"locked":False},{"index":3,"raw_text":"生命加成\n13.5%","value":13.5,"locked":False},{"index":4,"raw_text":"暴击伤害\n+16解锁","value":-1,"locked":True}]}
        _, rows, _ = build_database_rows(record)
        stats = {stat_type: (value, index, unlocked, unlock_level) for _, index, _, stat_type, value, unlock_level, unlocked in rows}
        self.assertAlmostEqual(stats["ATK_PCT"][0], 0.66)
        self.assertAlmostEqual(stats["CRIT_RATE"][0], 0.17)
        self.assertEqual(stats["ATK_SPEED"][0], 36.0)
        self.assertAlmostEqual(stats["HP_PCT"][0], 0.135)
        self.assertIsNone(stats["CRIT_DMG"][0])
        self.assertEqual(stats["CRIT_DMG"][1], 4)
        self.assertEqual(stats["CRIT_DMG"][2], 0)
        self.assertEqual(stats["CRIT_DMG"][3], 16)

    def test_schema_uses_indexed_stats_and_effective_view(self):
        with tempfile.TemporaryDirectory() as directory:
            database = EquipmentDatabase(Path(directory) / "equipment.db")
            try:
                database.initialize()
                info = database.connection.execute("PRAGMA table_info(equipment_stats)").fetchall()
                pk = [row[1] for row in sorted((row for row in info if row[5]), key=lambda row: row[5])]
                self.assertEqual(pk, ["item_id", "stat_index"])
                view = database.connection.execute("SELECT name FROM sqlite_master WHERE type='view' AND name='v_equipment_stat_effective'").fetchone()
                self.assertIsNotNone(view)
            finally:
                database.close()

    def test_v11_search_evaluates_later_left_slot_combinations(self):
        with tempfile.TemporaryDirectory() as directory:
            database = EquipmentDatabase(Path(directory) / "equipment.db")
            try:
                database.initialize()
                database.seed_full_fixture()
                database.connection.execute("INSERT INTO equipment(item_id, slot, set_id, available) VALUES (?, ?, ?, 1)", ("W2", "weapon", "SET_B"))
                database.connection.execute("INSERT INTO equipment_stats(item_id, stat_index, stat_source, stat_type, stat_value) VALUES (?, ?, ?, ?, ?)", ("W2", 0, "main", "ATK_PCT", 2.0))
                database.connection.commit()
                result = EquipmentOptimizer(database).search("H1", "single", 1, 1)[0]
                self.assertIn("W2", result.item_ids)
            finally:
                database.close()


if __name__ == "__main__":
    unittest.main()

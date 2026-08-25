import json
import tempfile
import unittest
from pathlib import Path

from equipment_db import EquipmentDatabase
from equipment_persistence import build_database_rows, is_upgrade_of


class EquipmentPersistenceTests(unittest.TestCase):
    def test_existing_fine_ocr_record_is_written_and_is_idempotent(self):
        root = Path(r"E:\code\EAIA")
        screenshot = root / "captures" / "current_detail_retry" / "screen_20260825_152418_195.jpeg"
        record = json.loads((root / "fine_ocr_test" / "result.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            database = EquipmentDatabase(Path(directory) / "equipment.db")
            database.initialize()
            database.upsert_recognized_equipment(record, source_screenshot=screenshot)
            database.upsert_recognized_equipment(record, source_screenshot=screenshot)
            item = database.connection.execute("SELECT * FROM equipment WHERE item_id=?", (record["item_id"],)).fetchone()
            stats = database.connection.execute("SELECT * FROM equipment_stats WHERE item_id=? ORDER BY stat_index", (record["item_id"],)).fetchall()
            recognition = database.connection.execute("SELECT * FROM equipment_recognition WHERE item_id=?", (record["item_id"],)).fetchone()
            self.assertEqual(item["slot"], "weapon")
            self.assertEqual(item["slot_id"], "weapon")
            self.assertEqual(item["locked"], 0)
            self.assertGreaterEqual(len(stats), 1)
            self.assertEqual(len({row["stat_index"] for row in stats}), len(stats))
            self.assertTrue(any(row["stat_type"] == "ATK_FLAT" for row in stats))
            self.assertEqual(recognition["fully_unlocked"], 0)
            self.assertEqual(recognition["source_screenshot"], str(screenshot.resolve()))
            primary = record["primary"]
            self.assertEqual(recognition["main_stat_name"], primary["raw_text"].splitlines()[0])
            self.assertEqual(recognition["main_stat_value"], primary["value"])
            for sub in record["sub_attributes"]:
                index = sub["index"]
                self.assertEqual(recognition[f"sub_stat_{index}_name"], sub["raw_text"].splitlines()[0])
                expected_value = sub["value"] if sub["value"] >= 0 else None
                self.assertEqual(recognition[f"sub_stat_{index}_value"], expected_value)
            view = database.connection.execute("SELECT main_stat_name, main_stat_value, sub_stat_4_name, sub_stat_4_value FROM v_equipment_full WHERE item_id=? LIMIT 1", (record["item_id"],)).fetchone()
            self.assertEqual(view["main_stat_name"], recognition["main_stat_name"])
            self.assertEqual(view["sub_stat_4_value"], recognition["sub_stat_4_value"])
            self.assertEqual(database.connection.execute("SELECT COUNT(*) FROM equipment").fetchone()[0], 1)
            database.close()

    def test_dictionary_hook_is_defined_but_not_used_by_default(self):
        record = {"item_id":"x","profile":"general","fully_unlocked":True,"quality":{"raw_text":"装备"},"slot":{"raw_text":"武器"},"primary":{"raw_text":"攻击 10","value":10},"set_name":{"raw_text":"套装"},"sub_attributes":[]}
        item, stats, _ = build_database_rows(record)
        self.assertEqual(item[1], "weapon")
        self.assertEqual(stats[0][3], "ATK_FLAT")
        self.assertEqual(stats[0][1], 0)

    def test_upgrade_reuses_previous_item_and_updates_it(self):
        root = Path(r"E:\code\EAIA")
        record = json.loads((root / "fine_ocr_test" / "result.json").read_text(encoding="utf-8"))
        upgraded = json.loads(json.dumps(record))
        upgraded["item_id"] = "new_scan_item"
        upgraded["primary"]["value"] = 800
        upgraded["primary"]["raw_text"] = "攻击\n800"
        upgraded["sub_attributes"][3]["value"] = 16
        upgraded["sub_attributes"][3]["raw_text"] = "暴击伤害\n+16"
        upgraded["sub_attributes"][3]["locked"] = False
        upgraded["fully_unlocked"] = True
        self.assertTrue(is_upgrade_of(record, upgraded))
        with tempfile.TemporaryDirectory() as directory:
            database = EquipmentDatabase(Path(directory) / "equipment.db")
            try:
                database.initialize()
                first = database.upsert_recognized_equipment(record)
                second = database.upsert_recognized_equipment(upgraded)
                self.assertFalse(first["matched_upgrade"])
                self.assertTrue(second["matched_upgrade"])
                self.assertEqual(second["item_id"], record["item_id"])
                self.assertEqual(database.connection.execute("SELECT COUNT(*) FROM equipment").fetchone()[0], 1)
                saved = database.connection.execute("SELECT primary_text, fully_unlocked FROM equipment_recognition WHERE item_id=?", (record["item_id"],)).fetchone()
                self.assertEqual(saved["primary_text"], "攻击\n800")
                self.assertEqual(saved["fully_unlocked"], 1)
            finally:
                database.close()


if __name__ == "__main__":
    unittest.main()

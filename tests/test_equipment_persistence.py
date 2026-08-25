import copy
import tempfile
import unittest
from pathlib import Path

from equipment_db import EquipmentDatabase
from equipment_persistence import build_database_rows, is_upgrade_of


def fine_record(item_id="item_test"):
    return {
        "item_id": item_id,
        "profile": "general",
        "fully_unlocked": False,
        "quality": {"raw_text": "神话", "confidence": 0.99},
        "enhancement_level": {"raw_text": "+12", "value": 12, "confidence": 0.99},
        "slot": {"raw_text": "武器", "confidence": 0.99},
        "primary": {"raw_text": "攻击\n700", "value": 700, "confidence": 0.99},
        "set_name": {"raw_text": "测试套装", "confidence": 0.99},
        "sub_attributes": [
            {"index": 1, "raw_text": "攻击加成\n20%", "value": 20, "locked": False, "confidence": 0.99},
            {"index": 2, "raw_text": "暴击率\n10%", "value": 10, "locked": False, "confidence": 0.99},
            {"index": 3, "raw_text": "攻击速度\n36", "value": 36, "locked": False, "confidence": 0.99},
            {"index": 4, "raw_text": "暴击伤害\n+16解锁", "value": -1, "locked": True, "confidence": 0.99},
        ],
    }


class EquipmentPersistenceTests(unittest.TestCase):
    def test_fine_ocr_record_is_written_and_is_idempotent(self):
        record = fine_record()
        with tempfile.TemporaryDirectory() as directory:
            screenshot = Path(directory) / "screen.jpeg"
            screenshot.write_bytes(b"fixture")
            database = EquipmentDatabase(Path(directory) / "equipment.db")
            try:
                database.initialize()
                database.upsert_recognized_equipment(record, source_screenshot=screenshot)
                database.upsert_recognized_equipment(record, source_screenshot=screenshot)
                item = database.connection.execute(
                    "SELECT * FROM equipment WHERE item_id=?", (record["item_id"],)
                ).fetchone()
                stats = database.connection.execute(
                    "SELECT * FROM equipment_stats WHERE item_id=? ORDER BY stat_index", (record["item_id"],)
                ).fetchall()
                recognition = database.connection.execute(
                    "SELECT * FROM equipment_recognition WHERE item_id=?", (record["item_id"],)
                ).fetchone()
                self.assertEqual(item["slot"], "weapon")
                self.assertEqual(item["slot_id"], "weapon")
                self.assertEqual(item["locked"], 0)
                self.assertEqual(len(stats), 5)
                self.assertEqual([row["stat_index"] for row in stats], [0, 1, 2, 3, 4])
                by_index = {row["stat_index"]: row for row in stats}
                self.assertEqual(by_index[0]["stat_type"], "ATK_FLAT")
                self.assertAlmostEqual(by_index[1]["stat_value"], 0.20)
                self.assertAlmostEqual(by_index[2]["stat_value"], 0.10)
                self.assertEqual(by_index[3]["stat_type"], "ATK_SPEED")
                self.assertEqual(by_index[3]["stat_value"], 36)
                self.assertEqual(by_index[4]["stat_type"], "CRIT_DMG")
                self.assertIsNone(by_index[4]["stat_value"])
                self.assertEqual(by_index[4]["unlock_level"], 16)
                self.assertEqual(by_index[4]["is_unlocked"], 0)
                self.assertEqual(recognition["fully_unlocked"], 0)
                self.assertEqual(recognition["source_screenshot"], str(screenshot.resolve()))
                self.assertEqual(recognition["main_stat_name"], "攻击")
                self.assertEqual(recognition["main_stat_value"], 700)
                self.assertEqual(recognition["sub_stat_4_name"], "暴击伤害")
                self.assertIsNone(recognition["sub_stat_4_value"])
                view = database.connection.execute(
                    """SELECT main_stat_name,main_stat_value,sub_stat_4_name,sub_stat_4_value
                       FROM v_equipment_full WHERE item_id=? LIMIT 1""",
                    (record["item_id"],),
                ).fetchone()
                self.assertEqual(view["main_stat_name"], "攻击")
                self.assertIsNone(view["sub_stat_4_value"])
                self.assertEqual(database.connection.execute("SELECT COUNT(*) FROM equipment").fetchone()[0], 1)
            finally:
                database.close()

    def test_dictionary_hook_is_defined_but_not_used_by_default(self):
        record = {
            "item_id": "x",
            "profile": "general",
            "fully_unlocked": True,
            "quality": {"raw_text": "装备"},
            "slot": {"raw_text": "武器"},
            "primary": {"raw_text": "攻击 10", "value": 10},
            "set_name": {"raw_text": "套装"},
            "sub_attributes": [],
        }
        item, stats, _ = build_database_rows(record)
        self.assertEqual(item[1], "weapon")
        self.assertEqual(stats[0][3], "ATK_FLAT")
        self.assertEqual(stats[0][1], 0)

    def test_upgrade_reuses_previous_item_and_updates_it(self):
        record = fine_record()
        upgraded = copy.deepcopy(record)
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
                saved = database.connection.execute(
                    "SELECT primary_text,fully_unlocked FROM equipment_recognition WHERE item_id=?",
                    (record["item_id"],),
                ).fetchone()
                self.assertEqual(saved["primary_text"], "攻击\n800")
                self.assertEqual(saved["fully_unlocked"], 1)
                unlocked = database.connection.execute(
                    "SELECT stat_value,is_unlocked FROM equipment_stats WHERE item_id=? AND stat_index=4",
                    (record["item_id"],),
                ).fetchone()
                self.assertAlmostEqual(unlocked["stat_value"], 0.16)
                self.assertEqual(unlocked["is_unlocked"], 1)
            finally:
                database.close()


if __name__ == "__main__":
    unittest.main()

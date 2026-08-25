import json
import tempfile
import unittest
from pathlib import Path

from equipment_db import EquipmentDatabase
from equipment_persistence import build_database_rows, is_upgrade_of


def make_record(item_id="item_1", *, upgraded=False):
    return {
        "item_id": item_id,
        "profile": "general",
        "fully_unlocked": upgraded,
        "quality": {"raw_text": "装备"},
        "enhancement_level": {"raw_text": "+16", "value": 16},
        "slot": {"raw_text": "测试武器"},
        "primary": {"raw_text": f"攻击\n{800 if upgraded else 760}", "value": 800 if upgraded else 760},
        "set_name": {"raw_text": "测试套装"},
        "sub_attributes": [
            {"index": 1, "raw_text": "暴击率\n17%", "value": 17, "locked": False, "confidence": 0.99},
            {"index": 2, "raw_text": "攻击速度\n36", "value": 36, "locked": False, "confidence": 0.99},
            {"index": 3, "raw_text": "怒气恢复效率\n13.5%", "value": 13.5, "locked": False, "confidence": 0.99},
            {
                "index": 4,
                "raw_text": "暴击伤害\n+16" if upgraded else "暴击伤害\n+16解锁",
                "value": 16 if upgraded else -1,
                "locked": not upgraded,
                "confidence": 0.99,
            },
        ],
    }


class EquipmentPersistenceTests(unittest.TestCase):
    def test_fine_ocr_record_is_written_normalized_and_idempotent(self):
        record = make_record()
        with tempfile.TemporaryDirectory() as directory:
            database = EquipmentDatabase(Path(directory) / "equipment.db")
            try:
                database.initialize()
                database.upsert_recognized_equipment(record)
                database.upsert_recognized_equipment(record)

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
                self.assertEqual(item["quality_id"], "mythic_red")
                self.assertEqual(item["enhancement_level"], 16)
                self.assertEqual([row["stat_index"] for row in stats], [0, 1, 2, 3])
                values = {row["stat_type"]: row["stat_value"] for row in stats}
                self.assertEqual(values["ATK_FLAT"], 760)
                self.assertAlmostEqual(values["CRIT_RATE"], 0.17)
                self.assertEqual(values["ATK_SPEED"], 36)
                self.assertAlmostEqual(values["RAGE_REGEN"], 0.135)
                self.assertEqual(recognition["set_name_text"], "测试套装")
                self.assertEqual(database.connection.execute("SELECT COUNT(*) FROM equipment").fetchone()[0], 1)
            finally:
                database.close()

    def test_build_rows_preserves_duplicate_stat_types_by_index(self):
        record = make_record()
        record["sub_attributes"][1] = {
            "index": 2, "raw_text": "暴击率\n9%", "value": 9, "locked": False, "confidence": 0.99
        }
        _, stats, _ = build_database_rows(record)
        crit_rows = [row for row in stats if row[3] == "CRIT_RATE"]
        self.assertEqual([(row[1], row[4]) for row in crit_rows], [(1, 0.17), (2, 0.09)])

    def test_upgrade_reuses_previous_item_and_updates_it(self):
        record = make_record()
        upgraded = make_record("new_scan_item", upgraded=True)
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
                stat = database.connection.execute(
                    "SELECT stat_value FROM equipment_stats WHERE item_id=? AND stat_index=4",
                    (record["item_id"],),
                ).fetchone()
                self.assertAlmostEqual(stat[0], 0.16)
            finally:
                database.close()

    def test_raw_recognition_json_remains_audit_source(self):
        record = make_record()
        with tempfile.TemporaryDirectory() as directory:
            database = EquipmentDatabase(Path(directory) / "equipment.db")
            try:
                database.initialize()
                database.upsert_recognized_equipment(record)
                raw = database.connection.execute(
                    "SELECT raw_result FROM equipment_recognition WHERE item_id=?", (record["item_id"],)
                ).fetchone()[0]
                saved = json.loads(raw)
                self.assertEqual(saved["sub_attributes"][0]["value"], 17)
                self.assertEqual(saved["sub_attributes"][0]["raw_text"], "暴击率\n17%")
            finally:
                database.close()


if __name__ == "__main__":
    unittest.main()

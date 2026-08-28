import sqlite3
import tempfile
import unittest
from pathlib import Path

from data_manager import create_resource, delete_equipment, delete_resource, list_equipment, list_resource, save_equipment, update_resource
from equipment_db import EquipmentDatabase


class DataManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "equipment.db"
        db = EquipmentDatabase(self.database)
        db.initialize()
        db.close()

    def tearDown(self):
        self.temp.cleanup()

    def test_generic_crud(self):
        create_resource(self.database, "equipment_categories", {
            "category_id": "test_user", "category_name": "用户测试", "description": "可编辑", "sort_order": 99,
        })
        payload = list_resource(self.database, "equipment_categories")
        self.assertTrue(any(row["category_id"] == "test_user" for row in payload["rows"]))
        update_resource(self.database, "equipment_categories", {"category_id": "test_user"}, {"category_name": "用户修改"})
        payload = list_resource(self.database, "equipment_categories")
        self.assertTrue(any(row["category_name"] == "用户修改" for row in payload["rows"]))
        delete_resource(self.database, "equipment_categories", {"category_id": "test_user"})
        payload = list_resource(self.database, "equipment_categories")
        self.assertFalse(any(row["category_id"] == "test_user" for row in payload["rows"]))

    def test_equipment_round_trip(self):
        with sqlite3.connect(self.database) as connection:
            set_id = connection.execute("SELECT set_id FROM sets LIMIT 1").fetchone()[0]
            slot_id = connection.execute("SELECT slot_id FROM equipment_slots LIMIT 1").fetchone()[0]
            quality_id = connection.execute("SELECT quality_id FROM gear_qualities LIMIT 1").fetchone()[0]
            # Choose a dictionary stat that is valid as an equipment main stat;
            # the v2.2 dictionary also contains simulation-only stats.
            stat_type = connection.execute(
                "SELECT stat_type FROM stat_definitions WHERE can_main_stat=1 LIMIT 1"
            ).fetchone()[0]
        connection.close()
        values = {
            "item_id": "UI_TEST_ITEM", "slot_id": slot_id, "set_id": set_id, "quality_id": quality_id,
            "enhancement_level": 16, "available": True, "locked": False, "source": "unittest",
            "stats": [{"stat_index": 0, "stat_source": "main", "stat_type": stat_type, "stat_value": 123.0, "is_unlocked": True}],
        }
        save_equipment(self.database, values)
        row = next(row for row in list_equipment(self.database)["rows"] if row["item_id"] == "UI_TEST_ITEM")
        self.assertEqual(row["enhancement_level"], 16)
        self.assertEqual(row["stats"][0]["stat_value"], 123.0)
        values["enhancement_level"] = 15
        values["stats"][0]["stat_value"] = 456.0
        save_equipment(self.database, values, original_item_id="UI_TEST_ITEM")
        row = next(row for row in list_equipment(self.database)["rows"] if row["item_id"] == "UI_TEST_ITEM")
        self.assertEqual(row["enhancement_level"], 15)
        self.assertEqual(row["stats"][0]["stat_value"], 456.0)
        delete_equipment(self.database, "UI_TEST_ITEM")
        self.assertFalse(any(row["item_id"] == "UI_TEST_ITEM" for row in list_equipment(self.database)["rows"]))


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest
from pathlib import Path

from equipment_db import EquipmentDatabase
from official_hero_data import seed_official_hero_catalog
from web_api import database_payload


class WebApiDatabaseTests(unittest.TestCase):
    def test_payloads_are_read_from_sqlite(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "equipment.db"
            database = EquipmentDatabase(path)
            try:
                database.initialize()
                seed_official_hero_catalog(database.connection)
                database.connection.execute(
                    "INSERT INTO sets(set_id,set_name,required_pieces,slot_group,output_set) VALUES (?,?,?,?,?)",
                    ("API_SET", "API 测试套装", 2, "right3", 1),
                )
                database.connection.commit()
            finally:
                database.close()

            heroes, catalog, equipment = database_payload(path)
            self.assertGreaterEqual(len(heroes["heroes"]), 58)
            self.assertIn("API_SET", {row["set_id"] for row in catalog["sets"]})
            self.assertIn("v_equipment_full", equipment)
            self.assertIsInstance(heroes["skills"][0]["hero_key"], str)


if __name__ == "__main__":
    unittest.main()

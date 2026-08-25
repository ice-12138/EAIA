import tempfile
import unittest
from pathlib import Path

from equipment_db import EquipmentDatabase
from official_hero_data import load_optimizer_usable_official_basics, seed_official_hero_catalog


class OfficialHeroDataTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = EquipmentDatabase(Path(self.temp_dir.name) / "equipment.db")
        self.db.initialize()

    def tearDown(self):
        self.db.close()
        self.temp_dir.cleanup()

    def test_official_catalog_is_seeded_and_idempotent(self):
        first = seed_official_hero_catalog(self.db.connection)
        second = seed_official_hero_catalog(self.db.connection)
        self.assertEqual(first, second)
        self.assertGreaterEqual(first["heroes"], 50)
        self.assertGreaterEqual(first["skills"], 20)
        self.assertGreaterEqual(first["optimizer_usable_skills"], 5)
        self.assertGreaterEqual(first.get("numeric_partial", 0), 7)

    def test_morrigan_official_numeric_basic_is_preserved(self):
        seed_official_hero_catalog(self.db.connection)
        row = self.db.connection.execute(
            """SELECT coefficient,target_cap,optimizer_usable,source_url
               FROM official_skill_catalog
               WHERE hero_key='MORRIGAN' AND skill_key='basic_magic'"""
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertAlmostEqual(row["coefficient"], 0.70)
        self.assertEqual(row["target_cap"], "3")
        self.assertEqual(row["optimizer_usable"], 1)
        self.assertIn("taptap.cn", row["source_url"])

    def test_incomplete_official_data_does_not_pollute_production_heroes(self):
        seed_official_hero_catalog(self.db.connection)
        self.assertIsNone(
            self.db.connection.execute("SELECT hero_id FROM heroes WHERE hero_id='MORRIGAN'").fetchone()
        )

    def test_optimizer_usable_basics_are_only_numeric_official_records(self):
        seed_official_hero_catalog(self.db.connection)
        rows = load_optimizer_usable_official_basics(self.db.connection)
        names = {row["hero_name"] for row in rows}
        self.assertTrue({"摩瑞甘", "赫克斯", "维尔娜", "兹丽忒", "西拉斯", "妲丽亚"} <= names)
        for row in rows:
            self.assertIsNotNone(row["coefficient"])
            self.assertIsNotNone(row["target_cap"])
            self.assertIn("taptap.cn", row["source_url"])


if __name__ == "__main__":
    unittest.main()

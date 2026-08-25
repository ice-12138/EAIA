import tempfile
import unittest
from pathlib import Path

from equipment_db import EquipmentDatabase


class EquipmentV22Tests(unittest.TestCase):
    def test_v22_dictionary_is_seeded_and_idempotent(self):
        temp_dir = tempfile.TemporaryDirectory()
        db = None
        try:
            db = EquipmentDatabase(Path(temp_dir.name) / "equipment.db")
            db.initialize()
            db.initialize()
            self.assertEqual(db.connection.execute("SELECT COUNT(*) FROM equipment_categories").fetchone()[0], 4)
            self.assertEqual(db.connection.execute("SELECT COUNT(*) FROM equipment_slots").fetchone()[0], 5)
            self.assertEqual(db.connection.execute("SELECT COUNT(*) FROM sets").fetchone()[0], 48)
            self.assertEqual(db.connection.execute("SELECT COUNT(*) FROM ocr_aliases").fetchone()[0], 13)
            self.assertEqual(db.connection.execute("SELECT COUNT(*) FROM main_stat_max_values").fetchone()[0], 13)
            self.assertEqual(db.connection.execute("SELECT COUNT(*) FROM special_effect_definitions").fetchone()[0], 18)
            self.assertEqual(
                db.connection.execute(
                    "SELECT max_value_at_level_cap FROM main_stat_max_values WHERE quality_id='mythic_red' AND slot_scope='right' AND stat_type='crit_rate'"
                ).fetchone()[0],
                0.60,
            )
            self.assertEqual(db.connection.execute("SELECT rule_value FROM game_rules WHERE rule_key='max_hero_level'").fetchone()[0], "60")
        finally:
            if db is not None:
                db.close()
            temp_dir.cleanup()


if __name__ == "__main__":
    unittest.main()

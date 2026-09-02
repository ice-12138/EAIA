import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from equipment_db import EquipmentDatabase


class V22SetMetadataRepairTests(unittest.TestCase):
    @staticmethod
    def _set_row(connection, set_id="set_unshaken_will"):
        return connection.execute(
            """SELECT set_id,set_name,set_tier_id,required_pieces,slot_group,category_id,active
               FROM sets WHERE set_id=?""",
            (set_id,),
        ).fetchone()

    def test_initialize_repairs_stale_canonical_set_structure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "equipment.db"
            database = EquipmentDatabase(path)
            try:
                database.initialize()
                database.connection.execute(
                    """UPDATE sets
                       SET set_tier_id=NULL, required_pieces=1, slot_group='right3',
                           category_id=NULL, active=1
                       WHERE set_id='set_unshaken_will'"""
                )
                database.connection.execute(
                    """INSERT INTO sets(set_id,set_name,required_pieces,slot_group,output_set)
                       VALUES ('OCR_TEST_SET','用户临时套装',1,NULL,0)"""
                )
                database.connection.commit()
            finally:
                database.close()

            repaired = EquipmentDatabase(path)
            try:
                repaired.initialize()
                row = self._set_row(repaired.connection)
                self.assertIsNotNone(row)
                self.assertEqual(row["set_name"], "不灭意志")
                self.assertEqual(row["set_tier_id"], "T3")
                self.assertEqual(row["required_pieces"], 3)
                self.assertEqual(row["slot_group"], "right")
                self.assertEqual(row["category_id"], "defense")
                self.assertEqual(row["active"], 1)

                custom = repaired.connection.execute(
                    "SELECT set_name,required_pieces,slot_group FROM sets WHERE set_id='OCR_TEST_SET'"
                ).fetchone()
                self.assertEqual(tuple(custom), ("用户临时套装", 1, None))
            finally:
                repaired.close()

    def test_repository_inventory_gets_canonical_unshaken_metadata_on_initialize(self):
        source = Path(__file__).resolve().parents[1] / "data" / "equipment.db"
        if not source.exists():
            self.skipTest("repository equipment database is unavailable")

        with tempfile.TemporaryDirectory() as directory:
            copied = Path(directory) / "equipment.db"
            shutil.copyfile(source, copied)

            raw = sqlite3.connect(copied)
            raw.row_factory = sqlite3.Row
            try:
                before = self._set_row(raw)
                if before is not None:
                    print(
                        "repository set_unshaken_will before initialize:",
                        dict(before),
                    )
            finally:
                raw.close()

            database = EquipmentDatabase(copied)
            try:
                database.initialize()
                row = self._set_row(database.connection)
                self.assertIsNotNone(row)
                self.assertEqual(row["required_pieces"], 3)
                self.assertEqual(row["slot_group"], "right")
                self.assertEqual(row["category_id"], "defense")
                self.assertEqual(row["set_tier_id"], "T3")
            finally:
                database.close()


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest
from pathlib import Path

from data_manager import list_equipment, save_equipment
from equipment_db import EquipmentDatabase
from optimizer_projection import OptimizerEquipmentDatabase


class EquipmentEditorSemanticsTests(unittest.TestCase):
    def _database_path(self, directory: str) -> Path:
        path = Path(directory) / "equipment.db"
        database = EquipmentDatabase(path)
        try:
            database.initialize()
            database.connection.execute(
                """INSERT OR IGNORE INTO sets(
                       set_id,set_name,required_pieces,slot_group,output_set
                   ) VALUES ('TEST_SET','测试套装',3,'right',1)"""
            )
            database.connection.commit()
        finally:
            database.close()
        return path

    @staticmethod
    def _payload() -> dict:
        return {
            "item_id": "ANCIENT_MYTHIC",
            "slot_id": "bracelet",
            "set_id": "TEST_SET",
            "quality_id": "ancient_mythic_red",
            "enhancement_level": 12,
            "locked": False,
            "available": True,
            "stats": [
                {"stat_index": 0, "stat_source": "main", "stat_type": "ATK_PCT", "stat_value": 0.34, "is_unlocked": False},
                {"stat_index": 1, "stat_source": "sub", "stat_type": "HP_PCT", "stat_value": 0.19, "is_unlocked": False},
                {"stat_index": 2, "stat_source": "sub", "stat_type": "CRIT_DMG", "stat_value": 0.26, "is_unlocked": False},
                {"stat_index": 3, "stat_source": "sub", "stat_type": "ATK_FLAT", "stat_value": 267, "is_unlocked": False},
                {"stat_index": 4, "stat_source": "sub", "stat_type": "CRIT_RATE", "stat_value": None, "is_unlocked": True, "estimate_override": 0.15},
            ],
        }

    def test_virtual_ancient_quality_persists_base_quality_and_normalizes_unlock_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._database_path(directory)
            save_equipment(path, self._payload())

            database = EquipmentDatabase(path)
            try:
                row = database.connection.execute(
                    "SELECT quality_id,is_ancient FROM equipment WHERE item_id='ANCIENT_MYTHIC'"
                ).fetchone()
                self.assertEqual(row[0], "mythic_red")
                self.assertEqual(row[1], 1)
                stats = database.connection.execute(
                    "SELECT stat_index,is_unlocked FROM equipment_stats WHERE item_id='ANCIENT_MYTHIC' ORDER BY stat_index"
                ).fetchall()
                self.assertEqual([row[1] for row in stats], [1, 1, 1, 1, 0])
            finally:
                database.close()

            listed = list_equipment(path)["rows"]
            item = next(row for row in listed if row["item_id"] == "ANCIENT_MYTHIC")
            self.assertEqual(item["quality_id"], "ancient_mythic_red")
            self.assertEqual(item["quality_name"], "上古神话")
            self.assertTrue(item["is_ancient"])
            self.assertEqual(
                [stat["stat_type"] for stat in item["stats"]],
                ["ATK_PCT", "HP_PCT", "CRIT_DMG", "ATK_FLAT", "CRIT_RATE"],
            )
            self.assertEqual(
                [stat["is_unlocked"] for stat in item["stats"]],
                [1, 1, 1, 1, 0],
            )

    def test_optimizer_uses_actual_value_even_when_legacy_unlock_flag_is_inverted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._database_path(directory)
            save_equipment(path, self._payload())

            database = EquipmentDatabase(path)
            try:
                # Recreate the historical bad metadata seen in existing user DBs:
                # a visible value marked locked and an empty roll marked unlocked.
                database.connection.execute(
                    "UPDATE equipment_stats SET is_unlocked=0 WHERE item_id='ANCIENT_MYTHIC' AND stat_index=1"
                )
                database.connection.execute(
                    "UPDATE equipment_stats SET is_unlocked=1 WHERE item_id='ANCIENT_MYTHIC' AND stat_index=4"
                )
                database.connection.commit()
            finally:
                database.close()

            optimizer = OptimizerEquipmentDatabase(path, percentile=0.60)
            try:
                optimizer.initialize()
                item = optimizer.load_equipment(["ANCIENT_MYTHIC"])[0]
                stats = {stat.stat_index: stat.stat_value for stat in item.stats}
                self.assertEqual(item.level, 16)
                # Ancient Mythic uses the verified Mythic +16 main-stat curve.
                self.assertAlmostEqual(stats[0], 0.60)
                # Actual visible value wins over the stale is_unlocked=0 flag.
                self.assertAlmostEqual(stats[1], 0.19)
                # NULL remains locked and uses the explicit estimate override.
                self.assertAlmostEqual(stats[4], 0.15)
                report = {row["stat_index"]: row for row in optimizer.projection_reports["ANCIENT_MYTHIC"]["stats"]}
                self.assertEqual(report[1]["projection_source"], "actual")
                self.assertTrue(report[1]["is_unlocked"])
                self.assertEqual(report[4]["projection_source"], "override")
                self.assertFalse(report[4]["is_unlocked"])
            finally:
                optimizer.close()


if __name__ == "__main__":
    unittest.main()

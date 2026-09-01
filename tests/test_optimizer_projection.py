import tempfile
import unittest
from pathlib import Path

from equipment_db import EquipmentDatabase
from hero_core_service import recommend_hero_core
from optimizer_projection import EquipmentProjectionError, OptimizerEquipmentDatabase


class OptimizerProjectionTests(unittest.TestCase):
    def _database(self, directory: str) -> OptimizerEquipmentDatabase:
        # This legacy fixture intentionally has no set_tier_id. Keep its small
        # five-sample distribution explicit so the test continues to focus on
        # +16 projection semantics rather than the production >=10 tier rule.
        database = OptimizerEquipmentDatabase(
            Path(directory) / "equipment.db",
            percentile=0.60,
            min_samples_for_percentile=5,
        )
        database.initialize()
        database.connection.execute(
            "INSERT OR IGNORE INTO sets(set_id,set_name,required_pieces,slot_group,output_set) VALUES ('TEST_NONE','测试无套装',99,NULL,1)"
        )
        database.connection.commit()
        return database

    @staticmethod
    def _insert_item(
        database,
        *,
        item_id: str,
        slot: str,
        level: int,
        main_type: str,
        main_value: float,
        locked_stat: tuple[str, str] | None = None,
        quality_id: str = "mythic_red",
        available: int = 1,
    ) -> None:
        database.connection.execute(
            """INSERT INTO equipment(
                 item_id,slot,set_id,locked,available,slot_id,quality_id,
                 enhancement_level,item_locked
               ) VALUES (?,?,?,?,?,?,?,?,?)""",
            (item_id, slot, "TEST_NONE", 0, available, slot, quality_id, level, 0),
        )
        rows = [
            (item_id, 0, "main", main_type, main_value, 0, 1, None, None),
            (item_id, 1, "sub", "CRIT_RATE", 0.17, 0, 1, "gold", None),
            (item_id, 2, "sub", "ATK_SPEED", 32.0, 0, 1, "gold", None),
            (item_id, 3, "sub", "CRIT_DMG", 0.22, 0, 1, "gold", None),
            (item_id, 4, "sub", "HP_PCT", 0.14, 0, 1, "gold", None),
        ]
        if locked_stat is not None:
            stat_type, grade = locked_stat
            rows[1] = (item_id, 1, "sub", stat_type, None, 16, 0, grade, None)
        database.connection.executemany(
            """INSERT INTO equipment_stats(
                 item_id,stat_index,stat_source,stat_type,stat_value,
                 unlock_level,is_unlocked,roll_grade_id,estimate_override
               ) VALUES (?,?,?,?,?,?,?,?,?)""",
            rows,
        )
        database.connection.commit()

    def _insert_observations(self, database, stat_type: str, grade: str, values: list[float]) -> None:
        for index, value in enumerate(values):
            item_id = f"OBS_{stat_type}_{grade}_{index}"
            database.connection.execute(
                """INSERT INTO equipment(
                     item_id,slot,set_id,locked,available,slot_id,quality_id,
                     enhancement_level,item_locked
                   ) VALUES (?,?,?,?,?,?,?,?,?)""",
                (item_id, "bracelet", "TEST_NONE", 0, 0, "bracelet", "mythic_red", 16, 0),
            )
            database.connection.execute(
                """INSERT INTO sub_stat_observations(
                     item_id,stat_type,roll_grade_id,stat_value,data_source,observed_at
                   ) VALUES (?,?,?,?,?,datetime('now'))""",
                (item_id, stat_type, grade, value, "normalized_test"),
            )
        database.connection.commit()

    def test_underlevel_mythic_uses_plus16_main_cap_and_locked_p60(self):
        with tempfile.TemporaryDirectory() as directory:
            database = self._database(directory)
            try:
                self._insert_observations(database, "CRIT_RATE", "gold", [0.10, 0.20, 0.30, 0.40, 0.50])
                self._insert_item(
                    database,
                    item_id="TARGET",
                    slot="bracelet",
                    level=8,
                    main_type="ATK_PCT",
                    main_value=0.30,
                    locked_stat=("CRIT_RATE", "gold"),
                )

                item = database.load_equipment(["TARGET"])[0]
                stats = {stat.stat_index: stat.stat_value for stat in item.stats}
                self.assertEqual(item.level, 16)
                self.assertAlmostEqual(stats[0], 0.60)
                self.assertAlmostEqual(stats[1], 0.34)

                report = database.projection_reports["TARGET"]
                self.assertEqual(report["current_level"], 8)
                self.assertEqual(report["projected_level"], 16)
                self.assertEqual(report["main_stat_level_used"], 16)
                self.assertFalse(report["uses_current_main_fallback"])
                self.assertTrue(report["projection_complete"])
                self.assertEqual(report["stats"][0]["projection_source"], "main_stat_cap")
                self.assertEqual(report["stats"][1]["projection_source"], "empirical_unknown_tier_iqr_p60")

                stored = database.connection.execute(
                    "SELECT enhancement_level FROM equipment WHERE item_id='TARGET'"
                ).fetchone()[0]
                stored_main = database.connection.execute(
                    "SELECT stat_value FROM equipment_stats WHERE item_id='TARGET' AND stat_index=0"
                ).fetchone()[0]
                stored_locked = database.connection.execute(
                    "SELECT stat_value FROM equipment_stats WHERE item_id='TARGET' AND stat_index=1"
                ).fetchone()[0]
                self.assertEqual(stored, 8)
                self.assertAlmostEqual(stored_main, 0.30)
                self.assertIsNone(stored_locked)
            finally:
                database.close()

    def test_underlevel_item_without_known_main_cap_uses_current_main_value(self):
        with tempfile.TemporaryDirectory() as directory:
            database = self._database(directory)
            try:
                self._insert_item(
                    database,
                    item_id="UNKNOWN_CAP",
                    slot="bracelet",
                    level=8,
                    main_type="DEF_PCT",
                    main_value=0.20,
                )

                all_items = {item.item_id: item for item in database.load_equipment()}
                self.assertIn("UNKNOWN_CAP", all_items)
                main = next(stat for stat in all_items["UNKNOWN_CAP"].stats if stat.stat_index == 0)
                self.assertAlmostEqual(main.stat_value, 0.20)
                self.assertEqual(all_items["UNKNOWN_CAP"].level, 16)
                self.assertNotIn("UNKNOWN_CAP", database.projection_exclusions)

                report = database.projection_reports["UNKNOWN_CAP"]
                self.assertEqual(report["current_level"], 8)
                self.assertEqual(report["projected_level"], 16)
                self.assertEqual(report["main_stat_level_used"], 8)
                self.assertTrue(report["uses_current_main_fallback"])
                self.assertFalse(report["projection_complete"])
                self.assertEqual(
                    report["stats"][0]["projection_source"],
                    "current_level_main_fallback",
                )
                self.assertEqual(report["stats"][0]["value_level"], 8)
                self.assertTrue(report["warnings"])

                summary = database.projection_summary(["UNKNOWN_CAP"])
                self.assertEqual(summary["current_main_fallback_item_count"], 1)
                self.assertEqual(
                    summary["main_stat_fallback_policy"],
                    "use_current_observed_value_when_max_cap_unknown",
                )

                direct = database.load_equipment(["UNKNOWN_CAP"])[0]
                direct_main = next(stat for stat in direct.stats if stat.stat_index == 0)
                self.assertAlmostEqual(direct_main.stat_value, 0.20)

                stored = database.connection.execute(
                    "SELECT enhancement_level FROM equipment WHERE item_id='UNKNOWN_CAP'"
                ).fetchone()[0]
                stored_main = database.connection.execute(
                    "SELECT stat_value FROM equipment_stats WHERE item_id='UNKNOWN_CAP' AND stat_index=0"
                ).fetchone()[0]
                self.assertEqual(stored, 8)
                self.assertAlmostEqual(stored_main, 0.20)
            finally:
                database.close()

    def test_plus16_unknown_cap_keeps_observed_main_value(self):
        with tempfile.TemporaryDirectory() as directory:
            database = self._database(directory)
            try:
                self._insert_item(
                    database,
                    item_id="ALREADY_MAX",
                    slot="bracelet",
                    level=16,
                    main_type="DEF_PCT",
                    main_value=0.42,
                )
                item = database.load_equipment(["ALREADY_MAX"])[0]
                main = next(stat for stat in item.stats if stat.stat_index == 0)
                self.assertEqual(item.level, 16)
                self.assertAlmostEqual(main.stat_value, 0.42)
                self.assertEqual(
                    database.projection_reports["ALREADY_MAX"]["stats"][0]["projection_source"],
                    "actual_at_level_cap",
                )
                self.assertFalse(database.projection_reports["ALREADY_MAX"]["uses_current_main_fallback"])
            finally:
                database.close()

    def test_missing_main_value_still_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            database = self._database(directory)
            try:
                self._insert_item(
                    database,
                    item_id="NO_MAIN_VALUE",
                    slot="bracelet",
                    level=8,
                    main_type="DEF_PCT",
                    main_value=0.20,
                )
                database.connection.execute(
                    "UPDATE equipment_stats SET stat_value=NULL WHERE item_id='NO_MAIN_VALUE' AND stat_index=0"
                )
                database.connection.commit()
                self.assertNotIn("NO_MAIN_VALUE", {item.item_id for item in database.load_equipment()})
                self.assertIn("NO_MAIN_VALUE", database.projection_exclusions)
                with self.assertRaises(EquipmentProjectionError):
                    database.load_equipment(["NO_MAIN_VALUE"])
            finally:
                database.close()

    def test_hero_core_recommendation_scores_projected_plus16_items(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "equipment.db"
            database = EquipmentDatabase(path)
            try:
                database.initialize()
                database.connection.execute(
                    "INSERT OR IGNORE INTO sets(set_id,set_name,required_pieces,slot_group,output_set) VALUES ('TEST_NONE','测试无套装',99,NULL,1)"
                )
                database.connection.commit()
            finally:
                database.close()

            projected = OptimizerEquipmentDatabase(path)
            try:
                projected.initialize()
                self._insert_item(projected, item_id="W", slot="weapon", level=8, main_type="ATK_FLAT", main_value=400)
                self._insert_item(projected, item_id="A", slot="armor", level=8, main_type="HP_FLAT", main_value=1600)
                self._insert_item(projected, item_id="B", slot="bracelet", level=8, main_type="ATK_PCT", main_value=0.30)
                self._insert_item(projected, item_id="N", slot="necklace", level=8, main_type="ATK_PCT", main_value=0.30)
                self._insert_item(projected, item_id="R", slot="ring", level=8, main_type="ATK_PCT", main_value=0.30)
            finally:
                projected.close()

            result = recommend_hero_core(path, {
                "hero_core_id": "SUN_WUKONG",
                "top_k": 1,
                "candidate_per_slot": 1,
                "trials": 1,
                "screening_warmup": 0,
                "screening_measurement": 60,
                "warmup": 0,
                "measurement": 60,
                "target_def": 0,
            })
            self.assertEqual(result["equipment_projection"]["mode"], "max_enhancement_p60")
            self.assertEqual(result["equipment_projection"]["locked_substat_percentile"], 0.60)
            self.assertEqual(result["combinations_screened"], 1)
            build_projection = result["results"][0]["equipment_projection"]
            self.assertEqual(build_projection["mode"], "max_enhancement_p60")
            self.assertEqual(build_projection["locked_substat_percentile"], 0.60)
            self.assertEqual(len(build_projection["items"]), 5)
            self.assertTrue(all(item["projected_level"] == 16 for item in build_projection["items"]))
            weapon = next(item for item in build_projection["items"] if item["item_id"] == "W")
            self.assertAlmostEqual(weapon["stats"][0]["projected_value"], 960.0)


if __name__ == "__main__":
    unittest.main()

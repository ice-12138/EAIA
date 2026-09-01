import tempfile
import unittest
from pathlib import Path

from equipment_db import EquipmentDatabase
from rebuild_substat_learning import rebuild_substat_learning
from sub_stat_estimator import SubStatEstimator


class RebuildSubstatLearningTests(unittest.TestCase):
    def test_rebuild_clears_derived_state_and_preserves_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "equipment.db"
            database = EquipmentDatabase(path)
            try:
                database.initialize()
                connection = database.connection
                connection.execute(
                    """INSERT OR REPLACE INTO sets(
                         set_id,set_name,required_pieces,slot_group,output_set,set_tier_id
                       ) VALUES ('REBUILD_T1','重建测试T1',3,'right',1,'T1')"""
                )
                connection.execute(
                    """INSERT INTO equipment(
                         item_id,slot,set_id,tier,level,locked,available,
                         slot_id,quality_id,enhancement_level,item_locked
                       ) VALUES ('REBUILD_ITEM','bracelet','REBUILD_T1','mythic_red',16,0,1,
                                 'bracelet','mythic_red',16,0)"""
                )
                connection.execute(
                    """INSERT INTO equipment_stats(
                         item_id,stat_index,stat_source,stat_type,stat_value,
                         unlock_level,is_unlocked,roll_grade_id,estimate_override,value_confidence
                       ) VALUES ('REBUILD_ITEM',1,'sub','CRIT_RATE',0.20,0,1,NULL,NULL,1.0)"""
                )
                connection.commit()

                estimator = SubStatEstimator(connection)
                estimator.ensure_schema()
                connection.execute(
                    """UPDATE sub_stat_observations
                          SET stat_value=9.99,set_tier_id='T2'
                        WHERE item_id='REBUILD_ITEM' AND stat_type='CRIT_RATE'"""
                )
                connection.execute(
                    """INSERT OR REPLACE INTO sub_stat_learned_ranges(
                         stat_type,roll_grade_id,observed_min,observed_max,sample_count,
                         range_status,data_source,updated_at
                       ) VALUES ('CRIT_RATE','unknown',0.1,9.99,99,'provisional','stale','now')"""
                )
                connection.execute(
                    """INSERT INTO stat_observation_queue(
                         item_id,stat_type,roll_grade_id,stat_value,data_source,reason,created_at
                       ) VALUES ('REBUILD_ITEM','CRIT_RATE','unknown',9.99,'stale','old','now')"""
                )
                connection.commit()
                equipment_count = connection.execute("SELECT COUNT(*) FROM equipment").fetchone()[0]
            finally:
                database.close()

            report = rebuild_substat_learning(path)
            self.assertTrue(report["ok"])
            self.assertEqual(report["equipment_count"], equipment_count)
            self.assertGreaterEqual(report["cleared_rows"]["sub_stat_observations"], 1)
            self.assertEqual(report["rebuilt_observation_count"], 1)

            verify = EquipmentDatabase(path)
            try:
                verify.initialize()
                row = verify.connection.execute(
                    """SELECT stat_value,set_tier_id
                         FROM sub_stat_observations
                        WHERE item_id='REBUILD_ITEM' AND stat_type='CRIT_RATE'"""
                ).fetchone()
                self.assertIsNotNone(row)
                self.assertAlmostEqual(row["stat_value"], 0.20)
                self.assertEqual(row["set_tier_id"], "T1")
                self.assertEqual(
                    verify.connection.execute("SELECT COUNT(*) FROM sub_stat_learned_ranges").fetchone()[0],
                    0,
                )
                self.assertEqual(
                    verify.connection.execute("SELECT COUNT(*) FROM stat_observation_queue").fetchone()[0],
                    0,
                )
                self.assertEqual(
                    verify.connection.execute("SELECT COUNT(*) FROM equipment").fetchone()[0],
                    equipment_count,
                )
            finally:
                verify.close()


if __name__ == "__main__":
    unittest.main()

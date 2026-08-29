import tempfile
import unittest
from pathlib import Path

from equipment_models import EquipmentItem, EquipmentStat, Slot, StatType
from hero_core_service import _select_set_aware_candidates, _set_candidate_bonuses
from optimizer_projection import OptimizerEquipmentDatabase


def item(item_id: str, slot: str, set_id: str, atk_pct: float) -> EquipmentItem:
    return EquipmentItem(
        item_id=item_id,
        slot=Slot(slot),
        set_id=set_id,
        stats=(EquipmentStat(item_id, "sub", StatType.ATK_PCT, atk_pct, 1),),
    )


class MixedT1T2CandidateReachabilityTests(unittest.TestCase):
    def test_native_t2_and_source_t1_share_reachable_t2_bonus(self):
        with tempfile.TemporaryDirectory() as directory:
            database = OptimizerEquipmentDatabase(Path(directory) / "equipment.db")
            try:
                database.initialize()
                evolution = database.connection.execute(
                    "SELECT from_set_id,to_set_id FROM set_evolutions WHERE from_set_id='set_insight'"
                ).fetchone()
                self.assertIsNotNone(evolution)
                source = str(evolution["from_set_id"])
                target = str(evolution["to_set_id"])

                items = [
                    item("B_T1", "bracelet", source, 0.06),
                    item("B_T2", "bracelet", target, 0.20),
                    item("N_T1", "necklace", source, 0.06),
                    item("R_T1", "ring", source, 0.06),
                ]
                bonuses = _set_candidate_bonuses(database, items)
                self.assertGreater(bonuses[source], 0.0)
                self.assertGreater(bonuses[target], 0.0)
                self.assertAlmostEqual(bonuses[source], bonuses[target])

                by_slot = {
                    "weapon": [],
                    "armor": [],
                    "bracelet": [items[0], items[1]],
                    "necklace": [items[2]],
                    "ring": [items[3]],
                }
                candidates, _ = _select_set_aware_candidates(database, by_slot, 1)
                self.assertEqual(candidates["bracelet"][0].item_id, "B_T2")
            finally:
                database.close()


if __name__ == "__main__":
    unittest.main()

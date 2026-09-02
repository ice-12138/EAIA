import tempfile
import unittest
from pathlib import Path

from equipment_models import EquipmentItem, EquipmentStat, Slot, StatType
from equipment_recommendation_profile import resolve_recommendation_profile
from hero_core_service import _select_group_build_candidates, _select_set_aware_candidates
from optimizer_projection import OptimizerEquipmentDatabase


class CompleteSetPruningRegressionTests(unittest.TestCase):
    @staticmethod
    def _item(item_id: str, slot: str, set_id: str, atk_pct: float) -> EquipmentItem:
        return EquipmentItem(
            item_id=item_id,
            slot=Slot(slot),
            set_id=set_id,
            stats=(
                EquipmentStat(
                    item_id,
                    "sub",
                    StatType.ATK_PCT,
                    atk_pct,
                    1,
                ),
            ),
        )

    def test_zero_score_complete_right_set_survives_both_pruning_stages(self):
        """A structural full set must not disappear before sets_only validation.

        Complete-set-only filtering is applied after per-slot and group pruning.
        Therefore a physically complete right-side set must be retained even if
        its cheap profile effect score is zero and stronger loose pieces occupy
        the normal top-N candidate positions.
        """
        with tempfile.TemporaryDirectory() as directory:
            database = OptimizerEquipmentDatabase(Path(directory) / "equipment.db")
            try:
                database.initialize()
                database.connection.execute(
                    "INSERT OR REPLACE INTO sets(set_id,set_name,required_pieces,slot_group,output_set) "
                    "VALUES ('TEST_ZERO_RIGHT','零分完整右套',3,'right',1)"
                )
                database.connection.execute(
                    "INSERT OR REPLACE INTO sets(set_id,set_name,required_pieces,slot_group,output_set) "
                    "VALUES ('TEST_RAW_RIGHT','高分散件',99,'right',1)"
                )
                database.connection.commit()

                by_slot = {
                    "weapon": [],
                    "armor": [],
                    "bracelet": [
                        self._item("B_RAW", "bracelet", "TEST_RAW_RIGHT", 0.50),
                        self._item("B_SET", "bracelet", "TEST_ZERO_RIGHT", 0.01),
                    ],
                    "necklace": [
                        self._item("N_RAW", "necklace", "TEST_RAW_RIGHT", 0.50),
                        self._item("N_SET", "necklace", "TEST_ZERO_RIGHT", 0.01),
                    ],
                    "ring": [
                        self._item("R_RAW", "ring", "TEST_RAW_RIGHT", 0.50),
                        self._item("R_SET", "ring", "TEST_ZERO_RIGHT", 0.01),
                    ],
                }
                profile = resolve_recommendation_profile(
                    {"recommendation_profile": {"category": "output"}}
                )

                candidates, _ = _select_set_aware_candidates(
                    database,
                    by_slot,
                    candidate_per_slot=1,
                    recommendation_profile=profile,
                )
                for slot in ("bracelet", "necklace", "ring"):
                    self.assertIn(
                        "TEST_ZERO_RIGHT",
                        {item.set_id for item in candidates[slot]},
                        f"complete set representative was pruned from {slot}",
                    )

                definitions = database.load_sets()
                builds, _ = _select_group_build_candidates(
                    candidates,
                    ("bracelet", "necklace", "ring"),
                    keep=1,
                    definitions=definitions,
                    effect_scores={},
                    evolutions={},
                    set_names={"TEST_ZERO_RIGHT": "零分完整右套"},
                    recommendation_profile=profile,
                )
                self.assertTrue(
                    any({item.set_id for item in build} == {"TEST_ZERO_RIGHT"} for build in builds),
                    "complete right-side set was pruned before sets_only validation",
                )
            finally:
                database.close()


if __name__ == "__main__":
    unittest.main()

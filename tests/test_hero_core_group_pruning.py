import unittest

from equipment_models import EquipmentItem, EquipmentStat, Slot, StatType
from hero_core_service import _select_group_build_candidates


def item(item_id: str, slot: str, atk_pct: float) -> EquipmentItem:
    return EquipmentItem(
        item_id=item_id,
        slot=Slot(slot),
        set_id="TEST_NONE",
        stats=(EquipmentStat(item_id, "sub", StatType.ATK_PCT, atk_pct, 1),),
    )


class HeroCoreGroupPruningTests(unittest.TestCase):
    def test_default_left_right_limits_reduce_five_per_slot_from_3125_to_128(self):
        candidates = {
            slot: [item(f"{slot}_{index}", slot, 0.01 * index) for index in range(5)]
            for slot in ("weapon", "armor", "bracelet", "necklace", "ring")
        }
        common = {
            "definitions": {},
            "effect_scores": {},
            "evolutions": {},
            "set_names": {},
        }
        left, left_raw = _select_group_build_candidates(
            candidates, ("weapon", "armor"), 8, **common
        )
        right, right_raw = _select_group_build_candidates(
            candidates, ("bracelet", "necklace", "ring"), 16, **common
        )
        self.assertEqual(left_raw, 25)
        self.assertEqual(right_raw, 125)
        self.assertEqual(len(left), 8)
        self.assertEqual(len(right), 16)
        self.assertEqual(len(left) * len(right), 128)
        self.assertLess(len(left) * len(right), 5 ** 5)

    def test_group_pruning_does_not_expand_small_candidate_spaces(self):
        candidates = {
            slot: [item(f"{slot}_{index}", slot, 0.01 * index) for index in range(2)]
            for slot in ("weapon", "armor", "bracelet", "necklace", "ring")
        }
        common = {
            "definitions": {},
            "effect_scores": {},
            "evolutions": {},
            "set_names": {},
        }
        left, left_raw = _select_group_build_candidates(
            candidates, ("weapon", "armor"), 8, **common
        )
        right, right_raw = _select_group_build_candidates(
            candidates, ("bracelet", "necklace", "ring"), 16, **common
        )
        self.assertEqual((left_raw, len(left)), (4, 4))
        self.assertEqual((right_raw, len(right)), (8, 8))
        self.assertEqual(len(left) * len(right), 32)


if __name__ == "__main__":
    unittest.main()

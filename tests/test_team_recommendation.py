import unittest

from equipment_models import EquipmentItem, SetDefinition, Slot
from hero_core_service import _is_complete_set_variant, _solve_total_team


def item(item_id: str, slot: Slot, set_id: str) -> EquipmentItem:
    return EquipmentItem(item_id=item_id, slot=slot, set_id=set_id)


class TeamRecommendationTests(unittest.TestCase):
    def test_complete_set_constraint_requires_left_two_and_right_three(self):
        definitions = {
            "LEFT": SetDefinition("LEFT", "左套", 2, "left", True),
            "RIGHT": SetDefinition("RIGHT", "右套", 3, "right", True),
            "OTHER": SetDefinition("OTHER", "其他", 3, "right", True),
        }
        valid = (
            item("w", Slot.WEAPON, "LEFT"),
            item("a", Slot.ARMOR, "LEFT"),
            item("b", Slot.BRACELET, "RIGHT"),
            item("n", Slot.NECKLACE, "RIGHT"),
            item("r", Slot.RING, "RIGHT"),
        )
        self.assertTrue(_is_complete_set_variant(valid, definitions))

        broken = valid[:-1] + (item("r2", Slot.RING, "OTHER"),)
        self.assertFalse(_is_complete_set_variant(broken, definitions))

    def test_total_team_solver_sacrifices_local_best_for_global_score(self):
        candidates = {
            "A": [
                {"role_score": 100.0, "item_ids": ["shared", "a1", "a2", "a3", "a4"]},
                {"role_score": 90.0, "item_ids": ["a0", "a1", "a2", "a3", "a4"]},
            ],
            "B": [
                {"role_score": 95.0, "item_ids": ["shared", "b1", "b2", "b3", "b4"]},
                {"role_score": 30.0, "item_ids": ["b0", "b1", "b2", "b3", "b4"]},
            ],
        }
        selected = _solve_total_team(["A", "B"], candidates)
        self.assertEqual(selected["A"]["role_score"], 90.0)
        self.assertEqual(selected["B"]["role_score"], 95.0)
        self.assertFalse(set(selected["A"]["item_ids"]) & set(selected["B"]["item_ids"]))


if __name__ == "__main__":
    unittest.main()

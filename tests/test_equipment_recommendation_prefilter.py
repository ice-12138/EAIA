import tempfile
import unittest
from pathlib import Path

from equipment_db import EquipmentDatabase
from equipment_models import EquipmentItem, EquipmentStat, Slot, StatType
from equipment_recommendation_prefilter import (
    RecommendationCategory,
    classify_recommendation_category,
    prefilter_equipment,
)


def item(item_id, slot, set_id, substats):
    return EquipmentItem(
        item_id=item_id,
        slot=Slot(slot),
        set_id=set_id,
        stats=tuple(
            EquipmentStat(item_id, "sub", stat_type, value, index + 1)
            for index, (stat_type, value) in enumerate(substats)
        ),
    )


class EquipmentRecommendationPrefilterTests(unittest.TestCase):
    def test_role_classifier_and_reserved_interfaces(self):
        self.assertEqual(classify_recommendation_category({"hero": {"role": "战士"}}), RecommendationCategory.OUTPUT)
        self.assertEqual(classify_recommendation_category({"hero": {"role": "守护者"}}), RecommendationCategory.TANK)
        self.assertEqual(classify_recommendation_category({"hero": {"role": "医师"}}), RecommendationCategory.HEALING)
        self.assertEqual(classify_recommendation_category({"hero": {"role": "战术大师"}}), RecommendationCategory.SUPPORT)
        self.assertEqual(
            classify_recommendation_category({"recommendation_profile": {"category": "healing"}, "hero": {"role": "战士"}}),
            RecommendationCategory.HEALING,
        )

    def test_output_policy_keeps_only_output_sets_with_two_output_substats(self):
        with tempfile.TemporaryDirectory() as directory:
            database = EquipmentDatabase(Path(directory) / "equipment.db")
            try:
                database.initialize()
                items = [
                    item("GOOD", "weapon", "set_calamity", [(StatType.ATK_PCT, 0.1), (StatType.CRIT_RATE, 0.1)]),
                    item("DEF_SET", "armor", "set_life_force", [(StatType.ATK_PCT, 0.2), (StatType.CRIT_DMG, 0.2)]),
                    item("ONE_SUB", "bracelet", "set_insight", [(StatType.ATK_PCT, 0.2), (StatType.HP_PCT, 0.2)]),
                ]
                kept, report = prefilter_equipment(database, {"hero": {"role": "战士"}}, items)
                self.assertEqual([row.item_id for row in kept], ["GOOD"])
                self.assertEqual(report["category"], "output")
                self.assertEqual(report["min_relevant_substats"], 2)
                self.assertEqual(report["default_min_relevant_substats"], 2)
                self.assertIsNone(report["requested_min_relevant_substats"])
                self.assertEqual(report["removed_by_reason"]["non_output_set"], 1)
                self.assertEqual(report["removed_by_reason"]["insufficient_output_substats"], 1)
            finally:
                database.close()

    def test_output_threshold_can_be_overridden_per_request(self):
        with tempfile.TemporaryDirectory() as directory:
            database = EquipmentDatabase(Path(directory) / "equipment.db")
            try:
                database.initialize()
                items = [
                    item("ONE", "weapon", "set_calamity", [(StatType.ATK_PCT, 0.1), (StatType.HP_PCT, 0.1)]),
                    item("TWO", "armor", "set_calamity", [(StatType.ATK_PCT, 0.1), (StatType.CRIT_RATE, 0.1)]),
                    item("THREE", "bracelet", "set_insight", [(StatType.ATK_PCT, 0.1), (StatType.CRIT_RATE, 0.1), (StatType.CRIT_DMG, 0.1)]),
                ]
                kept_one, report_one = prefilter_equipment(
                    database, {"hero": {"role": "战士"}}, items, min_relevant_substats=1
                )
                self.assertEqual({row.item_id for row in kept_one}, {"ONE", "TWO", "THREE"})
                self.assertEqual(report_one["requested_min_relevant_substats"], 1)
                self.assertEqual(report_one["min_relevant_substats"], 1)

                kept_three, report_three = prefilter_equipment(
                    database, {"hero": {"role": "战士"}}, items, min_relevant_substats=3
                )
                self.assertEqual([row.item_id for row in kept_three], ["THREE"])
                self.assertEqual(report_three["requested_min_relevant_substats"], 3)
                self.assertEqual(report_three["min_relevant_substats"], 3)

                kept_four, report_four = prefilter_equipment(
                    database, {"hero": {"role": "战士"}}, items, min_relevant_substats=99
                )
                self.assertEqual(kept_four, [])
                self.assertEqual(report_four["requested_min_relevant_substats"], 4)
                self.assertEqual(report_four["min_relevant_substats"], 4)
            finally:
                database.close()

    def test_tank_policy_is_reserved_passthrough_for_now(self):
        with tempfile.TemporaryDirectory() as directory:
            database = EquipmentDatabase(Path(directory) / "equipment.db")
            try:
                database.initialize()
                items = [item("A", "weapon", "set_calamity", [(StatType.ATK_PCT, 0.1)])]
                kept, report = prefilter_equipment(
                    database,
                    {"hero": {"role": "守护者"}},
                    items,
                    min_relevant_substats=4,
                )
                self.assertEqual(kept, items)
                self.assertEqual(report["category"], "tank")
                self.assertFalse(report["policy_implemented"])
                self.assertEqual(report["strategy"], "reserved_passthrough")
                self.assertEqual(report["requested_min_relevant_substats"], 4)
                self.assertIsNone(report["min_relevant_substats"])
            finally:
                database.close()


if __name__ == "__main__":
    unittest.main()

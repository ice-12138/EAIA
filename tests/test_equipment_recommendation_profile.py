import tempfile
import unittest
from pathlib import Path

from equipment_db import EquipmentDatabase
from equipment_models import EquipmentItem, EquipmentStat, Slot, StatType
from equipment_recommendation_profile import (
    evaluate_role_build,
    item_potential,
    resolve_recommendation_profile,
)


def build_item(item_id, slot, set_id, stats):
    return EquipmentItem(
        item_id=item_id,
        slot=Slot(slot),
        set_id=set_id,
        stats=tuple(
            EquipmentStat(item_id, "sub", stat_type, value, index + 1)
            for index, (stat_type, value) in enumerate(stats)
        ),
    )


class RecommendationProfileTests(unittest.TestCase):
    def test_tank_defaults_follow_primary_scaling_stat(self):
        hp = resolve_recommendation_profile({
            "hero": {"role": "守护者"},
            "recommendation_profile": {"category": "tank", "primary_scaling_stat": "HP"},
        })
        defense = resolve_recommendation_profile({
            "hero": {"role": "守护者"},
            "recommendation_profile": {"category": "tank", "primary_scaling_stat": "DEF"},
        })
        self.assertEqual(hp["archetype"], "hp")
        self.assertEqual(defense["archetype"], "defense")
        self.assertGreater(hp["stat_weights"]["HP_PCT"], hp["stat_weights"]["DEF_PCT"])
        self.assertGreater(defense["stat_weights"]["DEF_PCT"], defense["stat_weights"]["HP_PCT"])

    def test_healer_defaults_follow_attack_or_hp_scaling(self):
        attack = resolve_recommendation_profile({
            "hero": {"role": "医师"},
            "recommendation_profile": {"category": "healing", "primary_scaling_stat": "ATK"},
        })
        hp = resolve_recommendation_profile({
            "hero": {"role": "医师"},
            "recommendation_profile": {"category": "healing", "primary_scaling_stat": "HP"},
        })
        self.assertEqual(attack["archetype"], "attack")
        self.assertEqual(hp["archetype"], "hp")
        self.assertGreater(attack["stat_weights"]["ATK_PCT"], attack["stat_weights"].get("HP_PCT", 0))
        self.assertGreater(hp["stat_weights"]["HP_PCT"], hp["stat_weights"].get("ATK_PCT", 0))

    def test_item_potential_changes_with_tank_archetype(self):
        hp_item = build_item("HP", "weapon", "X", [(StatType.HP_PCT, 0.20)])
        def_item = build_item("DEF", "weapon", "X", [(StatType.DEF_PCT, 0.20)])
        hp_profile = resolve_recommendation_profile({
            "recommendation_profile": {"category": "tank", "primary_scaling_stat": "HP"}
        })
        def_profile = resolve_recommendation_profile({
            "recommendation_profile": {"category": "tank", "primary_scaling_stat": "DEF"}
        })
        self.assertGreater(item_potential(hp_item, hp_profile), item_potential(def_item, hp_profile))
        self.assertGreater(item_potential(def_item, def_profile), item_potential(hp_item, def_profile))

    def test_role_evaluation_uses_profile_objective(self):
        with tempfile.TemporaryDirectory() as directory:
            database = EquipmentDatabase(Path(directory) / "equipment.db")
            try:
                database.initialize()
                database.connection.execute(
                    """INSERT OR REPLACE INTO sets
                       (set_id,set_name,required_pieces,slot_group,output_set,category_id,active)
                       VALUES ('TEST_DEF','测试防御',99,NULL,0,'defense',1)"""
                )
                rows = [
                    ("HP", "weapon", "HP_PCT", 0.20),
                    ("DEF", "armor", "DEF_PCT", 0.20),
                ]
                for item_id, slot, stat_type, value in rows:
                    database.connection.execute(
                        "INSERT INTO equipment(item_id,slot,set_id,locked,available) VALUES (?,?,?,?,?)",
                        (item_id, slot, "TEST_DEF", 0, 1),
                    )
                    database.connection.execute(
                        """INSERT INTO equipment_stats
                           (item_id,stat_index,stat_source,stat_type,stat_value,unlock_level,is_unlocked,value_confidence)
                           VALUES (?,?,?,?,?,?,?,?)""",
                        (item_id, 1, "sub", stat_type, value, 0, 1, 1.0),
                    )
                database.connection.commit()
                core = {
                    "hero": {
                        "role": "守护者",
                        "base_stats": {
                            "atk": 1000.0, "hp": 10000.0, "defense": 1000.0,
                            "crit_rate": 0.0, "crit_dmg": 1.5, "attack_interval": 1.0,
                        },
                    },
                }
                hp_profile = resolve_recommendation_profile({
                    **core,
                    "recommendation_profile": {
                        "category": "tank",
                        "primary_scaling_stat": "HP",
                        "objective_weights": {"hp_gain": 1.0},
                    },
                })
                def_profile = resolve_recommendation_profile({
                    **core,
                    "recommendation_profile": {
                        "category": "tank",
                        "primary_scaling_stat": "DEF",
                        "objective_weights": {"defense_gain": 1.0},
                    },
                })
                hp_eval = evaluate_role_build(database, core, ["HP"], hp_profile)
                def_eval = evaluate_role_build(database, core, ["DEF"], def_profile)
                self.assertAlmostEqual(hp_eval["role_metrics"]["hp_gain"], 0.20)
                self.assertAlmostEqual(def_eval["role_metrics"]["defense_gain"], 0.20)
                self.assertAlmostEqual(hp_eval["role_score"], 0.20)
                self.assertAlmostEqual(def_eval["role_score"], 0.20)
            finally:
                database.close()


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest
from pathlib import Path

from equipment_models import EffectType
from equipment_set_variants import load_hero_core_set_effects, load_optimizer_set_effects
from optimizer_projection import OptimizerEquipmentDatabase


class HeroCoreSetCatalogTests(unittest.TestCase):
    def test_current_catalog_exposes_insight_extra_and_fatality_penetration(self):
        with tempfile.TemporaryDirectory() as directory:
            db = OptimizerEquipmentDatabase(Path(directory) / "equipment.db")
            try:
                db.initialize()
                for loader in (load_optimizer_set_effects, load_hero_core_set_effects, lambda database: database.load_set_effects()):
                    by_id = {effect.effect_id: effect for effect in loader(db)}
                    self.assertIn("eff_insight_extra", by_id)
                    insight = by_id["eff_insight_extra"]
                    self.assertEqual(insight.effect_type, EffectType.EXTRA_DAMAGE)
                    self.assertEqual(insight.trigger, "on_basic_attack_damage")
                    self.assertFalse(insight.requires_dot)
                    self.assertIsNone(insight.condition, repr(insight))
                    self.assertEqual(insight.applies_to, "single_target_basic")
                    self.assertEqual(insight.proc_chance, 1.0)

                    self.assertIn("eff_fatality_pen", by_id)
                    fatality = by_id["eff_fatality_pen"]
                    self.assertEqual(fatality.effect_type, EffectType.PENETRATION)
                    self.assertIsNone(fatality.condition, repr(fatality))
                    self.assertEqual(fatality.trigger, "always", repr(fatality))
                    self.assertFalse(fatality.requires_dot)
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()

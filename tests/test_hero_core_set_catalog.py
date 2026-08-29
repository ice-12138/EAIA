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
                    self.assertEqual(by_id["eff_insight_extra"].effect_type, EffectType.EXTRA_DAMAGE)
                    self.assertEqual(by_id["eff_insight_extra"].trigger, "on_basic_attack_damage")
                    self.assertFalse(by_id["eff_insight_extra"].requires_dot)
                    self.assertIn("eff_fatality_pen", by_id)
                    self.assertEqual(by_id["eff_fatality_pen"].effect_type, EffectType.PENETRATION)
                    self.assertEqual(by_id["eff_fatality_pen"].trigger, "always")
                    self.assertFalse(by_id["eff_fatality_pen"].requires_dot)
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()

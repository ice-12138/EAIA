import unittest

from validate_random_builds import validate_random_builds


class RandomBuildValidationTests(unittest.TestCase):
    def test_random_equipment_search_matches_independent_exhaustive_ranking_for_all_official_basics(self):
        result = validate_random_builds(seed=20260825, items_per_slot=3, top_k=5)
        self.assertEqual(result["combinations"], 243)
        self.assertGreaterEqual(result["official_validation_heroes"], 7)
        self.assertEqual(
            result["total_scored_combinations"],
            result["official_validation_heroes"] * 243,
        )
        self.assertTrue(result["top_k_exact_match"], result)
        self.assertTrue(result["aoe_target_scaling_ok"], result)
        self.assertTrue(all(result["monotonic_checks"].values()), result)
        self.assertTrue(all(row["all_checks_passed"] for row in result["validated_heroes"]), result)
        self.assertFalse(result["attack_speed_formula_calibrated"])
        self.assertTrue(result["all_checks_passed"], result)


if __name__ == "__main__":
    unittest.main()

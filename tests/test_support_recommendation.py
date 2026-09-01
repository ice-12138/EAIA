import unittest
from types import SimpleNamespace
from unittest.mock import patch

from support_recommendation import (
    AUTO_UTILITY,
    MANUAL_PRIORITY,
    evaluate_support_build,
    normalize_support_recommendation_mode,
    resolve_support_profile,
    support_uses_simulation,
)


BASE_PROFILE = {
    "category": "support",
    "archetype": "inspiration",
    "primary_scaling_stat": "ATK",
    "stat_weights": {"RAGE_REGEN": 1.0, "ATK_SPEED": 0.45, "ATK_PCT": 0.15},
    "objective_weights": {"rage_regen": 1.0, "attack_speed": 0.35, "attack_gain": 0.15},
    "effect_weights": {},
    "stat_normalizers": {"ATK_PCT": 1.0, "ATK_FLAT": 1 / 900, "RAGE_REGEN": 1.0, "ATK_SPEED": 0.01},
}

PANEL_PROXY = {
    "role_score": 0.5,
    "role_metrics": {"set_utility": 0.1},
    "role_contributions": {"set_utility": 0.1},
    "evaluation_mode": "recommendation_profile_panel_proxy",
    "recommendation_profile": BASE_PROFILE,
    "panel": {"atk": 1000.0, "hp": 10000.0, "defense": 1000.0, "atk_speed": 100.0, "rage_regen": 0.5},
    "active_sets": [],
}


class FakeSimulator:
    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs

    def run(self):
        return SimpleNamespace(
            panel={"atk": 1000.0, "hp": 10000.0, "defense": 1000.0, "atk_speed": 100.0, "rage_regen": 0.5},
            event_counts={"ultimate_cast": 2},
        )


class SupportRecommendationTests(unittest.TestCase):
    def test_mode_aliases_are_normalized(self):
        self.assertEqual(normalize_support_recommendation_mode("manual_burst"), MANUAL_PRIORITY)
        self.assertEqual(normalize_support_recommendation_mode("automatic"), AUTO_UTILITY)
        with self.assertRaises(ValueError):
            normalize_support_recommendation_mode("unknown")

    def test_manual_mode_uses_hero_specific_stat_priority(self):
        core = {
            "recommendation_profile": {
                "category": "support",
                "primary_scaling_stat": "ATK",
                "support_modes": {
                    "manual_priority": {
                        "stat_priority": ["ATK_PCT", "ATK_FLAT", "RAGE_REGEN", "ATK_SPEED", "HP_PCT"],
                        "ignored_stats": ["ATK_SPEED"],
                        "set_utility_weight": 0.2,
                    }
                },
            }
        }
        profile = resolve_support_profile(core, BASE_PROFILE, MANUAL_PRIORITY)
        self.assertEqual(profile["support_recommendation_mode"], MANUAL_PRIORITY)
        self.assertEqual(profile["stat_priority"][:3], ["ATK_PCT", "ATK_FLAT", "RAGE_REGEN"])
        self.assertNotIn("ATK_SPEED", profile["stat_priority"])
        self.assertNotIn("ATK_SPEED", profile["stat_weights"])
        self.assertGreater(profile["objective_weights"]["attack_gain"], profile["objective_weights"]["rage_regen"])
        self.assertAlmostEqual(profile["objective_weights"]["set_utility"], 0.2)

    def test_auto_mode_uses_timeline_activation_count(self):
        core = {
            "recommendation_profile": {
                "category": "support",
                "support_modes": {
                    "auto_utility": {
                        "utility_model": {
                            "activation_event": "ultimate_cast",
                            "source_stat": "atk",
                            "buff_ratio": 0.5,
                            "duration_seconds": 20,
                            "target_count": 3,
                        }
                    }
                },
            }
        }
        profile = resolve_support_profile(core, BASE_PROFILE, AUTO_UTILITY)
        self.assertTrue(support_uses_simulation(profile))
        with patch("support_recommendation.evaluate_role_build", return_value=dict(PANEL_PROXY)), patch(
            "support_recommendation.HeroCoreSimulator", FakeSimulator
        ):
            result = evaluate_support_build(
                object(), core, ["a", "b", "c", "d", "e"], profile,
                target={"defense": 0}, policy="auto", trials=2, seed=1, seconds=60,
            )
        # 1000 ATK * 50% Inspiration * (2*20/60 coverage) * 3 targets = 1000 utility.
        self.assertAlmostEqual(result["role_score"], 1000.0)
        self.assertAlmostEqual(result["role_metrics"]["coverage"], 2 / 3)
        self.assertAlmostEqual(result["role_metrics"]["activation_count"], 2.0)
        self.assertEqual(result["evaluation_mode"], "support_auto_utility_simulation")
        self.assertFalse(result["auto_utility_fallback"])

    def test_auto_mode_without_utility_model_is_explicit_fallback(self):
        core = {"recommendation_profile": {"category": "support"}}
        profile = resolve_support_profile(core, BASE_PROFILE, AUTO_UTILITY)
        self.assertFalse(support_uses_simulation(profile))
        with patch("support_recommendation.evaluate_role_build", return_value=dict(PANEL_PROXY)):
            result = evaluate_support_build(object(), core, [], profile)
        self.assertEqual(result["evaluation_mode"], "support_auto_panel_proxy_fallback")
        self.assertTrue(result["auto_utility_fallback"])
        self.assertIn("fallback_reason", result)


if __name__ == "__main__":
    unittest.main()

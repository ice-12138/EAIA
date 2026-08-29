"""Recommendation-profile driven equipment scoring for HeroCore roles."""

from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from typing import Any

from equipment_models import EquipmentItem
from equipment_rules import GameRules


# ``stat_weights`` express relative importance only. Unit conversion belongs in
# ``_STAT_NORMALIZERS``. Keeping the two concerns separate prevents flat ATK and
# attack-speed points from being normalized twice during candidate pruning.
_OUTPUT_STAT_WEIGHTS = {
    "ATK_PCT": 8.0,
    "ATK_FLAT": 1.0,
    "CRIT_RATE": 7.0,
    "CRIT_DMG": 3.2,
    "ATK_SPEED": 100.0 / 85.0,
    "RAGE_REGEN": 2.2,
}
_STAT_NORMALIZERS = {
    "ATK_FLAT": 1.0 / 900.0,
    "ATK_PCT": 1.0,
    "HP_FLAT": 1.0 / 10000.0,
    "HP_PCT": 1.0,
    "DEF_FLAT": 1.0 / 1000.0,
    "DEF_PCT": 1.0,
    "CRIT_RATE": 1.0,
    "CRIT_DMG": 1.0,
    "ATK_SPEED": 1.0 / 100.0,
    "RAGE_REGEN": 1.0,
    "HEALING_EFFECT": 1.0,
}
_PANEL_EFFECT_TYPES = {
    "ATK_FLAT", "ATK_PCT", "HP_FLAT", "HP_PCT", "DEF_FLAT", "DEF_PCT",
    "CRIT_RATE", "CRIT_DMG", "ATK_SPEED", "RAGE_REGEN", "HEALING_EFFECT",
}


def _upper_mapping(value: Any) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, float] = {}
    for key, raw in value.items():
        try:
            result[str(key).upper()] = float(raw)
        except (TypeError, ValueError):
            continue
    return result


def _objective_mapping(value: Any) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, float] = {}
    for key, raw in value.items():
        try:
            result[str(key).strip().lower()] = float(raw)
        except (TypeError, ValueError):
            continue
    return result


def _category_from_core(core: dict[str, Any]) -> str:
    explicit = (
        (core.get("recommendation_profile") or {}).get("category")
        or (core.get("hero") or {}).get("equipment_category")
    )
    if explicit:
        value = str(explicit).strip().lower()
        if value in {"output", "tank", "healing", "support"}:
            return value
        raise ValueError(f"unsupported recommendation category: {explicit}")

    role = str((core.get("hero") or {}).get("role") or "").strip().casefold()
    if role in {"守护者", "defender", "guardian", "tank"}:
        return "tank"
    if role in {"医师", "healer", "medic"}:
        return "healing"
    if role in {"战术大师", "support", "tactician", "tactical master"}:
        return "support"
    return "output"


def _default_profile(category: str, primary: str | None, archetype: str | None) -> dict[str, Any]:
    primary = (primary or "").upper()
    archetype = (archetype or "").lower()

    if category == "output":
        return {
            "category": "output",
            "archetype": archetype or "damage",
            "primary_scaling_stat": primary or "ATK",
            "set_categories": ["output"],
            "stat_category": "output",
            "min_relevance_weight": 0.60,
            "min_relevant_substats": 2,
            "stat_weights": dict(_OUTPUT_STAT_WEIGHTS),
            "objective_weights": {"damage_60s": 1.0},
            "effect_weights": {},
        }

    if category == "tank":
        defense_first = primary == "DEF" or archetype in {"defense", "armor", "defense_tank", "armor_tank"}
        if defense_first:
            stat_weights = {
                "DEF_PCT": 1.0, "DEF_FLAT": 0.60, "HP_PCT": 0.45, "HP_FLAT": 0.25,
                "RAGE_REGEN": 0.12,
            }
            objective = {
                "defense_gain": 1.0, "hp_gain": 0.45, "rage_regen": 0.12, "set_utility": 0.35,
            }
            resolved_archetype = "defense"
            primary = primary or "DEF"
        else:
            stat_weights = {
                "HP_PCT": 1.0, "HP_FLAT": 0.60, "DEF_PCT": 0.40, "DEF_FLAT": 0.25,
                "RAGE_REGEN": 0.12,
            }
            objective = {
                "hp_gain": 1.0, "defense_gain": 0.40, "rage_regen": 0.12, "set_utility": 0.35,
            }
            resolved_archetype = "hp"
            primary = primary or "HP"
        return {
            "category": "tank",
            "archetype": archetype or resolved_archetype,
            "primary_scaling_stat": primary,
            "set_categories": ["defense", "buff"],
            "stat_category": "defense",
            "min_relevance_weight": 0.60,
            "min_relevant_substats": 2,
            "stat_weights": stat_weights,
            "objective_weights": objective,
            "effect_weights": {},
        }

    if category == "healing":
        hp_first = primary == "HP" or archetype in {"hp", "life", "hp_healer", "life_healer"}
        if hp_first:
            stat_weights = {
                "HP_PCT": 1.0, "HP_FLAT": 0.60, "HEALING_EFFECT": 0.90,
                "RAGE_REGEN": 0.35, "ATK_SPEED": 0.25, "DEF_PCT": 0.10,
            }
            objective = {
                "hp_gain": 1.0, "healing_effect": 0.90, "rage_regen": 0.35,
                "attack_speed": 0.20, "defense_gain": 0.10, "set_utility": 0.40,
            }
            resolved_archetype = "hp"
            primary = primary or "HP"
            sets = ["healing", "buff", "defense"]
        else:
            stat_weights = {
                "ATK_PCT": 1.0, "ATK_FLAT": 0.60, "HEALING_EFFECT": 0.90,
                "RAGE_REGEN": 0.35, "ATK_SPEED": 0.25, "HP_PCT": 0.10,
            }
            objective = {
                "attack_gain": 1.0, "healing_effect": 0.90, "rage_regen": 0.35,
                "attack_speed": 0.20, "hp_gain": 0.10, "set_utility": 0.40,
            }
            resolved_archetype = "attack"
            primary = primary or "ATK"
            sets = ["healing", "buff", "output"]
        return {
            "category": "healing",
            "archetype": archetype or resolved_archetype,
            "primary_scaling_stat": primary,
            "set_categories": sets,
            "stat_category": "healing",
            "min_relevance_weight": 0.60,
            "min_relevant_substats": 2,
            "stat_weights": stat_weights,
            "objective_weights": objective,
            "effect_weights": {},
        }

    return {
        "category": "support",
        "archetype": archetype or "utility",
        "primary_scaling_stat": primary or "RAGE_REGEN",
        "set_categories": ["buff", "healing", "defense"],
        "stat_category": "buff",
        "min_relevance_weight": 0.60,
        "min_relevant_substats": 1,
        "stat_weights": {
            "RAGE_REGEN": 1.0, "ATK_SPEED": 0.45, "HP_PCT": 0.20,
            "DEF_PCT": 0.15, "ATK_PCT": 0.15,
        },
        "objective_weights": {
            "rage_regen": 1.0, "attack_speed": 0.35, "hp_gain": 0.20,
            "defense_gain": 0.15, "attack_gain": 0.15, "set_utility": 0.75,
        },
        "effect_weights": {},
    }


def resolve_recommendation_profile(core: dict[str, Any]) -> dict[str, Any]:
    """Resolve defaults and explicit HeroCore recommendation_profile overrides."""
    raw = core.get("recommendation_profile") or {}
    category = _category_from_core(core)
    primary = raw.get("primary_scaling_stat") or raw.get("primary_stat")
    archetype = raw.get("archetype") or raw.get("subtype")
    profile = _default_profile(
        category,
        None if primary is None else str(primary),
        None if archetype is None else str(archetype),
    )

    if "set_categories" in raw:
        values = [str(value).strip().lower() for value in (raw.get("set_categories") or []) if str(value).strip()]
        if values:
            profile["set_categories"] = values
    if raw.get("stat_category"):
        profile["stat_category"] = str(raw["stat_category"]).strip().lower()
    if raw.get("primary_scaling_stat") or raw.get("primary_stat"):
        profile["primary_scaling_stat"] = str(raw.get("primary_scaling_stat") or raw.get("primary_stat")).strip().upper()
    if raw.get("archetype") or raw.get("subtype"):
        profile["archetype"] = str(raw.get("archetype") or raw.get("subtype")).strip().lower()

    if raw.get("min_relevance_weight") is not None:
        profile["min_relevance_weight"] = max(0.0, float(raw["min_relevance_weight"]))
    if raw.get("min_relevant_substats") is not None:
        profile["min_relevant_substats"] = max(0, min(int(raw["min_relevant_substats"]), 4))

    explicit_stats = _upper_mapping(raw.get("stat_weights"))
    if explicit_stats:
        profile["stat_weights"] = explicit_stats
    explicit_objective = _objective_mapping(raw.get("objective_weights"))
    if explicit_objective:
        profile["objective_weights"] = explicit_objective
    explicit_effects = _upper_mapping(raw.get("effect_weights"))
    if explicit_effects:
        profile["effect_weights"] = explicit_effects

    normalizers = dict(_STAT_NORMALIZERS)
    normalizers.update(_upper_mapping(raw.get("stat_normalizers")))
    profile["stat_normalizers"] = normalizers
    return profile


def relevant_stat_types(profile: dict[str, Any]) -> set[str]:
    return {key.upper() for key, weight in profile.get("stat_weights", {}).items() if float(weight) > 0}


def item_potential(item: EquipmentItem, profile: dict[str, Any]) -> float:
    score = 0.0
    weights = _upper_mapping(profile.get("stat_weights"))
    normalizers = _upper_mapping(profile.get("stat_normalizers")) or dict(_STAT_NORMALIZERS)
    for stat in item.stats:
        key = stat.stat_type.value.upper()
        score += float(stat.stat_value) * weights.get(key, 0.0) * normalizers.get(key, 1.0)
    return score


def _trigger_uptime_factor(effect: Any) -> float:
    """Conservative cheap-score factor for non-static set effects.

    Precise uptime is resolved by HeroCore. This factor only prevents the cheap
    pruning stage from treating a short conditional buff as permanently active.
    """
    trigger = str(getattr(effect, "trigger", "always") or "always").lower()
    if trigger in {"always", "while_deployed", "passive"}:
        return 1.0
    if trigger in {"on_ult", "on_ultimate", "on_any_ultimate_cast"}:
        return 0.60
    if trigger in {"on_crit", "on_basic_crit"}:
        return 0.50
    if trigger == "on_basic_attack_damage":
        return 1.0
    if trigger == "on_kill":
        # Recommendation uses a non-dying dummy unless a future scenario adds
        # explicit kill events.
        return 0.0
    return 0.35


def effect_potential(effect: Any, profile: dict[str, Any]) -> float:
    key = effect.effect_type.value.upper()
    explicit = _upper_mapping(profile.get("effect_weights"))
    stack_factor = max(1.0, float(getattr(effect, "max_stacks", 1) or 1))
    uptime = _trigger_uptime_factor(effect)

    if key in explicit:
        return max(0.0, float(effect.value) * explicit[key] * stack_factor * uptime)

    if profile.get("category") == "output":
        if key == "EXTRA_DAMAGE":
            # EXTRA_DAMAGE may be fixed damage (e.g. 600) or a ratio (e.g.
            # 4% max-HP true damage). Normalize fixed values before weighting;
            # never treat a raw 600 as a 600x multiplicative bonus.
            raw = abs(float(effect.value))
            normalized = raw / 1000.0 if raw >= 1.0 else raw
            return normalized * 2.0 * stack_factor * uptime

        weights = {
            "ATK_PCT": 8.0,
            "ATK_FLAT": 1.0 / 900.0,
            "CRIT_RATE": 7.0,
            "CRIT_DMG": 3.2,
            "ATK_SPEED": 1.0 / 85.0,
            "RAGE_REGEN": 2.2,
            "DAMAGE_PCT": 8.0,
            "BASIC_DMG": 6.0,
            "SKILL_DMG": 5.0,
            "ULT_DMG": 5.0,
            "SINGLE_DMG": 4.0,
            "AOE_DMG": 4.0,
            "PENETRATION": 3.0,
        }
        return max(0.0, float(effect.value) * weights.get(key, 0.0) * stack_factor * uptime)

    stat_weights = _upper_mapping(profile.get("stat_weights"))
    normalizers = _upper_mapping(profile.get("stat_normalizers")) or dict(_STAT_NORMALIZERS)
    if key in stat_weights:
        return max(
            0.0,
            float(effect.value) * stat_weights[key] * normalizers.get(key, 1.0) * stack_factor * uptime,
        )
    if key == "DAMAGE_PCT" and profile.get("category") == "support":
        return max(0.0, float(effect.value) * 0.25 * stack_factor * uptime)
    return 0.0


def _active_sets_and_effects(database: Any, items: list[EquipmentItem]) -> tuple[list[str], list[Any]]:
    definitions = database.load_sets()
    counts = Counter(item.set_id for item in items)
    active = sorted(
        set_id for set_id, count in counts.items()
        if set_id in definitions and count >= int(definitions[set_id].required_pieces)
    )
    effects = [
        effect for effect in database.load_set_effects()
        if effect.set_id in active and not effect.requires_dot
    ]
    return active, effects


def evaluate_role_build(
    database: Any,
    core: dict[str, Any],
    item_ids: list[str] | tuple[str, ...],
    profile: dict[str, Any],
) -> dict[str, Any]:
    """Evaluate tank/healing/support builds with an auditable profile objective.

    HeroCore currently uses the event simulator for output ranking. Other roles
    keep a profile-driven panel objective until healing/shield/utility events are
    modeled, while sharing the same equipment/set catalog interfaces.
    """
    items = list(database.load_equipment(list(item_ids)))
    if len(items) != len(item_ids):
        raise ValueError("one or more equipment items are unavailable")

    base = (core.get("hero") or {}).get("base_stats") or {}
    base_atk = float(base.get("atk") or 0.0)
    base_hp = float(base.get("hp") or base.get("max_hp") or 0.0)
    base_def = float(base.get("def") or base.get("defense") or 0.0)
    base_speed = float(base.get("atk_speed") or 0.0)
    base_rage = float(base.get("rage_regen") or 0.0)
    base_heal = float(base.get("healing_effect") or 0.0)

    stats: defaultdict[str, float] = defaultdict(float)
    for item in items:
        for stat in item.stats:
            stats[stat.stat_type.value.upper()] += float(stat.stat_value)

    active_sets, effects = _active_sets_and_effects(database, items)
    for effect in effects:
        key = effect.effect_type.value.upper()
        if effect.trigger == "always" and key in _PANEL_EFFECT_TYPES:
            stats[key] += float(effect.value)

    rules = GameRules.from_mapping(database.load_rules())
    atk = rules.compose_attack(base_atk, stats["ATK_FLAT"], stats["ATK_PCT"])
    hp = (base_hp + stats["HP_FLAT"]) * (1.0 + stats["HP_PCT"])
    defense = (base_def + stats["DEF_FLAT"]) * (1.0 + stats["DEF_PCT"])
    atk_speed = base_speed + stats["ATK_SPEED"]
    rage_regen = base_rage + stats["RAGE_REGEN"]
    healing_effect = base_heal + stats["HEALING_EFFECT"]

    def gain(total: float, base_value: float, flat_fallback: float) -> float:
        if base_value > 0:
            return total / base_value - 1.0
        return total * flat_fallback

    metrics = {
        "attack_gain": gain(atk, base_atk, 1.0 / 900.0),
        "hp_gain": gain(hp, base_hp, 1.0 / 10000.0),
        "defense_gain": gain(defense, base_def, 1.0 / 1000.0),
        "healing_effect": healing_effect,
        "attack_speed": (atk_speed - base_speed) / 100.0,
        "rage_regen": rage_regen - base_rage,
    }

    set_utility = 0.0
    for effect in effects:
        key = effect.effect_type.value.upper()
        if effect.trigger == "always" and key in _PANEL_EFFECT_TYPES:
            continue
        set_utility += effect_potential(effect, profile)
    metrics["set_utility"] = set_utility

    objective = _objective_mapping(profile.get("objective_weights"))
    contributions = {
        key: metrics.get(key, 0.0) * float(weight)
        for key, weight in objective.items()
    }
    score = sum(contributions.values())
    return {
        "role_score": score,
        "role_metrics": metrics,
        "role_contributions": contributions,
        "evaluation_mode": "recommendation_profile_panel_proxy",
        "recommendation_profile": deepcopy(profile),
        "panel": {
            "atk": atk,
            "hp": hp,
            "defense": defense,
            "atk_speed": atk_speed,
            "rage_regen": rage_regen,
            "healing_effect": healing_effect,
        },
        "active_sets": active_sets,
    }

"""Support-specific recommendation modes layered on top of HeroCore.

The optimizer keeps the existing set-aware candidate search. This module only
changes how support candidates are scored:

* ``manual_priority`` assumes the player manually aligns support ultimates with
  important damage windows. It therefore ranks gear by a HeroCore-configurable
  stat/objective priority instead of rewarding automatic cast frequency.
* ``auto_utility`` lets HeroCore describe a lightweight team-utility model. The
  generic HeroCore event simulator resolves rage, attack speed and ultimate
  cadence; the utility model converts the resulting activation coverage into a
  comparable support score. If a support core has not supplied such a model,
  the mode explicitly falls back to the existing auditable panel proxy.

Hero-specific semantics stay in JSON. The optimizer contains no Dolores/Ferssi
or other hero-name branches.
"""
from __future__ import annotations

import math
from copy import deepcopy
from typing import Any

from equipment_recommendation_profile import evaluate_role_build
from hero_core_engine import HeroCoreSimulator

MANUAL_PRIORITY = "manual_priority"
AUTO_UTILITY = "auto_utility"

_MODE_ALIASES = {
    "manual": MANUAL_PRIORITY,
    "manual_priority": MANUAL_PRIORITY,
    "manual_burst": MANUAL_PRIORITY,
    "burst": MANUAL_PRIORITY,
    "auto": AUTO_UTILITY,
    "automatic": AUTO_UTILITY,
    "auto_utility": AUTO_UTILITY,
    "automatic_utility": AUTO_UTILITY,
}

_STAT_TO_OBJECTIVE = {
    "ATK_PCT": "attack_gain",
    "ATK_FLAT": "attack_gain",
    "HP_PCT": "hp_gain",
    "HP_FLAT": "hp_gain",
    "DEF_PCT": "defense_gain",
    "DEF_FLAT": "defense_gain",
    "ATK_SPEED": "attack_speed",
    "RAGE_REGEN": "rage_regen",
    "HEALING_EFFECT": "healing_effect",
}

_DEFAULT_PRIORITIES = {
    "ATK": ["ATK_PCT", "ATK_FLAT", "RAGE_REGEN", "ATK_SPEED", "HP_PCT", "DEF_PCT"],
    "HP": ["HP_PCT", "HP_FLAT", "RAGE_REGEN", "ATK_SPEED", "DEF_PCT", "ATK_PCT"],
    "DEF": ["DEF_PCT", "DEF_FLAT", "RAGE_REGEN", "HP_PCT", "ATK_SPEED", "ATK_PCT"],
    "RAGE_REGEN": ["RAGE_REGEN", "ATK_SPEED", "HP_PCT", "DEF_PCT", "ATK_PCT"],
}


def normalize_support_recommendation_mode(value: Any) -> str:
    """Return a canonical support recommendation mode."""
    normalized = str(value or MANUAL_PRIORITY).strip().lower()
    try:
        return _MODE_ALIASES[normalized]
    except KeyError as error:
        raise ValueError(
            "support_recommendation_mode must be manual_priority or auto_utility"
        ) from error


def is_support_profile(profile: dict[str, Any] | None) -> bool:
    return str((profile or {}).get("category") or "").strip().lower() == "support"


def _float_mapping(value: Any, *, upper: bool = False) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, float] = {}
    for key, raw in value.items():
        try:
            name = str(key).strip()
            if upper:
                name = name.upper()
            else:
                name = name.lower()
            result[name] = float(raw)
        except (TypeError, ValueError):
            continue
    return result


def _manual_priority_weights(priority: list[str]) -> dict[str, float]:
    """Turn an ordered stat list into stable, decreasing pruning weights.

    These weights intentionally express order rather than a claim about exact
    team DPS conversion. Final manual-mode output is still labelled a priority
    proxy so users do not mistake it for a damage simulation.
    """
    result: dict[str, float] = {}
    weight = 1.0
    for index, stat in enumerate(priority):
        key = str(stat).strip().upper()
        if not key or key in result:
            continue
        result[key] = weight
        weight *= 0.55 if index < 2 else 0.60
    return result


def _default_manual_priority(profile: dict[str, Any]) -> list[str]:
    primary = str(profile.get("primary_scaling_stat") or "RAGE_REGEN").strip().upper()
    return list(_DEFAULT_PRIORITIES.get(primary, _DEFAULT_PRIORITIES["RAGE_REGEN"]))


def resolve_support_profile(
    core: dict[str, Any],
    base_profile: dict[str, Any],
    requested_mode: Any = None,
) -> dict[str, Any]:
    """Overlay support-mode settings without changing non-support profiles.

    HeroCore may provide mode-specific overrides under::

        recommendation_profile.support_modes.manual_priority
        recommendation_profile.support_modes.auto_utility

    Manual mode accepts ``stat_priority``, ``ignored_stats``, ``stat_weights``,
    ``objective_weights``, ``effect_weights`` and ``set_utility_weight``.
    Automatic mode accepts the same direct weight overrides plus
    ``utility_model``. The latter is intentionally generic and hero-name free.
    """
    profile = deepcopy(base_profile)
    if not is_support_profile(profile):
        return profile

    mode = normalize_support_recommendation_mode(requested_mode)
    raw_profile = core.get("recommendation_profile") or {}
    support_modes = raw_profile.get("support_modes") or {}
    mode_config = support_modes.get(mode) or {}
    if not isinstance(mode_config, dict):
        mode_config = {}

    profile["support_recommendation_mode"] = mode

    if mode == MANUAL_PRIORITY:
        raw_priority = mode_config.get("stat_priority")
        if isinstance(raw_priority, (list, tuple)):
            priority = [str(value).strip().upper() for value in raw_priority if str(value).strip()]
        else:
            priority = _default_manual_priority(profile)

        ignored = {
            str(value).strip().upper()
            for value in (mode_config.get("ignored_stats") or [])
            if str(value).strip()
        }
        priority = [stat for stat in priority if stat not in ignored]
        stat_weights = _manual_priority_weights(priority)
        explicit_stats = _float_mapping(mode_config.get("stat_weights"), upper=True)
        if explicit_stats:
            stat_weights.update(explicit_stats)
        for stat in ignored:
            stat_weights.pop(stat, None)

        objective_weights: dict[str, float] = {}
        for stat, weight in stat_weights.items():
            objective = _STAT_TO_OBJECTIVE.get(stat)
            if objective:
                objective_weights[objective] = max(objective_weights.get(objective, 0.0), weight)
        objective_weights["set_utility"] = float(mode_config.get("set_utility_weight", 0.25))
        explicit_objective = _float_mapping(mode_config.get("objective_weights"))
        if explicit_objective:
            objective_weights.update(explicit_objective)

        profile["stat_priority"] = priority
        profile["ignored_stats"] = sorted(ignored)
        profile["stat_weights"] = stat_weights
        profile["objective_weights"] = objective_weights
        profile["evaluation_semantic"] = "manual_burst_stat_priority"
    else:
        explicit_stats = _float_mapping(mode_config.get("stat_weights"), upper=True)
        if explicit_stats:
            profile["stat_weights"] = explicit_stats
        explicit_objective = _float_mapping(mode_config.get("objective_weights"))
        if explicit_objective:
            profile["objective_weights"] = explicit_objective
        model = mode_config.get("utility_model")
        if isinstance(model, dict) and model:
            profile["auto_utility_model"] = deepcopy(model)
        else:
            profile.pop("auto_utility_model", None)
        profile["evaluation_semantic"] = (
            "automatic_timeline_team_utility_proxy"
            if profile.get("auto_utility_model")
            else "automatic_panel_proxy_fallback"
        )

    explicit_effects = _float_mapping(mode_config.get("effect_weights"), upper=True)
    if explicit_effects:
        profile["effect_weights"] = explicit_effects
    if isinstance(mode_config.get("set_categories"), (list, tuple)):
        categories = [
            str(value).strip().lower()
            for value in mode_config["set_categories"]
            if str(value).strip()
        ]
        if categories:
            profile["set_categories"] = categories
    if mode_config.get("min_relevant_substats") is not None:
        profile["min_relevant_substats"] = max(
            0, min(int(mode_config["min_relevant_substats"]), 4)
        )
    return profile


def _panel_source_value(panel: dict[str, Any], source_stat: Any) -> float:
    key = str(source_stat or "atk").strip().lower()
    aliases = {
        "attack": "atk",
        "atk": "atk",
        "hp": "hp",
        "health": "hp",
        "def": "defense",
        "defense": "defense",
        "atk_speed": "atk_speed",
        "attack_speed": "atk_speed",
        "rage": "rage_regen",
        "rage_regen": "rage_regen",
        "healing": "healing_effect",
        "healing_effect": "healing_effect",
    }
    return float(panel.get(aliases.get(key, key), 0.0) or 0.0)


def _utility_from_trial(trial: Any, model: dict[str, Any], seconds: float) -> dict[str, float]:
    event_name = str(model.get("activation_event") or "ultimate_cast")
    persistent = bool(model.get("persistent", False))
    activations = 1.0 if persistent else float(trial.event_counts.get(event_name, 0.0))
    duration = max(0.0, float(model.get("duration_seconds", seconds if persistent else 0.0)))
    if persistent:
        coverage = 1.0
    elif duration > 0.0:
        coverage = min(float(model.get("coverage_cap", 1.0)), activations * duration / seconds)
    else:
        coverage = min(float(model.get("coverage_cap", 1.0)), activations)

    source = _panel_source_value(trial.panel, model.get("source_stat") or "atk")
    ratio = float(model.get("buff_ratio", 0.0))
    flat = float(model.get("flat_utility", 0.0))
    targets = max(1.0, float(model.get("target_count", 1.0)))
    response = max(0.0, float(model.get("team_response", 1.0)))
    magnitude = source * ratio + flat
    utility = magnitude * coverage * targets * response
    return {
        "team_utility": utility,
        "buff_magnitude": magnitude,
        "coverage": coverage,
        "activation_count": activations,
    }


def _mean_std(values: list[float]) -> tuple[float, float]:
    if not values:
        return 0.0, 0.0
    mean = sum(values) / len(values)
    if len(values) == 1:
        return mean, 0.0
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    return mean, math.sqrt(max(0.0, variance))


def support_uses_simulation(profile: dict[str, Any]) -> bool:
    return (
        is_support_profile(profile)
        and profile.get("support_recommendation_mode") == AUTO_UTILITY
        and isinstance(profile.get("auto_utility_model"), dict)
        and bool(profile.get("auto_utility_model"))
    )


def evaluate_support_build(
    database: Any,
    core: dict[str, Any],
    item_ids: list[str] | tuple[str, ...],
    profile: dict[str, Any],
    *,
    target: dict[str, Any] | None = None,
    policy: str = "",
    trials: int = 1,
    seed: int = 20260828,
    seconds: float = 60.0,
) -> dict[str, Any]:
    """Evaluate one support build under the selected recommendation mode."""
    mode = normalize_support_recommendation_mode(
        profile.get("support_recommendation_mode") or MANUAL_PRIORITY
    )
    panel_proxy = evaluate_role_build(database, core, list(item_ids), profile)

    if mode == MANUAL_PRIORITY:
        result = dict(panel_proxy)
        result["evaluation_mode"] = "support_manual_stat_priority"
        result["support_recommendation_mode"] = mode
        result["auto_utility_fallback"] = False
        return result

    model = profile.get("auto_utility_model")
    if not isinstance(model, dict) or not model:
        result = dict(panel_proxy)
        result["evaluation_mode"] = "support_auto_panel_proxy_fallback"
        result["support_recommendation_mode"] = mode
        result["auto_utility_fallback"] = True
        result["fallback_reason"] = "HeroCore has no recommendation_profile.support_modes.auto_utility.utility_model"
        return result

    seconds = max(1.0, float(seconds))
    trial_count = max(1, min(int(trials), 256))
    samples: list[dict[str, float]] = []
    for trial_index in range(trial_count):
        trial = HeroCoreSimulator(
            core,
            database=database,
            item_ids=list(item_ids),
            target=target or {},
            policy=policy,
            seed=int(seed) + trial_index,
            warmup=0.0,
            measurement=seconds,
        ).run()
        samples.append(_utility_from_trial(trial, model, seconds))

    utilities = [sample["team_utility"] for sample in samples]
    mean, std = _mean_std(utilities)
    secondary_weight = max(0.0, float(model.get("panel_secondary_weight", 0.0)))
    panel_secondary = float(panel_proxy["role_score"]) * secondary_weight
    score = mean + panel_secondary

    metrics = dict(panel_proxy.get("role_metrics") or {})
    for key in ("buff_magnitude", "coverage", "activation_count"):
        metrics[key] = sum(sample[key] for sample in samples) / len(samples)
    metrics["team_utility"] = mean
    contributions = {
        "team_utility": mean,
        "panel_secondary": panel_secondary,
    }
    return {
        **panel_proxy,
        "role_score": score,
        "role_metrics": metrics,
        "role_contributions": contributions,
        "evaluation_mode": "support_auto_utility_simulation",
        "support_recommendation_mode": mode,
        "auto_utility_fallback": False,
        "utility_model": deepcopy(model),
        "utility_60s": {"mean": mean, "std": std},
    }

"""Backend facade for HeroCore catalog, import, simulation and equipment recommendation."""
from __future__ import annotations

import heapq
import json
import re
from collections import Counter, defaultdict
from itertools import product
from pathlib import Path
from typing import Any, Callable

from equipment_db import EquipmentDatabase
from equipment_models import EquipmentItem, SetAscension
from equipment_recommendation_prefilter import prefilter_equipment
from equipment_recommendation_profile import (
    effect_potential as _profile_effect_potential,
    evaluate_role_build,
    item_potential as _profile_item_potential,
    resolve_recommendation_profile,
)
from equipment_set_variants import iter_ascension_variants, load_set_evolutions, load_set_names
from hero_core_engine import (
    DEFAULT_CORE_DIR,
    HeroCoreError,
    HeroCoreSimulator,
    list_cores,
    load_core,
    simulate_average,
    validate_core,
)
from optimizer_projection import OptimizerEquipmentDatabase

_SAFE_CORE_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_SLOTS = ("weapon", "armor", "bracelet", "necklace", "ring")
_LEFT_SLOT_ORDER = ("weapon", "armor")
_RIGHT_SLOT_ORDER = ("bracelet", "necklace", "ring")
_LEFT_SLOTS = set(_LEFT_SLOT_ORDER)
_RIGHT_SLOTS = set(_RIGHT_SLOT_ORDER)
_DEFAULT_LEFT_GROUP_CANDIDATES = 8
_DEFAULT_RIGHT_GROUP_CANDIDATES = 16
_DEFAULT_SCREENING_TRIALS = 3


def _output_profile() -> dict[str, Any]:
    return resolve_recommendation_profile({"recommendation_profile": {"category": "output"}})


def hero_core_catalog(core_dir: str | Path = DEFAULT_CORE_DIR) -> dict[str, Any]:
    return {"hero_cores": list_cores(Path(core_dir))}


def hero_core_detail(core_id: str, core_dir: str | Path = DEFAULT_CORE_DIR) -> dict[str, Any]:
    return load_core(core_id, Path(core_dir))


def _derived_codex_skills(core: dict[str, Any]) -> list[dict[str, Any]]:
    codex = core.get("codex") or {}
    supplied = codex.get("skills") or []
    if supplied:
        return [dict(row) for row in supplied]
    rows: list[dict[str, Any]] = []
    for skill_id, skill in (core.get("skills") or {}).items():
        rows.append({
            "id": skill_id,
            "name": skill.get("name") or skill_id,
            "type": skill.get("kind") or "skill",
            "description": skill.get("description") or "",
            "coefficient": skill.get("coefficient"),
            "target_cap": skill.get("target_cap", 1),
            "duration": skill.get("duration"),
            "direct_damage": True,
        })
    return rows


def hero_core_codex_payload(core_dir: str | Path = DEFAULT_CORE_DIR) -> dict[str, list[dict[str, Any]]]:
    heroes: list[dict[str, Any]] = []
    skills: list[dict[str, Any]] = []
    root = Path(core_dir)
    for metadata in list_cores(root):
        core = load_core(str(metadata["id"]), root)
        hero = core["hero"]
        codex = core.get("codex") or {}
        base = dict(hero.get("base_stats") or {})
        hero_key = str(hero["id"])
        numeric_complete = all(base.get(name) is not None for name in ("atk", "crit_rate", "crit_dmg", "attack_interval"))
        heroes.append({
            "hero_key": hero_key,
            "hero_name": hero["name"],
            "title": hero.get("title") or codex.get("title"),
            "faction": hero.get("faction") or codex.get("faction"),
            "role": hero.get("role") or codex.get("role"),
            "completeness": "numeric_complete" if numeric_complete else "numeric_partial",
            "mechanic_summary": hero.get("mechanic_summary") or codex.get("mechanic_summary") or "HeroCore 可执行英雄机制",
            "source_url": "",
            "source_kind": "herocore",
            "source_date": core.get("game_version"),
            "official_channel": "HeroCore",
            "data_version": core.get("core_version"),
            "updated_at": None,
            "hero_core_available": True,
            "hero_core_id": hero_key,
            "base_stats": base,
            "resources": core.get("resources") or {},
            "damage_type": hero.get("damage_type"),
            "talents": codex.get("talents") or [],
            "validation_required": core.get("validation_required") or [],
            "recommendation_profile": core.get("recommendation_profile"),
        })
        for row in _derived_codex_skills(core):
            skill_id = str(row.get("id") or row.get("skill_key") or row.get("name") or "skill")
            skills.append({
                "hero_key": hero_key,
                "skill_key": skill_id,
                "skill_name": row.get("name") or row.get("skill_name") or skill_id,
                "skill_type": row.get("type") or row.get("skill_type") or "skill",
                "description": row.get("description") or "",
                "coefficient": row.get("coefficient"),
                "target_cap": row.get("target_cap"),
                "duration": row.get("duration"),
                "direct_damage": 1 if row.get("direct_damage", True) else 0,
                "optimizer_usable": 1,
                "source_url": "",
                "source_date": core.get("game_version"),
                "value_json": row.get("values") or row.get("value_json"),
                "notes": row.get("notes"),
                "hero_core_managed": True,
            })
    return {"heroes": heroes, "skills": skills}


def import_hero_core(payload: dict[str, Any], core_dir: str | Path = DEFAULT_CORE_DIR) -> dict[str, Any]:
    core = payload.get("core") if isinstance(payload, dict) and "core" in payload else payload
    if not isinstance(core, dict):
        raise HeroCoreError("上传文件必须是 HeroCore JSON 对象")
    validate_core(core)
    try:
        resolve_recommendation_profile(core)
    except (TypeError, ValueError) as error:
        raise HeroCoreError(f"recommendation_profile 无效: {error}") from error

    hero_id = str(core["hero"]["id"])
    if not _SAFE_CORE_ID.fullmatch(hero_id):
        raise HeroCoreError("hero.id 只能包含英文字母、数字、下划线和连字符")
    base = core["hero"].get("base_stats") or {}
    missing = [name for name in ("atk", "crit_rate", "crit_dmg", "attack_interval") if base.get(name) is None]
    if missing:
        raise HeroCoreError("英雄属性缺少必填字段: " + ", ".join(missing))
    root = Path(core_dir)
    root.mkdir(parents=True, exist_ok=True)
    destination = root / f"{hero_id.lower()}.json"
    temporary = destination.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(core, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(destination)
    installed = load_core(hero_id, root)
    return {
        "ok": True,
        "hero_core_id": installed["hero"]["id"],
        "hero_name": installed["hero"]["name"],
        "path": str(destination),
        "core_version": installed.get("core_version"),
        "game_version": installed.get("game_version"),
    }


def _target_from_payload(payload: dict[str, Any]) -> tuple[float, float, int, dict[str, Any]]:
    target_def = float(payload.get("target_def", 0.0))
    if target_def < 0:
        raise HeroCoreError("target_def must be non-negative")
    raw_mres = payload.get("target_mres")
    target_mres = target_def if raw_mres in (None, "") else float(raw_mres)
    if target_mres < 0:
        raise HeroCoreError("target_mres must be non-negative")
    enemy_count = int(payload.get("enemy_count", 1))
    if enemy_count < 1:
        raise HeroCoreError("enemy_count must be at least 1")
    return target_def, target_mres, enemy_count, {
        "defense": target_def,
        "mres": target_mres,
        "control_immune": bool(payload.get("control_immune", True)),
        "enemy_count": enemy_count,
    }


def simulate_hero_core(database_path: str | Path, payload: dict[str, Any]) -> dict[str, Any]:
    core_id = str(payload.get("hero_core_id") or "SUN_WUKONG")
    core = load_core(core_id)
    item_ids = [str(value) for value in (payload.get("item_ids") or []) if value]
    if len(item_ids) > 5 or len(set(item_ids)) != len(item_ids):
        raise HeroCoreError("item_ids must contain at most five unique equipment ids")
    target_def, _, _, target = _target_from_payload(payload)
    trials = int(payload.get("trials", 64))
    warmup = float(payload.get("warmup", 120.0))
    measurement = float(payload.get("measurement", 600.0))
    if warmup < 0 or measurement <= 0:
        raise HeroCoreError("warmup must be non-negative and measurement must be positive")
    database = EquipmentDatabase(database_path)
    try:
        database.initialize()
        if item_ids:
            items = database.load_equipment(item_ids)
            slots = [item.slot.value for item in items]
            if len(items) != len(item_ids):
                raise HeroCoreError("one or more equipment items are unavailable")
            if len(set(slots)) != len(slots):
                raise HeroCoreError("a build may contain at most one item per equipment slot")
        return simulate_average(
            core,
            database=database,
            item_ids=item_ids,
            target=target,
            policy=str(payload.get("policy") or core.get("default_policy") or ""),
            trials=trials,
            warmup=warmup,
            measurement=measurement,
            seed=int(payload.get("seed", 20260828)),
        )
    finally:
        database.close()


def _item_potential(item: EquipmentItem, recommendation_profile: dict[str, Any] | None = None) -> float:
    return _profile_item_potential(item, recommendation_profile or _output_profile())


def _effect_potential(effect, recommendation_profile: dict[str, Any] | None = None) -> float:
    return _profile_effect_potential(effect, recommendation_profile or _output_profile())


def _eligible_slots(slot_group: str | None) -> set[str]:
    if slot_group == "left":
        return set(_LEFT_SLOTS)
    if slot_group == "right":
        return set(_RIGHT_SLOTS)
    return set(_SLOTS)


def _set_effect_scores(
    database: OptimizerEquipmentDatabase,
    recommendation_profile: dict[str, Any] | None = None,
) -> dict[str, float]:
    profile = recommendation_profile or _output_profile()
    scores: defaultdict[str, float] = defaultdict(float)
    for effect in database.load_set_effects():
        if not effect.requires_dot:
            scores[effect.set_id] += _effect_potential(effect, profile)
    return dict(scores)


def _reachable_set_context(
    database: OptimizerEquipmentDatabase,
    all_items: list[EquipmentItem],
    recommendation_profile: dict[str, Any] | None = None,
) -> tuple[dict, dict[str, str], dict[str, float], dict[str, set[str]], set[str]]:
    definitions = database.load_sets()
    evolutions = load_set_evolutions(database)
    effect_scores = _set_effect_scores(database, recommendation_profile)
    slots_by_current_set: defaultdict[str, set[str]] = defaultdict(set)
    for item in all_items:
        slots_by_current_set[item.set_id].add(item.slot.value)
    sources_by_target: defaultdict[str, set[str]] = defaultdict(set)
    for source, target in evolutions.items():
        sources_by_target[target].add(source)

    # Reachability is a structural property of the inventory, not a scoring
    # property of the current hero profile. Complete-set-only validation happens
    # after lossy candidate/group pruning, so every physically reachable set
    # must survive those stages even when its cheap effect score is zero.
    feasible_final_sets: set[str] = set()
    for final_set_id, definition in definitions.items():
        compatible_current_sets = {final_set_id, *sources_by_target.get(final_set_id, set())}
        reachable_slots: set[str] = set()
        for current_set_id in compatible_current_sets:
            reachable_slots.update(slots_by_current_set.get(current_set_id, set()))
        if len(reachable_slots & _eligible_slots(definition.slot_group)) < int(definition.required_pieces):
            continue
        feasible_final_sets.add(final_set_id)
    return definitions, evolutions, effect_scores, sources_by_target, feasible_final_sets


def _set_candidate_bonuses(
    database: OptimizerEquipmentDatabase,
    all_items: list[EquipmentItem],
    recommendation_profile: dict[str, Any] | None = None,
) -> dict[str, float]:
    definitions, evolutions, effect_scores, sources_by_target, feasible_final_sets = _reachable_set_context(
        database, all_items, recommendation_profile
    )
    feasible_final_bonus = {
        set_id: effect_scores.get(set_id, 0.0) / max(1, int(definitions[set_id].required_pieces))
        for set_id in feasible_final_sets
    }
    current_sets = {item.set_id for item in all_items}
    result: dict[str, float] = {}
    for current_set_id in current_sets:
        reachable_final_sets = {current_set_id}
        target = evolutions.get(current_set_id)
        if target:
            reachable_final_sets.add(target)
        best = max((feasible_final_bonus.get(set_id, 0.0) for set_id in reachable_final_sets), default=0.0)
        if best > 0:
            result[current_set_id] = best
    return result


def _select_set_aware_candidates(
    database: OptimizerEquipmentDatabase,
    by_slot: dict[str, list[EquipmentItem]],
    candidate_per_slot: int,
    recommendation_profile: dict[str, Any] | None = None,
) -> tuple[dict[str, list[EquipmentItem]], dict[str, float]]:
    """Select strong individual items while hard-protecting every feasible set.

    Protection is not limited to native T2 anymore. T3 sets and mixed
    ``native T2 + evolvable T1`` final states receive one best representative in
    every eligible slot, preventing a complete set from disappearing before the
    group/HeroCore stages. Physical reachability is protected independently of
    whether the current profile assigns a positive cheap score to the set.
    """
    profile = recommendation_profile or _output_profile()
    all_items = [item for values in by_slot.values() for item in values]
    bonuses = _set_candidate_bonuses(database, all_items, profile)
    definitions, _, _, sources_by_target, feasible_final_sets = _reachable_set_context(database, all_items, profile)

    candidates: dict[str, list[EquipmentItem]] = {}
    for slot, values in by_slot.items():
        ranked = sorted(
            values,
            key=lambda item: (
                _item_potential(item, profile) + bonuses.get(item.set_id, 0.0),
                _item_potential(item, profile),
                item.item_id,
            ),
            reverse=True,
        )
        selected = list(ranked[:candidate_per_slot])
        selected_ids = {item.item_id for item in selected}

        for final_set_id in sorted(feasible_final_sets):
            definition = definitions[final_set_id]
            if slot not in _eligible_slots(definition.slot_group):
                continue
            compatible = {final_set_id, *sources_by_target.get(final_set_id, set())}
            representatives = [item for item in values if item.set_id in compatible]
            if not representatives:
                continue
            best_item = max(
                representatives,
                key=lambda item: (
                    _item_potential(item, profile) + bonuses.get(item.set_id, 0.0),
                    _item_potential(item, profile),
                    item.item_id,
                ),
            )
            if best_item.item_id not in selected_ids:
                selected.append(best_item)
                selected_ids.add(best_item.item_id)
        candidates[slot] = selected
    return candidates, bonuses


def _active_set_ids_for_items(items: tuple[EquipmentItem, ...], definitions: dict) -> set[str]:
    counts = Counter(item.set_id for item in items)
    return {
        set_id for set_id, count in counts.items()
        if set_id in definitions and count >= int(definitions[set_id].required_pieces)
    }


def _active_set_score(items: tuple[EquipmentItem, ...], definitions: dict, effect_scores: dict[str, float]) -> float:
    return sum(effect_scores.get(set_id, 0.0) for set_id in _active_set_ids_for_items(items, definitions))


def _cheap_group_build_score(
    items: tuple[EquipmentItem, ...],
    *,
    definitions: dict,
    effect_scores: dict[str, float],
    evolutions: dict[str, str],
    set_names: dict[str, str],
    recommendation_profile: dict[str, Any] | None = None,
) -> float:
    profile = recommendation_profile or _output_profile()
    base_score = sum(_item_potential(item, profile) for item in items)
    best_set_score = 0.0
    for variant_items, _ in iter_ascension_variants(items, evolutions, set_names):
        best_set_score = max(best_set_score, _active_set_score(variant_items, definitions, effect_scores))
    return base_score + best_set_score


def _select_group_build_candidates(
    candidates: dict[str, list[EquipmentItem]],
    slot_order: tuple[str, ...],
    keep: int,
    *,
    definitions: dict,
    effect_scores: dict[str, float],
    evolutions: dict[str, str],
    set_names: dict[str, str],
    recommendation_profile: dict[str, Any] | None = None,
) -> tuple[list[tuple[EquipmentItem, ...]], int]:
    """Cheap-score group builds plus one protected build per completed set."""
    raw_builds = product(*(candidates[slot] for slot in slot_order))
    scored: list[tuple[float, str, tuple[EquipmentItem, ...]]] = []
    best_by_active_set: dict[str, tuple[float, str, tuple[EquipmentItem, ...]]] = {}
    raw_count = 0
    for build in raw_builds:
        raw_count += 1
        physical_items = tuple(build)
        score = _cheap_group_build_score(
            physical_items,
            definitions=definitions,
            effect_scores=effect_scores,
            evolutions=evolutions,
            set_names=set_names,
            recommendation_profile=recommendation_profile,
        )
        key = "|".join(item.item_id for item in physical_items)
        row = (score, key, physical_items)
        scored.append(row)

        reachable_active: set[str] = set()
        for variant_items, _ in iter_ascension_variants(physical_items, evolutions, set_names):
            reachable_active.update(_active_set_ids_for_items(variant_items, definitions))
        for set_id in reachable_active:
            # A completed set is semantically relevant to sets_only even when
            # the current profile's cheap effect score is zero. Protect one
            # representative per completed set before truncating the group pool.
            previous = best_by_active_set.get(set_id)
            if previous is None or row > previous:
                best_by_active_set[set_id] = row

    scored.sort(reverse=True)
    selected = list(scored[:max(1, keep)])
    selected_keys = {row[1] for row in selected}
    for row in sorted(best_by_active_set.values(), reverse=True):
        if row[1] not in selected_keys:
            selected.append(row)
            selected_keys.add(row[1])
    selected.sort(reverse=True)
    return [row[2] for row in selected], raw_count


def _variant_overrides(physical_items: tuple[EquipmentItem, ...], variant_items: tuple[EquipmentItem, ...]) -> dict[str, str]:
    return {p.item_id: v.set_id for p, v in zip(physical_items, variant_items) if p.set_id != v.set_id}


def _serialize_ascensions(ascensions: tuple[SetAscension, ...]) -> list[dict[str, str]]:
    return [{
        "item_id": row.item_id,
        "slot": row.slot,
        "from_set_id": row.from_set_id,
        "to_set_id": row.to_set_id,
        "from_set_name": row.from_set_name,
        "to_set_name": row.to_set_name,
    } for row in ascensions]


def _serialize_final_set_states(items: tuple[EquipmentItem, ...], set_names: dict[str, str]) -> list[dict[str, str]]:
    return [{
        "item_id": item.item_id,
        "slot": item.slot.value,
        "set_id": item.set_id,
        "set_name": set_names.get(item.set_id, item.set_id),
    } for item in items]


def _active_set_ids(database: OptimizerEquipmentDatabase, items: tuple[EquipmentItem, ...]) -> list[str]:
    return sorted(_active_set_ids_for_items(items, database.load_sets()))


def _screen_physical_build(
    *,
    core,
    database,
    physical_items,
    evolutions,
    set_names,
    target,
    policy,
    seed,
    warmup,
    measurement,
    screening_trials,
    recommendation_profile,
) -> tuple[float, int]:
    """Screen with common random numbers across several seeds.

    A single random path can discard the true best build for heroes or sets with
    probabilistic procs. Averaging a small configurable number of shared seeds
    is substantially more stable while preserving the cheap screening stage.
    """
    best_score = float("-inf")
    best_ascensions = 10**9
    evaluations = 0
    category = recommendation_profile.get("category", "output")
    try:
        for variant_items, ascensions in iter_ascension_variants(physical_items, evolutions, set_names):
            database.set_variant_overrides(_variant_overrides(physical_items, variant_items))
            if category == "output":
                scores: list[float] = []
                for trial_index in range(max(1, int(screening_trials))):
                    trial = HeroCoreSimulator(
                        core,
                        database=database,
                        item_ids=[item.item_id for item in physical_items],
                        target=target,
                        policy=policy,
                        seed=seed + trial_index,
                        warmup=warmup,
                        measurement=measurement,
                    ).run()
                    scores.append(trial.total_damage / measurement * 60.0)
                    evaluations += 1
                score = sum(scores) / len(scores)
            else:
                evaluation = evaluate_role_build(
                    database,
                    core,
                    [item.item_id for item in physical_items],
                    recommendation_profile,
                )
                score = float(evaluation["role_score"])
                evaluations += 1
            if score > best_score or (score == best_score and len(ascensions) < best_ascensions):
                best_score, best_ascensions = score, len(ascensions)
    finally:
        database.clear_variant_overrides()
    return best_score, evaluations


def _refine_physical_build(
    *,
    core,
    database,
    item_ids,
    evolutions,
    set_names,
    target,
    policy,
    trials,
    warmup,
    measurement,
    seed,
    recommendation_profile,
) -> tuple[dict[str, Any], int]:
    database.clear_variant_overrides()
    physical_items = tuple(database.load_equipment(list(item_ids)))
    if len(physical_items) != len(item_ids):
        raise HeroCoreError("one or more shortlisted equipment items became unavailable")

    best_row = None
    best_key = None
    evaluations = 0
    category = recommendation_profile.get("category", "output")
    try:
        for variant_items, ascensions in iter_ascension_variants(physical_items, evolutions, set_names):
            database.set_variant_overrides(_variant_overrides(physical_items, variant_items))
            active_sets = _active_set_ids(database, variant_items)
            serialized_ascensions = _serialize_ascensions(ascensions)
            common = {
                "item_ids": list(item_ids),
                "active_sets": active_sets,
                "active_set_names": [set_names.get(set_id, set_id) for set_id in active_sets],
                "ascended_items": serialized_ascensions,
                "upgrade_recommendations": serialized_ascensions,
                "uses_ascension": bool(ascensions),
                "final_set_states": _serialize_final_set_states(variant_items, set_names),
                "equipment_projection": database.projection_summary(item_ids),
            }

            if category == "output":
                result = simulate_average(
                    core,
                    database=database,
                    item_ids=list(item_ids),
                    target=target,
                    policy=policy,
                    trials=trials,
                    warmup=warmup,
                    measurement=measurement,
                    seed=seed,
                )
                score = float(result["equivalent_60s"]["mean"])
                row = {
                    **common,
                    **result,
                    "role_score": score,
                    "role_metrics": {"damage_60s": score},
                    "role_contributions": {"damage_60s": score},
                    "evaluation_mode": "hero_core_damage_simulation",
                }
            else:
                evaluation = evaluate_role_build(database, core, list(item_ids), recommendation_profile)
                score = float(evaluation["role_score"])
                row = {
                    **common,
                    **evaluation,
                    "equivalent_60s": {
                        "mean": score,
                        "std": 0.0,
                        "semantic": "role_score_compatibility_alias",
                    },
                }

            evaluations += 1
            key = (score, -len(ascensions), "|".join(item.set_id for item in variant_items))
            if best_key is None or key > best_key:
                best_key, best_row = key, row
    finally:
        database.clear_variant_overrides()

    if best_row is None:
        raise HeroCoreError("no equipment set variant could be evaluated")
    return best_row, evaluations


def recommend_hero_core(
    database_path: str | Path,
    payload: dict[str, Any],
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Profile-aware, hierarchical set-aware HeroCore equipment recommendation."""
    core_id = str(payload.get("hero_core_id") or "SUN_WUKONG")
    core = load_core(core_id)
    try:
        recommendation_profile = resolve_recommendation_profile(core)
    except (TypeError, ValueError) as error:
        raise HeroCoreError(f"recommendation_profile 无效: {error}") from error

    top_k = max(1, min(int(payload.get("top_k", 5)), 20))
    candidate_per_slot = max(1, min(int(payload.get("candidate_per_slot", 5)), 8))
    left_group_candidates = max(1, min(int(payload.get("left_group_candidates", _DEFAULT_LEFT_GROUP_CANDIDATES)), 64))
    right_group_candidates = max(1, min(int(payload.get("right_group_candidates", _DEFAULT_RIGHT_GROUP_CANDIDATES)), 128))
    refine_trials = max(1, min(int(payload.get("trials", 32)), 256))
    screening_trials = max(1, min(int(payload.get("screening_trials", _DEFAULT_SCREENING_TRIALS)), 16))
    target_def, target_mres, enemy_count, target = _target_from_payload(payload)
    raw_min_relevant_substats = payload.get("min_relevant_substats")
    if raw_min_relevant_substats in (None, ""):
        min_relevant_substats = None
    else:
        try:
            min_relevant_substats = max(0, min(int(raw_min_relevant_substats), 4))
        except (TypeError, ValueError) as error:
            raise HeroCoreError("min_relevant_substats must be an integer from 0 to 4") from error

    policy = str(payload.get("policy") or core.get("default_policy") or "")
    seed = int(payload.get("seed", 20260828))

    database = OptimizerEquipmentDatabase(database_path, percentile=0.60)
    try:
        database.initialize()
        database.clear_variant_overrides()
        projected_items = database.load_equipment()
        all_items, prefilter_report = prefilter_equipment(
            database,
            core,
            projected_items,
            min_relevant_substats=min_relevant_substats,
        )

        by_slot: dict[str, list[EquipmentItem]] = {slot: [] for slot in _SLOTS}
        for item in all_items:
            if item.slot.value in by_slot:
                by_slot[item.slot.value].append(item)
        missing = [slot for slot, values in by_slot.items() if not values]
        if missing:
            detail = ""
            if prefilter_report.get("policy_implemented"):
                threshold = prefilter_report.get("min_relevant_substats", 0)
                detail = (
                    f"；当前{prefilter_report.get('category')}初筛仅保留"
                    f"{'/'.join(prefilter_report.get('set_categories') or [])}类套装"
                    f"且要求至少{threshold}条相关副词条"
                )
            elif database.projection_exclusions:
                detail = "；部分装备因无法可靠投影到满级全解锁状态而被排除"
            raise HeroCoreError("初筛后缺少可用装备部位: " + ", ".join(missing) + detail)

        candidates, set_candidate_bonuses = _select_set_aware_candidates(
            database,
            by_slot,
            candidate_per_slot,
            recommendation_profile,
        )
        per_slot_cartesian = 1
        for values in candidates.values():
            per_slot_cartesian *= len(values)

        evolutions = load_set_evolutions(database)
        set_names = load_set_names(database)
        definitions = database.load_sets()
        effect_scores = _set_effect_scores(database, recommendation_profile)

        group_pruning_applied = bool(prefilter_report.get("policy_implemented"))
        left_limit = left_group_candidates if group_pruning_applied else 10**9
        right_limit = right_group_candidates if group_pruning_applied else 10**9

        left_builds, left_raw = _select_group_build_candidates(
            candidates,
            _LEFT_SLOT_ORDER,
            left_limit,
            definitions=definitions,
            effect_scores=effect_scores,
            evolutions=evolutions,
            set_names=set_names,
            recommendation_profile=recommendation_profile,
        )
        right_builds, right_raw = _select_group_build_candidates(
            candidates,
            _RIGHT_SLOT_ORDER,
            right_limit,
            definitions=definitions,
            effect_scores=effect_scores,
            evolutions=evolutions,
            set_names=set_names,
            recommendation_profile=recommendation_profile,
        )
        combinations = len(left_builds) * len(right_builds)

        def report(**update: Any) -> None:
            if progress_callback is not None:
                progress_callback(update)

        report(phase="screening", completed=0, total=combinations, overall_completed=0, overall_total=combinations)
        keep = max(top_k * 4, 16)
        screening_heap: list[tuple[float, str, tuple[str, ...]]] = []
        screening_warmup = max(0.0, float(payload.get("screening_warmup", 60.0)))
        screening_measurement = max(1.0, float(payload.get("screening_measurement", 240.0)))
        variant_evaluations_screened = 0

        for completed, (left_build, right_build) in enumerate(product(left_builds, right_builds), 1):
            physical_items = tuple(left_build + right_build)
            item_ids = tuple(item.item_id for item in physical_items)
            score, variant_count = _screen_physical_build(
                core=core,
                database=database,
                physical_items=physical_items,
                evolutions=evolutions,
                set_names=set_names,
                target=target,
                policy=policy,
                seed=seed,
                warmup=screening_warmup,
                measurement=screening_measurement,
                screening_trials=screening_trials,
                recommendation_profile=recommendation_profile,
            )
            variant_evaluations_screened += variant_count
            entry = (score, "|".join(item_ids), item_ids)
            if len(screening_heap) < keep:
                heapq.heappush(screening_heap, entry)
            elif entry > screening_heap[0]:
                heapq.heapreplace(screening_heap, entry)
            report(
                phase="screening",
                completed=completed,
                total=combinations,
                overall_completed=completed,
                overall_total=combinations,
            )

        shortlisted = sorted(screening_heap, reverse=True)
        refined: list[dict[str, Any]] = []
        report(
            phase="refining",
            completed=0,
            total=len(shortlisted),
            overall_completed=combinations,
            overall_total=combinations + len(shortlisted),
        )
        variant_evaluations_refined = 0
        for completed, (_, _, item_ids) in enumerate(shortlisted, 1):
            row, variant_count = _refine_physical_build(
                core=core,
                database=database,
                item_ids=item_ids,
                evolutions=evolutions,
                set_names=set_names,
                target=target,
                policy=policy,
                trials=refine_trials,
                warmup=float(payload.get("warmup", 120.0)),
                measurement=float(payload.get("measurement", 600.0)),
                seed=seed,
                recommendation_profile=recommendation_profile,
            )
            refined.append(row)
            variant_evaluations_refined += variant_count
            report(
                phase="refining",
                completed=completed,
                total=len(shortlisted),
                overall_completed=combinations + completed,
                overall_total=combinations + len(shortlisted),
            )

        refined.sort(
            key=lambda row: (float(row["role_score"]), "|".join(row["item_ids"])),
            reverse=True,
        )
        results = refined[:top_k]
        best = float(results[0]["role_score"]) if results else 0.0
        for rank, row in enumerate(results, 1):
            row["rank"] = rank
            score = float(row["role_score"])
            row["delta_vs_best"] = (best - score) / best if best else 0.0

        reduction = 1.0 - (combinations / per_slot_cartesian) if per_slot_cartesian else 0.0
        category = recommendation_profile.get("category", "output")
        return {
            "hero_core_id": core_id,
            "hero_name": core["hero"]["name"],
            "hero_role": core["hero"].get("role"),
            "recommendation_category": category,
            "recommendation_profile": recommendation_profile,
            "ranking_metric": "equivalent_60s_damage" if category == "output" else "role_score",
            "policy": policy,
            "target_def": target_def,
            "target_mres": target_mres,
            "enemy_count": enemy_count,
            "candidate_per_slot": candidate_per_slot,
            "min_relevant_substats": prefilter_report.get("min_relevant_substats"),
            "candidate_pruning": "set_aware",
            "candidate_protection": "baseline_plus_all_feasible_set_representatives",
            "search_strategy": "left_right_group_pruning" if group_pruning_applied else "set_aware_cartesian",
            "group_pruning_applied": group_pruning_applied,
            "equipment_prefilter": prefilter_report,
            "set_candidate_bonus_count": len(set_candidate_bonuses),
            "set_candidate_bonuses": dict(sorted(set_candidate_bonuses.items())),
            "per_slot_candidate_counts": {slot: len(candidates[slot]) for slot in _SLOTS},
            "pre_group_combinations": per_slot_cartesian,
            "left_group_candidates": {
                "raw": left_raw,
                "kept": len(left_builds),
                "limit": left_group_candidates if group_pruning_applied else left_raw,
            },
            "right_group_candidates": {
                "raw": right_raw,
                "kept": len(right_builds),
                "limit": right_group_candidates if group_pruning_applied else right_raw,
            },
            "group_pruning_reduction": reduction,
            "combinations_screened": combinations,
            "screening_trials": screening_trials if category == "output" else 1,
            "variant_simulations_screened": variant_evaluations_screened,
            "variant_simulations_refined": variant_evaluations_refined,
            "variant_evaluations_screened": variant_evaluations_screened,
            "variant_evaluations_refined": variant_evaluations_refined,
            "refine_trials": refine_trials if category == "output" else 1,
            "equipment_projection": {
                "mode": "max_enhancement_p60",
                "locked_substat_percentile": 0.60,
                "projected_item_count": len(database.projection_reports),
                "prefiltered_item_count": len(all_items),
                "excluded_item_count": len(database.projection_exclusions),
                "excluded_items": dict(sorted(database.projection_exclusions.items())),
            },
            "set_model": {
                "normalized_v22_effects": True,
                "dynamic_set_effects": True,
                "extra_damage": True,
                "penetration": True,
                "damage_instance_single_aoe": True,
                "t1_t2_variants": True,
                "tie_prefers_no_ascension": True,
                "cheap_group_scoring_includes_completed_sets": True,
                "mixed_t1_t2_candidate_reachability": True,
                "all_feasible_set_candidate_protection": True,
                "completed_set_group_protection": True,
                "role_aware_candidate_scoring": True,
                "recommendation_profile_objective": True,
                "multi_seed_screening": True,
            },
            "results": results,
        }
    finally:
        database.clear_variant_overrides()
        database.close()
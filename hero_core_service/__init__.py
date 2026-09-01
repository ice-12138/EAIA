"""Compatibility facade adding team allocation and complete-set-only recommendation.

The historical implementation remains in ../hero_core_service.py. A package
takes import precedence over the sibling module, so this facade can extend the
public API without duplicating or rewriting the stable legacy implementation.
"""
from __future__ import annotations

import heapq
import importlib.util
import sys
from itertools import product
from pathlib import Path
from typing import Any, Callable

from support_recommendation import (
    AUTO_UTILITY,
    evaluate_support_build,
    is_support_profile,
    normalize_support_recommendation_mode,
    resolve_support_profile,
    support_uses_simulation,
)

_LEGACY_PATH = Path(__file__).resolve().parent.parent / "hero_core_service.py"
_SPEC = importlib.util.spec_from_file_location("_eaia_hero_core_service_legacy", _LEGACY_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise ImportError(f"Unable to load legacy HeroCore service: {_LEGACY_PATH}")
_legacy = importlib.util.module_from_spec(_SPEC)
sys.modules.setdefault(_SPEC.name, _legacy)
_SPEC.loader.exec_module(_legacy)

hero_core_catalog = _legacy.hero_core_catalog
hero_core_detail = _legacy.hero_core_detail
hero_core_codex_payload = _legacy.hero_core_codex_payload
import_hero_core = _legacy.import_hero_core
simulate_hero_core = _legacy.simulate_hero_core

_SLOTS = _legacy._SLOTS
_LEFT_SLOT_ORDER = _legacy._LEFT_SLOT_ORDER
_RIGHT_SLOT_ORDER = _legacy._RIGHT_SLOT_ORDER
_DEFAULT_LEFT_GROUP_CANDIDATES = _legacy._DEFAULT_LEFT_GROUP_CANDIDATES
_DEFAULT_RIGHT_GROUP_CANDIDATES = _legacy._DEFAULT_RIGHT_GROUP_CANDIDATES
_DEFAULT_SCREENING_TRIALS = _legacy._DEFAULT_SCREENING_TRIALS

HeroCoreError = _legacy.HeroCoreError
EquipmentItem = _legacy.EquipmentItem
OptimizerEquipmentDatabase = _legacy.OptimizerEquipmentDatabase


def __getattr__(name: str):
    return getattr(_legacy, name)


def _group_is_complete_set(items, *, slot_order, expected_group, definitions) -> bool:
    if len(items) != len(slot_order):
        return False
    by_slot = {item.slot.value: item for item in items}
    if set(by_slot) != set(slot_order):
        return False
    set_ids = {item.set_id for item in items}
    if len(set_ids) != 1:
        return False
    definition = definitions.get(next(iter(set_ids)))
    if definition is None:
        return False
    if str(definition.slot_group or "").lower() != expected_group:
        return False
    return int(definition.required_pieces) == len(slot_order)


def _is_complete_set_variant(items, definitions) -> bool:
    by_slot = {item.slot.value: item for item in items}
    if set(by_slot) != set(_SLOTS):
        return False
    left = tuple(by_slot[slot] for slot in _LEFT_SLOT_ORDER)
    right = tuple(by_slot[slot] for slot in _RIGHT_SLOT_ORDER)
    return _group_is_complete_set(
        left, slot_order=_LEFT_SLOT_ORDER, expected_group="left", definitions=definitions
    ) and _group_is_complete_set(
        right, slot_order=_RIGHT_SLOT_ORDER, expected_group="right", definitions=definitions
    )


def _group_can_reach_complete_set(
    physical_items, *, slot_order, expected_group, definitions, evolutions, set_names
) -> bool:
    for variant_items, _ in _legacy.iter_ascension_variants(physical_items, evolutions, set_names):
        if _group_is_complete_set(
            tuple(variant_items),
            slot_order=slot_order,
            expected_group=expected_group,
            definitions=definitions,
        ):
            return True
    return False


def _support_evaluation_count(profile, requested_trials):
    return max(1, int(requested_trials)) if support_uses_simulation(profile) else 1


def _screen_physical_build(
    *, core, database, physical_items, evolutions, set_names, definitions,
    target, policy, seed, warmup, measurement, screening_trials,
    recommendation_profile, sets_only,
):
    best_score = float("-inf")
    best_ascensions = 10**9
    evaluations = 0
    category = recommendation_profile.get("category", "output")
    try:
        for variant_items, ascensions in _legacy.iter_ascension_variants(
            physical_items, evolutions, set_names
        ):
            if sets_only and not _is_complete_set_variant(tuple(variant_items), definitions):
                continue
            database.set_variant_overrides(_legacy._variant_overrides(physical_items, variant_items))
            if category == "output":
                scores = []
                for trial_index in range(max(1, int(screening_trials))):
                    trial = _legacy.HeroCoreSimulator(
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
            elif category == "support" and recommendation_profile.get("support_recommendation_mode"):
                requested_trials = _support_evaluation_count(recommendation_profile, screening_trials)
                evaluation = evaluate_support_build(
                    database,
                    core,
                    [item.item_id for item in physical_items],
                    recommendation_profile,
                    target=target,
                    policy=policy,
                    trials=requested_trials,
                    seed=seed,
                    seconds=60.0,
                )
                score = float(evaluation["role_score"])
                evaluations += requested_trials
            else:
                evaluation = _legacy.evaluate_role_build(
                    database, core, [item.item_id for item in physical_items], recommendation_profile
                )
                score = float(evaluation["role_score"])
                evaluations += 1
            if score > best_score or (score == best_score and len(ascensions) < best_ascensions):
                best_score, best_ascensions = score, len(ascensions)
    finally:
        database.clear_variant_overrides()
    return best_score, evaluations


def _refine_physical_build(
    *, core, database, item_ids, evolutions, set_names, definitions, target,
    policy, trials, warmup, measurement, seed, recommendation_profile, sets_only,
):
    database.clear_variant_overrides()
    physical_items = tuple(database.load_equipment(list(item_ids)))
    if len(physical_items) != len(item_ids):
        raise HeroCoreError("one or more shortlisted equipment items became unavailable")
    best_row = None
    best_key = None
    evaluations = 0
    category = recommendation_profile.get("category", "output")
    try:
        for variant_items, ascensions in _legacy.iter_ascension_variants(
            physical_items, evolutions, set_names
        ):
            if sets_only and not _is_complete_set_variant(tuple(variant_items), definitions):
                continue
            database.set_variant_overrides(_legacy._variant_overrides(physical_items, variant_items))
            active_sets = _legacy._active_set_ids(database, variant_items)
            serialized_ascensions = _legacy._serialize_ascensions(ascensions)
            common = {
                "item_ids": list(item_ids),
                "active_sets": active_sets,
                "active_set_names": [set_names.get(set_id, set_id) for set_id in active_sets],
                "ascended_items": serialized_ascensions,
                "upgrade_recommendations": serialized_ascensions,
                "uses_ascension": bool(ascensions),
                "final_set_states": _legacy._serialize_final_set_states(variant_items, set_names),
                "equipment_projection": database.projection_summary(item_ids),
                "sets_only": bool(sets_only),
            }
            if category == "output":
                result = _legacy.simulate_average(
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
                evaluations += max(1, int(trials))
            elif category == "support" and recommendation_profile.get("support_recommendation_mode"):
                requested_trials = _support_evaluation_count(recommendation_profile, trials)
                evaluation = evaluate_support_build(
                    database,
                    core,
                    list(item_ids),
                    recommendation_profile,
                    target=target,
                    policy=policy,
                    trials=requested_trials,
                    seed=seed,
                    seconds=60.0,
                )
                score = float(evaluation["role_score"])
                utility_std = float((evaluation.get("utility_60s") or {}).get("std", 0.0))
                row = {
                    **common,
                    **evaluation,
                    "equivalent_60s": {
                        "mean": score,
                        "std": utility_std,
                        "semantic": "support_role_score_compatibility_alias",
                    },
                }
                evaluations += requested_trials
            else:
                evaluation = _legacy.evaluate_role_build(
                    database, core, list(item_ids), recommendation_profile
                )
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
        if sets_only:
            raise HeroCoreError("仅完整套装模式下，该装备组合无法形成左2件+右3件完整套装")
        raise HeroCoreError("no equipment set variant could be evaluated")
    return best_row, evaluations


def _recommend_single(database_path, payload, progress_callback=None, *, top_k_override=None):
    core_id = str(payload.get("hero_core_id") or "SUN_WUKONG")
    core = _legacy.load_core(core_id)
    try:
        recommendation_profile = _legacy.resolve_recommendation_profile(core)
        support_mode = None
        if is_support_profile(recommendation_profile) and payload.get("support_recommendation_mode") not in (None, ""):
            support_mode = normalize_support_recommendation_mode(payload.get("support_recommendation_mode"))
            recommendation_profile = resolve_support_profile(core, recommendation_profile, support_mode)
    except (TypeError, ValueError) as error:
        raise HeroCoreError(f"recommendation_profile 无效: {error}") from error

    requested_top = top_k_override if top_k_override is not None else payload.get("top_k", 5)
    top_k = max(1, min(int(requested_top), 128 if top_k_override is not None else 20))
    candidate_per_slot = max(1, min(int(payload.get("candidate_per_slot", 5)), 8))
    left_group_candidates = max(1, min(int(payload.get(
        "left_group_candidates", _DEFAULT_LEFT_GROUP_CANDIDATES)), 64))
    right_group_candidates = max(1, min(int(payload.get(
        "right_group_candidates", _DEFAULT_RIGHT_GROUP_CANDIDATES)), 128))
    refine_trials = max(1, min(int(payload.get("trials", 32)), 256))
    screening_trials = max(1, min(int(payload.get(
        "screening_trials", _DEFAULT_SCREENING_TRIALS)), 16))
    target_def, target_mres, enemy_count, target = _legacy._target_from_payload(payload)
    raw_min = payload.get("min_relevant_substats")
    if raw_min in (None, ""):
        min_relevant_substats = None
    else:
        try:
            min_relevant_substats = max(0, min(int(raw_min), 4))
        except (TypeError, ValueError) as error:
            raise HeroCoreError("min_relevant_substats must be an integer from 0 to 4") from error
    policy = str(payload.get("policy") or core.get("default_policy") or "")
    seed = int(payload.get("seed", 20260828))
    sets_only = bool(payload.get("sets_only", False))
    excluded = {str(value) for value in (payload.get("exclude_item_ids") or []) if value}

    database = OptimizerEquipmentDatabase(database_path, percentile=0.60)
    try:
        database.initialize()
        database.clear_variant_overrides()
        projected_items = [item for item in database.load_equipment() if item.item_id not in excluded]
        prefilter_core = core
        if support_mode:
            prefilter_core = dict(core)
            prefilter_core["recommendation_profile"] = {
                **(core.get("recommendation_profile") or {}),
                **recommendation_profile,
            }
        all_items, prefilter_report = _legacy.prefilter_equipment(
            database, prefilter_core, projected_items, min_relevant_substats=min_relevant_substats
        )
        by_slot = {slot: [] for slot in _SLOTS}
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
            if excluded:
                detail += f"；另有{len(excluded)}件装备已被团队其他英雄占用"
            raise HeroCoreError("初筛后缺少可用装备部位: " + ", ".join(missing) + detail)

        candidates, set_candidate_bonuses = _legacy._select_set_aware_candidates(
            database, by_slot, candidate_per_slot, recommendation_profile
        )
        per_slot_cartesian = 1
        for values in candidates.values():
            per_slot_cartesian *= len(values)
        evolutions = _legacy.load_set_evolutions(database)
        set_names = _legacy.load_set_names(database)
        definitions = database.load_sets()
        effect_scores = _legacy._set_effect_scores(database, recommendation_profile)
        group_pruning_applied = bool(prefilter_report.get("policy_implemented"))
        left_limit = left_group_candidates if group_pruning_applied else 10**9
        right_limit = right_group_candidates if group_pruning_applied else 10**9
        left_builds, left_raw = _legacy._select_group_build_candidates(
            candidates, _LEFT_SLOT_ORDER, left_limit,
            definitions=definitions, effect_scores=effect_scores, evolutions=evolutions,
            set_names=set_names, recommendation_profile=recommendation_profile,
        )
        right_builds, right_raw = _legacy._select_group_build_candidates(
            candidates, _RIGHT_SLOT_ORDER, right_limit,
            definitions=definitions, effect_scores=effect_scores, evolutions=evolutions,
            set_names=set_names, recommendation_profile=recommendation_profile,
        )
        if sets_only:
            left_builds = [build for build in left_builds if _group_can_reach_complete_set(
                tuple(build), slot_order=_LEFT_SLOT_ORDER, expected_group="left",
                definitions=definitions, evolutions=evolutions, set_names=set_names)]
            right_builds = [build for build in right_builds if _group_can_reach_complete_set(
                tuple(build), slot_order=_RIGHT_SLOT_ORDER, expected_group="right",
                definitions=definitions, evolutions=evolutions, set_names=set_names)]
            if not left_builds:
                raise HeroCoreError("仅完整套装模式下没有可用的左侧2件套")
            if not right_builds:
                raise HeroCoreError("仅完整套装模式下没有可用的右侧3件套")
        combinations = len(left_builds) * len(right_builds)

        def report(**update):
            if progress_callback is not None:
                progress_callback(update)

        report(phase="screening", completed=0, total=combinations,
               overall_completed=0, overall_total=combinations)
        keep = max(top_k * (2 if top_k_override is not None else 4), 16)
        screening_heap = []
        screening_warmup = max(0.0, float(payload.get("screening_warmup", 60.0)))
        screening_measurement = max(1.0, float(payload.get("screening_measurement", 240.0)))
        variant_evaluations_screened = 0
        for completed, (left_build, right_build) in enumerate(product(left_builds, right_builds), 1):
            physical_items = tuple(left_build + right_build)
            item_ids = tuple(item.item_id for item in physical_items)
            score, variant_count = _screen_physical_build(
                core=core, database=database, physical_items=physical_items,
                evolutions=evolutions, set_names=set_names, definitions=definitions,
                target=target, policy=policy, seed=seed, warmup=screening_warmup,
                measurement=screening_measurement, screening_trials=screening_trials,
                recommendation_profile=recommendation_profile, sets_only=sets_only,
            )
            variant_evaluations_screened += variant_count
            if score != float("-inf"):
                entry = (score, "|".join(item_ids), item_ids)
                if len(screening_heap) < keep:
                    heapq.heappush(screening_heap, entry)
                elif entry > screening_heap[0]:
                    heapq.heapreplace(screening_heap, entry)
            report(phase="screening", completed=completed, total=combinations,
                   overall_completed=completed, overall_total=combinations)
        shortlisted = sorted(screening_heap, reverse=True)
        if not shortlisted:
            raise HeroCoreError("没有满足当前条件的装备组合" +
                                ("（已启用仅完整套装）" if sets_only else ""))
        refined = []
        report(phase="refining", completed=0, total=len(shortlisted),
               overall_completed=combinations, overall_total=combinations + len(shortlisted))
        variant_evaluations_refined = 0
        for completed, (_, _, item_ids) in enumerate(shortlisted, 1):
            row, variant_count = _refine_physical_build(
                core=core, database=database, item_ids=item_ids, evolutions=evolutions,
                set_names=set_names, definitions=definitions, target=target, policy=policy,
                trials=refine_trials, warmup=float(payload.get("warmup", 120.0)),
                measurement=float(payload.get("measurement", 600.0)), seed=seed,
                recommendation_profile=recommendation_profile, sets_only=sets_only,
            )
            refined.append(row)
            variant_evaluations_refined += variant_count
            report(phase="refining", completed=completed, total=len(shortlisted),
                   overall_completed=combinations + completed,
                   overall_total=combinations + len(shortlisted))
        refined.sort(key=lambda row: (float(row["role_score"]), "|".join(row["item_ids"])), reverse=True)
        results = refined[:top_k]
        best = float(results[0]["role_score"]) if results else 0.0
        for rank, row in enumerate(results, 1):
            row["rank"] = rank
            score = float(row["role_score"])
            row["delta_vs_best"] = (best - score) / best if best else 0.0
        reduction = 1.0 - (combinations / per_slot_cartesian) if per_slot_cartesian else 0.0
        category = recommendation_profile.get("category", "output")
        support_simulation = bool(support_mode and support_uses_simulation(recommendation_profile))
        if category == "output":
            ranking_metric = "equivalent_60s_damage"
        elif category == "support" and support_mode:
            ranking_metric = "support_auto_utility" if support_mode == AUTO_UTILITY else "support_manual_priority"
        else:
            ranking_metric = "role_score"
        return {
            "hero_core_id": core_id,
            "hero_name": core["hero"]["name"],
            "hero_role": core["hero"].get("role"),
            "recommendation_category": category,
            "recommendation_profile": recommendation_profile,
            "support_recommendation_mode": support_mode,
            "ranking_metric": ranking_metric,
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
            "sets_only": sets_only,
            "sets_only_semantic": "left_2_piece_and_right_3_piece_complete_sets" if sets_only else "disabled",
            "excluded_item_count": len(excluded),
            "equipment_prefilter": prefilter_report,
            "set_candidate_bonus_count": len(set_candidate_bonuses),
            "set_candidate_bonuses": dict(sorted(set_candidate_bonuses.items())),
            "per_slot_candidate_counts": {slot: len(candidates[slot]) for slot in _SLOTS},
            "pre_group_combinations": per_slot_cartesian,
            "left_group_candidates": {"raw": left_raw, "kept": len(left_builds),
                "limit": left_group_candidates if group_pruning_applied else left_raw},
            "right_group_candidates": {"raw": right_raw, "kept": len(right_builds),
                "limit": right_group_candidates if group_pruning_applied else right_raw},
            "group_pruning_reduction": reduction,
            "combinations_screened": combinations,
            "screening_trials": screening_trials if (category == "output" or support_simulation) else 1,
            "variant_simulations_screened": variant_evaluations_screened,
            "variant_simulations_refined": variant_evaluations_refined,
            "variant_evaluations_screened": variant_evaluations_screened,
            "variant_evaluations_refined": variant_evaluations_refined,
            "refine_trials": refine_trials if (category == "output" or support_simulation) else 1,
            "equipment_projection": {
                "mode": "max_enhancement_p60",
                "locked_substat_percentile": 0.60,
                "projected_item_count": len(database.projection_reports),
                "prefiltered_item_count": len(all_items),
                "excluded_item_count": len(database.projection_exclusions),
                "excluded_items": dict(sorted(database.projection_exclusions.items())),
            },
            "set_model": {"complete_set_constraint": sets_only},
            "results": results,
        }
    finally:
        database.clear_variant_overrides()
        database.close()


def _validate_team_heroes(hero_core_ids):
    if len(hero_core_ids) < 2:
        raise HeroCoreError("团队推荐至少需要选择2名英雄")
    if len(hero_core_ids) > 10:
        raise HeroCoreError("团队推荐当前最多支持10名英雄")
    if len(set(hero_core_ids)) != len(hero_core_ids):
        raise HeroCoreError("团队推荐不能重复选择同一名英雄")
    rows = []
    for core_id in hero_core_ids:
        core = _legacy.load_core(core_id)
        profile = _legacy.resolve_recommendation_profile(core)
        if profile.get("category") != "output":
            raise HeroCoreError(
                f"{core['hero']['name']} 当前不是输出类英雄；第一阶段团队推荐仅支持 output"
            )
        rows.append((core_id, core))
    return rows


def _solve_total_team(hero_order, candidate_rows):
    solve_order = sorted(hero_order, key=lambda hero_id: (
        len(candidate_rows[hero_id]), hero_order.index(hero_id)))
    optimistic = [0.0] * (len(solve_order) + 1)
    for index in range(len(solve_order) - 1, -1, -1):
        rows = candidate_rows[solve_order[index]]
        optimistic[index] = optimistic[index + 1] + (float(rows[0]["role_score"]) if rows else 0.0)
    best_score = float("-inf")
    best_choice = None

    def visit(index, used_items, score, choice):
        nonlocal best_score, best_choice
        if score + optimistic[index] <= best_score:
            return
        if index >= len(solve_order):
            if score > best_score:
                best_score = score
                best_choice = dict(choice)
            return
        hero_id = solve_order[index]
        for row in candidate_rows[hero_id]:
            item_ids = set(row["item_ids"])
            if item_ids & used_items:
                continue
            choice[hero_id] = row
            visit(index + 1, used_items | item_ids,
                  score + float(row["role_score"]), choice)
            choice.pop(hero_id, None)

    visit(0, set(), 0.0, {})
    if best_choice is None:
        raise HeroCoreError(
            "当前候选池中没有装备完全不重复的团队方案；可提高团队候选池或关闭仅完整套装"
        )
    return best_choice


def _team_payload_for_hero(payload, core_id, *, exclude_item_ids=None):
    result = {key: value for key, value in payload.items() if key not in {
        "hero_core_ids", "team_mode", "team", "team_candidate_pool", "policies", "hero_name"}}
    result["hero_core_id"] = core_id
    policies = payload.get("policies") or {}
    if isinstance(policies, dict):
        result["policy"] = str(policies.get(core_id) or "")
    else:
        result.pop("policy", None)
    if exclude_item_ids:
        result["exclude_item_ids"] = sorted(exclude_item_ids)
    else:
        result.pop("exclude_item_ids", None)
    return result


def _recommend_team(database_path, payload, progress_callback=None):
    hero_core_ids = [str(value) for value in (payload.get("hero_core_ids") or []) if value]
    hero_rows = _validate_team_heroes(hero_core_ids)
    team_mode = str(payload.get("team_mode") or "total").strip().lower()
    if team_mode not in {"ordered", "total"}:
        raise HeroCoreError("team_mode must be ordered or total")
    sets_only = bool(payload.get("sets_only", False))

    def report(phase, completed, total, **extra):
        if progress_callback is not None:
            progress_callback({"phase": phase, "completed": completed, "total": total,
                "overall_completed": completed, "overall_total": total, **extra})

    selected = {}
    candidate_counts = {}
    if team_mode == "ordered":
        used = set()
        report("screening", 0, len(hero_rows))
        for index, (core_id, _) in enumerate(hero_rows, 1):
            hero_payload = _team_payload_for_hero(payload, core_id, exclude_item_ids=used)
            hero_payload["top_k"] = 1
            result = _recommend_single(database_path, hero_payload, None, top_k_override=1)
            if not result["results"]:
                raise HeroCoreError(f"{result['hero_name']} 没有可用团队配装")
            row = result["results"][0]
            selected[core_id] = row
            used.update(row["item_ids"])
            candidate_counts[core_id] = 1
            report("refining", index, len(hero_rows), hero_core_id=core_id,
                   hero_name=result["hero_name"])
        search_strategy = "ordered_priority_with_inventory_exclusion"
    else:
        pool_size = max(4, min(int(payload.get("team_candidate_pool", 24)), 64))
        candidate_rows = {}
        report("screening", 0, len(hero_rows))
        for index, (core_id, core) in enumerate(hero_rows, 1):
            hero_payload = _team_payload_for_hero(payload, core_id)
            if "trials" not in hero_payload:
                hero_payload["trials"] = 16
            result = _recommend_single(database_path, hero_payload, None, top_k_override=pool_size)
            rows = list(result["results"])
            if not rows:
                raise HeroCoreError(f"{core['hero']['name']} 没有可用团队候选配装")
            candidate_rows[core_id] = rows
            candidate_counts[core_id] = len(rows)
            report("screening", index, len(hero_rows), hero_core_id=core_id,
                   hero_name=core["hero"]["name"])
        report("refining", 0, 1)
        selected = _solve_total_team(hero_core_ids, candidate_rows)
        report("refining", 1, 1)
        search_strategy = "candidate_pool_branch_and_bound"

    assignments = []
    used_items = set()
    team_total = 0.0
    for priority, (core_id, core) in enumerate(hero_rows, 1):
        row = selected[core_id]
        item_ids = set(row["item_ids"])
        conflict = used_items & item_ids
        if conflict:
            raise HeroCoreError("团队求解器产生了重复装备: " + ", ".join(sorted(conflict)))
        used_items.update(item_ids)
        score = float(row["role_score"])
        team_total += score
        assignments.append({
            "priority": priority,
            "hero_core_id": core_id,
            "hero_name": core["hero"]["name"],
            "hero_role": core["hero"].get("role"),
            "equivalent_60s": score,
            "build": row,
        })
    return {
        "team": True,
        "team_mode": team_mode,
        "sets_only": sets_only,
        "sets_only_semantic": "left_2_piece_and_right_3_piece_complete_sets" if sets_only else "disabled",
        "hero_count": len(assignments),
        "ranking_metric": "team_equivalent_60s_damage",
        "team_total_equivalent_60s": team_total,
        "equipment_conflicts": 0,
        "unique_item_count": len(used_items),
        "search_strategy": search_strategy,
        "candidate_pool_per_hero": candidate_counts,
        "heroes": assignments,
        "results": [],
    }


def recommend_hero_core(database_path, payload, progress_callback=None):
    if payload.get("team") or payload.get("hero_core_ids"):
        return _recommend_team(database_path, payload, progress_callback)
    if (
        not payload.get("sets_only")
        and not payload.get("exclude_item_ids")
        and not payload.get("support_recommendation_mode")
    ):
        return _legacy.recommend_hero_core(database_path, payload, progress_callback)
    return _recommend_single(database_path, payload, progress_callback)

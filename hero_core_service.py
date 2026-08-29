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
from equipment_models import EffectType, EquipmentItem, SetAscension, StatType
from equipment_recommendation_prefilter import prefilter_equipment
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
_LEFT_SLOTS = {"weapon", "armor"}
_RIGHT_SLOTS = {"bracelet", "necklace", "ring"}


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


def simulate_hero_core(database_path: str | Path, payload: dict[str, Any]) -> dict[str, Any]:
    core_id = str(payload.get("hero_core_id") or "SUN_WUKONG")
    core = load_core(core_id)
    item_ids = [str(value) for value in (payload.get("item_ids") or []) if value]
    if len(item_ids) > 5 or len(set(item_ids)) != len(item_ids):
        raise HeroCoreError("item_ids must contain at most five unique equipment ids")
    target_def = float(payload.get("target_def", 0.0))
    if target_def < 0:
        raise HeroCoreError("target_def must be non-negative")
    enemy_count = int(payload.get("enemy_count", 1))
    if enemy_count < 1:
        raise HeroCoreError("enemy_count must be at least 1")
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
            target={"defense": target_def, "control_immune": bool(payload.get("control_immune", True)), "enemy_count": enemy_count},
            policy=str(payload.get("policy") or core.get("default_policy") or ""),
            trials=trials,
            warmup=warmup,
            measurement=measurement,
            seed=int(payload.get("seed", 20260828)),
        )
    finally:
        database.close()


def _item_potential(item: EquipmentItem) -> float:
    score = 0.0
    for stat in item.stats:
        value = float(stat.stat_value)
        if stat.stat_type == StatType.ATK_PCT: score += value * 8.0
        elif stat.stat_type == StatType.ATK_FLAT: score += value / 900.0
        elif stat.stat_type == StatType.CRIT_RATE: score += value * 7.0
        elif stat.stat_type == StatType.CRIT_DMG: score += value * 3.2
        elif stat.stat_type == StatType.ATK_SPEED: score += value / 85.0
        elif stat.stat_type == StatType.RAGE_REGEN: score += value * 2.2
    return score


def _effect_potential(effect) -> float:
    weights = {
        EffectType.ATK_PCT: 8.0, EffectType.ATK_FLAT: 1.0 / 900.0,
        EffectType.CRIT_RATE: 7.0, EffectType.CRIT_DMG: 3.2,
        EffectType.ATK_SPEED: 1.0 / 85.0, EffectType.RAGE_REGEN: 2.2,
        EffectType.DAMAGE_PCT: 8.0, EffectType.BASIC_DMG: 6.0,
        EffectType.SKILL_DMG: 5.0, EffectType.ULT_DMG: 5.0,
        EffectType.SINGLE_DMG: 4.0, EffectType.AOE_DMG: 4.0,
        EffectType.EXTRA_DAMAGE: 2.0, EffectType.PENETRATION: 3.0,
    }
    return max(0.0, float(effect.value) * weights.get(effect.effect_type, 0.0))


def _eligible_slots(slot_group: str | None) -> set[str]:
    if slot_group == "left": return set(_LEFT_SLOTS)
    if slot_group == "right": return set(_RIGHT_SLOTS)
    return set(_SLOTS)


def _set_candidate_bonuses(database: OptimizerEquipmentDatabase, all_items: list[EquipmentItem]) -> dict[str, float]:
    definitions = database.load_sets()
    evolutions = load_set_evolutions(database)
    effect_scores: defaultdict[str, float] = defaultdict(float)
    for effect in database.load_set_effects():
        if not effect.requires_dot:
            effect_scores[effect.set_id] += _effect_potential(effect)
    slots_by_set: defaultdict[str, set[str]] = defaultdict(set)
    for item in all_items:
        slots_by_set[item.set_id].add(item.slot.value)
    result: dict[str, float] = {}
    for set_id, slots in slots_by_set.items():
        definition = definitions.get(set_id)
        if definition is None or len(slots & _eligible_slots(definition.slot_group)) < int(definition.required_pieces):
            continue
        target = evolutions.get(set_id)
        total_score = max(effect_scores.get(set_id, 0.0), effect_scores.get(target, 0.0) if target else 0.0)
        if total_score > 0:
            result[set_id] = total_score / max(1, int(definition.required_pieces))
    return result


def _select_set_aware_candidates(database: OptimizerEquipmentDatabase, by_slot: dict[str, list[EquipmentItem]], candidate_per_slot: int) -> tuple[dict[str, list[EquipmentItem]], dict[str, float]]:
    all_items = [item for values in by_slot.values() for item in values]
    bonuses = _set_candidate_bonuses(database, all_items)
    candidates: dict[str, list[EquipmentItem]] = {}
    for slot, values in by_slot.items():
        candidates[slot] = sorted(
            values,
            key=lambda item: (_item_potential(item) + bonuses.get(item.set_id, 0.0), _item_potential(item), item.item_id),
            reverse=True,
        )[:candidate_per_slot]
    return candidates, bonuses


def _variant_overrides(physical_items: tuple[EquipmentItem, ...], variant_items: tuple[EquipmentItem, ...]) -> dict[str, str]:
    return {p.item_id: v.set_id for p, v in zip(physical_items, variant_items) if p.set_id != v.set_id}


def _serialize_ascensions(ascensions: tuple[SetAscension, ...]) -> list[dict[str, str]]:
    return [{
        "item_id": row.item_id, "slot": row.slot,
        "from_set_id": row.from_set_id, "to_set_id": row.to_set_id,
        "from_set_name": row.from_set_name, "to_set_name": row.to_set_name,
    } for row in ascensions]


def _active_set_ids(database: OptimizerEquipmentDatabase, items: tuple[EquipmentItem, ...]) -> list[str]:
    definitions = database.load_sets()
    counts = Counter(item.set_id for item in items)
    return sorted(set_id for set_id, count in counts.items() if set_id in definitions and count >= definitions[set_id].required_pieces)


def _screen_physical_build(*, core, database, physical_items, evolutions, set_names, target, policy, seed, warmup, measurement) -> tuple[float, int]:
    best_score = float("-inf")
    best_ascensions = 10**9
    simulations = 0
    try:
        for variant_items, ascensions in iter_ascension_variants(physical_items, evolutions, set_names):
            database.set_variant_overrides(_variant_overrides(physical_items, variant_items))
            trial = HeroCoreSimulator(
                core, database=database, item_ids=[item.item_id for item in physical_items],
                target=target, policy=policy, seed=seed, warmup=warmup, measurement=measurement,
            ).run()
            simulations += 1
            score = trial.total_damage / measurement * 60.0
            if score > best_score or (score == best_score and len(ascensions) < best_ascensions):
                best_score, best_ascensions = score, len(ascensions)
    finally:
        database.clear_variant_overrides()
    return best_score, simulations


def _refine_physical_build(*, core, database, item_ids, evolutions, set_names, target, policy, trials, warmup, measurement, seed) -> tuple[dict[str, Any], int]:
    database.clear_variant_overrides()
    physical_items = tuple(database.load_equipment(list(item_ids)))
    if len(physical_items) != len(item_ids):
        raise HeroCoreError("one or more shortlisted equipment items became unavailable")
    best_row = None
    best_key = None
    simulations = 0
    try:
        for variant_items, ascensions in iter_ascension_variants(physical_items, evolutions, set_names):
            database.set_variant_overrides(_variant_overrides(physical_items, variant_items))
            result = simulate_average(
                core, database=database, item_ids=list(item_ids), target=target, policy=policy,
                trials=trials, warmup=warmup, measurement=measurement, seed=seed,
            )
            simulations += 1
            active_sets = _active_set_ids(database, variant_items)
            row = {
                "item_ids": list(item_ids),
                "active_sets": active_sets,
                "active_set_names": [set_names.get(set_id, set_id) for set_id in active_sets],
                "ascended_items": _serialize_ascensions(ascensions),
                "uses_ascension": bool(ascensions),
                "equipment_projection": database.projection_summary(item_ids),
                **result,
            }
            mean = float(result["equivalent_60s"]["mean"])
            key = (mean, -len(ascensions), "|".join(item.set_id for item in variant_items))
            if best_key is None or key > best_key:
                best_key, best_row = key, row
    finally:
        database.clear_variant_overrides()
    if best_row is None:
        raise HeroCoreError("no equipment set variant could be simulated")
    return best_row, simulations


def recommend_hero_core(database_path: str | Path, payload: dict[str, Any], progress_callback: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    """Role-prefiltered, set-aware HeroCore recommendation search.

    Output heroes are reduced before any combinatorial search: only output sets
    are retained and every item must have at least two output-relevant substats.
    Tank/healing/support categories are classified but currently pass through so
    their future policies can be added without changing the search engine.
    """
    core_id = str(payload.get("hero_core_id") or "SUN_WUKONG")
    core = load_core(core_id)
    top_k = max(1, min(int(payload.get("top_k", 5)), 20))
    candidate_per_slot = max(1, min(int(payload.get("candidate_per_slot", 5)), 8))
    refine_trials = max(1, min(int(payload.get("trials", 32)), 256))
    target_def = max(0.0, float(payload.get("target_def", 0.0)))
    enemy_count = max(1, int(payload.get("enemy_count", 1)))
    policy = str(payload.get("policy") or core.get("default_policy") or "")
    seed = int(payload.get("seed", 20260828))
    target = {"defense": target_def, "control_immune": bool(payload.get("control_immune", True)), "enemy_count": enemy_count}

    database = OptimizerEquipmentDatabase(database_path, percentile=0.90)
    try:
        database.initialize()
        database.clear_variant_overrides()
        projected_items = database.load_equipment()
        all_items, prefilter_report = prefilter_equipment(database, core, projected_items)

        by_slot: dict[str, list[EquipmentItem]] = {slot: [] for slot in _SLOTS}
        for item in all_items:
            if item.slot.value in by_slot:
                by_slot[item.slot.value].append(item)
        missing = [slot for slot, values in by_slot.items() if not values]
        if missing:
            detail = ""
            if prefilter_report.get("policy_implemented"):
                detail = "；当前输出初筛仅保留输出类套装且要求至少2条输出相关副词条"
            elif database.projection_exclusions:
                detail = "；部分装备因无法可靠投影到满级全解锁状态而被排除"
            raise HeroCoreError("初筛后缺少可用装备部位: " + ", ".join(missing) + detail)

        candidates, set_candidate_bonuses = _select_set_aware_candidates(database, by_slot, candidate_per_slot)
        combinations = 1
        for values in candidates.values(): combinations *= len(values)
        evolutions = load_set_evolutions(database)
        set_names = load_set_names(database)

        def report(**update: Any) -> None:
            if progress_callback is not None: progress_callback(update)

        report(phase="screening", completed=0, total=combinations, overall_completed=0, overall_total=combinations)
        keep = max(top_k * 4, 16)
        screening_heap: list[tuple[float, str, tuple[str, ...]]] = []
        screening_warmup = max(0.0, float(payload.get("screening_warmup", 60.0)))
        screening_measurement = max(1.0, float(payload.get("screening_measurement", 240.0)))
        variant_simulations_screened = 0
        for completed, build in enumerate(product(*(candidates[slot] for slot in _SLOTS)), 1):
            physical_items = tuple(build)
            item_ids = tuple(item.item_id for item in physical_items)
            score, variant_count = _screen_physical_build(
                core=core, database=database, physical_items=physical_items,
                evolutions=evolutions, set_names=set_names, target=target, policy=policy,
                seed=seed, warmup=screening_warmup, measurement=screening_measurement,
            )
            variant_simulations_screened += variant_count
            entry = (score, "|".join(item_ids), item_ids)
            if len(screening_heap) < keep: heapq.heappush(screening_heap, entry)
            elif entry > screening_heap[0]: heapq.heapreplace(screening_heap, entry)
            report(phase="screening", completed=completed, total=combinations, overall_completed=completed, overall_total=combinations)

        shortlisted = sorted(screening_heap, reverse=True)
        refined: list[dict[str, Any]] = []
        report(phase="refining", completed=0, total=len(shortlisted), overall_completed=combinations, overall_total=combinations + len(shortlisted))
        variant_simulations_refined = 0
        for completed, (_, _, item_ids) in enumerate(shortlisted, 1):
            row, variant_count = _refine_physical_build(
                core=core, database=database, item_ids=item_ids, evolutions=evolutions,
                set_names=set_names, target=target, policy=policy, trials=refine_trials,
                warmup=float(payload.get("warmup", 120.0)), measurement=float(payload.get("measurement", 600.0)), seed=seed,
            )
            refined.append(row)
            variant_simulations_refined += variant_count
            report(phase="refining", completed=completed, total=len(shortlisted), overall_completed=combinations + completed, overall_total=combinations + len(shortlisted))

        refined.sort(key=lambda row: (float(row["equivalent_60s"]["mean"]), "|".join(row["item_ids"])), reverse=True)
        results = refined[:top_k]
        best = float(results[0]["equivalent_60s"]["mean"]) if results else 0.0
        for rank, row in enumerate(results, 1):
            row["rank"] = rank
            score = float(row["equivalent_60s"]["mean"])
            row["delta_vs_best"] = (best - score) / best if best else 0.0

        return {
            "hero_core_id": core_id,
            "hero_name": core["hero"]["name"],
            "hero_role": core["hero"].get("role"),
            "policy": policy,
            "target_def": target_def,
            "enemy_count": enemy_count,
            "candidate_per_slot": candidate_per_slot,
            "candidate_pruning": "set_aware",
            "equipment_prefilter": prefilter_report,
            "set_candidate_bonus_count": len(set_candidate_bonuses),
            "combinations_screened": combinations,
            "variant_simulations_screened": variant_simulations_screened,
            "variant_simulations_refined": variant_simulations_refined,
            "refine_trials": refine_trials,
            "equipment_projection": {
                "mode": "max_enhancement_p90",
                "locked_substat_percentile": 0.90,
                "projected_item_count": len(database.projection_reports),
                "prefiltered_item_count": len(all_items),
                "excluded_item_count": len(database.projection_exclusions),
                "excluded_items": dict(sorted(database.projection_exclusions.items())),
            },
            "set_model": {"normalized_v22_effects": True, "t1_t2_variants": True, "tie_prefers_no_ascension": True},
            "results": results,
        }
    finally:
        database.clear_variant_overrides()
        database.close()

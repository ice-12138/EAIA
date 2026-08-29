"""Role-aware equipment prefilter for HeroCore recommendations."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from equipment_models import EquipmentItem
from equipment_recommendation_profile import resolve_recommendation_profile, relevant_stat_types
from equipment_set_variants import load_set_evolutions


class RecommendationCategory(StrEnum):
    OUTPUT = "output"
    TANK = "tank"
    HEALING = "healing"
    SUPPORT = "support"
    UNCLASSIFIED = "unclassified"


@dataclass(frozen=True)
class PrefilterPolicy:
    category: RecommendationCategory
    implemented: bool
    set_categories: tuple[str, ...] = ()
    stat_category: str | None = None
    min_relevance_weight: float = 0.0
    min_relevant_substats: int = 0


_OUTPUT_ROLES = {"战士", "法师", "射手", "fighter", "mage", "marksman", "ranger"}
_TANK_ROLES = {"守护者", "defender", "guardian", "tank"}
_HEALING_ROLES = {"医师", "healer", "medic"}
_SUPPORT_ROLES = {"战术大师", "support", "tactician", "tactical master"}


def classify_recommendation_category(core: dict[str, Any]) -> RecommendationCategory:
    explicit = (
        (core.get("recommendation_profile") or {}).get("category")
        or (core.get("hero") or {}).get("equipment_category")
    )
    if explicit:
        try:
            return RecommendationCategory(str(explicit).strip().lower())
        except ValueError:
            return RecommendationCategory.UNCLASSIFIED

    role = str((core.get("hero") or {}).get("role") or "").strip()
    folded = role.casefold()
    if role in _OUTPUT_ROLES or folded in _OUTPUT_ROLES:
        return RecommendationCategory.OUTPUT
    if role in _TANK_ROLES or folded in _TANK_ROLES:
        return RecommendationCategory.TANK
    if role in _HEALING_ROLES or folded in _HEALING_ROLES:
        return RecommendationCategory.HEALING
    if role in _SUPPORT_ROLES or folded in _SUPPORT_ROLES:
        return RecommendationCategory.SUPPORT

    # Keep prefilter semantics aligned with resolve_recommendation_profile().
    # HeroCore files whose role metadata is missing/unknown are treated as
    # output by the recommendation objective; previously the prefilter called
    # them ``unclassified`` and disabled both filtering and group pruning. A
    # single incomplete role field (Valkyra is one current example) could then
    # expand a normal search into hundreds of thousands of simulations.
    return RecommendationCategory.OUTPUT


def policy_for(category: RecommendationCategory, core: dict[str, Any] | None = None) -> PrefilterPolicy:
    if category == RecommendationCategory.UNCLASSIFIED:
        return PrefilterPolicy(category, False)
    profile = resolve_recommendation_profile(core or {"recommendation_profile": {"category": category.value}})
    return PrefilterPolicy(
        category=category,
        implemented=True,
        set_categories=tuple(profile.get("set_categories") or ()),
        stat_category=str(profile.get("stat_category") or category.value),
        min_relevance_weight=float(profile.get("min_relevance_weight", 0.60)),
        min_relevant_substats=int(profile.get("min_relevant_substats", 0)),
    )


def _set_category_rows(database) -> dict[str, tuple[str | None, bool, bool]]:
    columns = {row[1] for row in database.connection.execute("PRAGMA table_info(sets)").fetchall()}
    category_expr = "category_id" if "category_id" in columns else "NULL"
    active_expr = "active" if "active" in columns else "1"
    rows = database.connection.execute(
        f"SELECT set_id,{category_expr} AS category_id,{active_expr} AS active,output_set FROM sets"
    ).fetchall()
    return {
        str(row["set_id"]): (
            None if row["category_id"] is None else str(row["category_id"]).lower(),
            bool(row["active"]),
            bool(row["output_set"]),
        )
        for row in rows
    }


def _allowed_sets(database, policy: PrefilterPolicy) -> set[str]:
    rows = _set_category_rows(database)
    allowed = {
        set_id
        for set_id, (category, active, legacy_output) in rows.items()
        if active and (
            category in policy.set_categories
            or (category is None and policy.category == RecommendationCategory.OUTPUT and legacy_output)
        )
    }
    for source, target in load_set_evolutions(database).items():
        if target in allowed:
            allowed.add(source)
    return allowed


def _relevant_stat_types(database, policy: PrefilterPolicy, profile: dict[str, Any]) -> set[str]:
    values = set(relevant_stat_types(profile))
    if policy.stat_category:
        try:
            rows = database.connection.execute(
                """SELECT stat_type,relevance_weight FROM stat_category_map
                   WHERE category_id=? AND relevance_weight>=?""",
                (policy.stat_category, policy.min_relevance_weight),
            ).fetchall()
            values.update(str(row["stat_type"]).upper() for row in rows)
        except Exception:
            pass
    return values


def _substat_count(item: EquipmentItem, relevant: set[str]) -> int:
    return sum(
        1 for stat in item.stats
        if str(stat.stat_source).lower() == "sub" and stat.stat_type.value.upper() in relevant
    )


def _normalize_min_relevant_substats(value: int | None, default: int) -> int:
    if value is None:
        return max(0, min(int(default), 4))
    return max(0, min(int(value), 4))


def prefilter_equipment(
    database,
    core: dict[str, Any],
    items: list[EquipmentItem],
    *,
    min_relevant_substats: int | None = None,
) -> tuple[list[EquipmentItem], dict[str, Any]]:
    """Filter by role-compatible set categories and profile-relevant substats."""
    category = classify_recommendation_category(core)
    before_by_slot = Counter(item.slot.value for item in items)
    requested_min = None if min_relevant_substats is None else max(0, min(int(min_relevant_substats), 4))

    if category == RecommendationCategory.UNCLASSIFIED:
        return list(items), {
            "category": category.value,
            "hero_role": (core.get("hero") or {}).get("role"),
            "policy_implemented": False,
            "strategy": "unclassified_passthrough",
            "requested_min_relevant_substats": requested_min,
            "min_relevant_substats": None,
            "input_item_count": len(items),
            "kept_item_count": len(items),
            "removed_item_count": 0,
            "before_by_slot": dict(sorted(before_by_slot.items())),
            "after_by_slot": dict(sorted(before_by_slot.items())),
            "removed_by_reason": {},
        }

    profile = resolve_recommendation_profile(core)
    policy = policy_for(category, core)
    effective_min = _normalize_min_relevant_substats(min_relevant_substats, policy.min_relevant_substats)
    allowed_sets = _allowed_sets(database, policy)
    relevant_stats = _relevant_stat_types(database, policy, profile)

    kept: list[EquipmentItem] = []
    removed = Counter()
    detail = defaultdict(list)
    for item in items:
        if item.set_id not in allowed_sets:
            reason = f"non_{category.value}_set"
            removed[reason] += 1
            detail[reason].append(item.item_id)
            continue
        count = _substat_count(item, relevant_stats)
        if count < effective_min:
            reason = f"insufficient_{category.value}_substats"
            removed[reason] += 1
            detail[reason].append(item.item_id)
            continue
        kept.append(item)

    after_by_slot = Counter(item.slot.value for item in kept)
    return kept, {
        "category": category.value,
        "hero_role": (core.get("hero") or {}).get("role"),
        "policy_implemented": True,
        "strategy": "profile_sets_then_min_relevant_substats",
        "archetype": profile.get("archetype"),
        "primary_scaling_stat": profile.get("primary_scaling_stat"),
        "set_categories": list(policy.set_categories),
        "requested_min_relevant_substats": requested_min,
        "min_relevant_substats": effective_min,
        "default_min_relevant_substats": policy.min_relevant_substats,
        "stat_category": policy.stat_category,
        "min_relevance_weight": policy.min_relevance_weight,
        "relevant_stat_types": sorted(relevant_stats),
        "allowed_set_count": len(allowed_sets),
        "input_item_count": len(items),
        "kept_item_count": len(kept),
        "removed_item_count": len(items) - len(kept),
        "before_by_slot": dict(sorted(before_by_slot.items())),
        "after_by_slot": dict(sorted(after_by_slot.items())),
        "removed_by_reason": dict(sorted(removed.items())),
        "removed_item_ids": {key: sorted(value) for key, value in sorted(detail.items())},
        "recommendation_profile": profile,
    }

"""Fast role-aware equipment prefilter for HeroCore recommendations.

Only the output policy is enabled today. Tank/healing/support policies are
classified and exposed as extension points, but intentionally remain pass-through
until their game-specific rules are defined.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from equipment_models import EquipmentItem
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


_POLICIES = {
    RecommendationCategory.OUTPUT: PrefilterPolicy(
        RecommendationCategory.OUTPUT, True, ("output",), "output", 0.60, 2
    ),
    # Reserved interfaces. Do not guess these rules before the user defines them.
    RecommendationCategory.TANK: PrefilterPolicy(RecommendationCategory.TANK, False, ("defense",)),
    RecommendationCategory.HEALING: PrefilterPolicy(RecommendationCategory.HEALING, False, ("healing",)),
    RecommendationCategory.SUPPORT: PrefilterPolicy(RecommendationCategory.SUPPORT, False, ("buff",)),
    RecommendationCategory.UNCLASSIFIED: PrefilterPolicy(RecommendationCategory.UNCLASSIFIED, False),
}

_OUTPUT_ROLES = {"战士", "法师", "射手", "fighter", "mage", "marksman", "ranger"}
_TANK_ROLES = {"守护者", "defender", "guardian", "tank"}
_HEALING_ROLES = {"医师", "healer", "medic"}
_SUPPORT_ROLES = {"战术大师", "support", "tactician", "tactical master"}


def classify_recommendation_category(core: dict[str, Any]) -> RecommendationCategory:
    """Classify a HeroCore into an equipment recommendation category.

    An explicit ``recommendation_profile.category`` or ``hero.equipment_category``
    always wins. This is the stable interface for future tank/healer/support
    implementations. Role mapping is only a fallback.
    """
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
    return RecommendationCategory.UNCLASSIFIED


def policy_for(category: RecommendationCategory) -> PrefilterPolicy:
    return _POLICIES[category]


def _set_category_rows(database) -> dict[str, tuple[str | None, bool]]:
    columns = {row[1] for row in database.connection.execute("PRAGMA table_info(sets)").fetchall()}
    category_expr = "category_id" if "category_id" in columns else "NULL"
    active_expr = "active" if "active" in columns else "1"
    rows = database.connection.execute(
        f"SELECT set_id,{category_expr} AS category_id,{active_expr} AS active,output_set FROM sets"
    ).fetchall()
    return {
        str(row["set_id"]): (
            None if row["category_id"] is None else str(row["category_id"]).lower(),
            bool(row["active"]) and bool(row["output_set"]),
        )
        for row in rows
    }


def _allowed_sets(database, policy: PrefilterPolicy) -> set[str]:
    rows = _set_category_rows(database)
    allowed = {
        set_id
        for set_id, (category, legacy_output) in rows.items()
        if category in policy.set_categories
        or (category is None and policy.category == RecommendationCategory.OUTPUT and legacy_output)
    }
    # Keep an evolvable T1 source if its reachable T2 belongs to the same allowed
    # category. This protects T1 -> T2 recommendation without admitting unrelated sets.
    for source, target in load_set_evolutions(database).items():
        if target in allowed:
            allowed.add(source)
    return allowed


def _relevant_stat_types(database, policy: PrefilterPolicy) -> set[str]:
    if not policy.stat_category:
        return set()
    try:
        rows = database.connection.execute(
            """SELECT stat_type,relevance_weight FROM stat_category_map
               WHERE category_id=? AND relevance_weight>=?""",
            (policy.stat_category, policy.min_relevance_weight),
        ).fetchall()
        values = {str(row["stat_type"]).upper() for row in rows}
        if values:
            return values
    except Exception:
        pass
    # Defensive fallback matching the current output dictionary semantics.
    if policy.category == RecommendationCategory.OUTPUT:
        return {"ATK_FLAT", "ATK_PCT", "CRIT_RATE", "CRIT_DMG", "ATK_SPEED", "RAGE_REGEN"}
    return set()


def _substat_count(item: EquipmentItem, relevant: set[str]) -> int:
    return sum(
        1 for stat in item.stats
        if str(stat.stat_source).lower() == "sub" and stat.stat_type.value.upper() in relevant
    )


def prefilter_equipment(
    database,
    core: dict[str, Any],
    items: list[EquipmentItem],
) -> tuple[list[EquipmentItem], dict[str, Any]]:
    """Apply the fast role-specific prefilter before candidate ranking/simulation."""
    category = classify_recommendation_category(core)
    policy = policy_for(category)
    before_by_slot = Counter(item.slot.value for item in items)

    if not policy.implemented:
        report = {
            "category": category.value,
            "hero_role": (core.get("hero") or {}).get("role"),
            "policy_implemented": False,
            "strategy": "reserved_passthrough",
            "input_item_count": len(items),
            "kept_item_count": len(items),
            "removed_item_count": 0,
            "before_by_slot": dict(sorted(before_by_slot.items())),
            "after_by_slot": dict(sorted(before_by_slot.items())),
            "removed_by_reason": {},
            "reserved_categories": ["tank", "healing", "support"],
        }
        return list(items), report

    allowed_sets = _allowed_sets(database, policy)
    relevant_stats = _relevant_stat_types(database, policy)
    kept: list[EquipmentItem] = []
    removed = Counter()
    detail = defaultdict(list)
    for item in items:
        if item.set_id not in allowed_sets:
            removed["non_output_set"] += 1
            detail["non_output_set"].append(item.item_id)
            continue
        count = _substat_count(item, relevant_stats)
        if count < policy.min_relevant_substats:
            removed["insufficient_output_substats"] += 1
            detail["insufficient_output_substats"].append(item.item_id)
            continue
        kept.append(item)

    after_by_slot = Counter(item.slot.value for item in kept)
    report = {
        "category": category.value,
        "hero_role": (core.get("hero") or {}).get("role"),
        "policy_implemented": True,
        "strategy": "output_sets_then_min_output_substats",
        "set_categories": list(policy.set_categories),
        "min_relevant_substats": policy.min_relevant_substats,
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
        "reserved_categories": ["tank", "healing", "support"],
    }
    return kept, report

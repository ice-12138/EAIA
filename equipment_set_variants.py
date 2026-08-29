"""Set-effect normalization and T1 -> T2 ascension variant expansion.

The game keeps an equipment item's rolled stats when it is ascended, while its
set identity/effect changes. The optimizer therefore treats an evolvable T1
item as one physical item with two calculation states. Final recommendations
can then keep the better state without duplicating the same physical build.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from itertools import product
from typing import Iterable

from equipment_models import EffectType, EquipmentItem, SetAscension, SetEffect


_TRIGGER_ALIASES = {
    "on_ultimate": "on_ult",
}

_DAMAGE_STAT_TYPES = {
    "damage_bonus": EffectType.DAMAGE_PCT,
    "basic_damage_bonus": EffectType.BASIC_DMG,
    "skill_damage_bonus": EffectType.SKILL_DMG,
    "ultimate_damage_bonus": EffectType.ULT_DMG,
    "single_damage_bonus": EffectType.SINGLE_DMG,
    "aoe_damage_bonus": EffectType.AOE_DMG,
}

_STAT_EFFECT_TYPES = {
    "atk_flat": EffectType.ATK_FLAT,
    "atk_pct": EffectType.ATK_PCT,
    "hp_flat": EffectType.HP_FLAT,
    "hp_pct": EffectType.HP_PCT,
    "def_flat": EffectType.DEF_FLAT,
    "def_pct": EffectType.DEF_PCT,
    "crit_rate": EffectType.CRIT_RATE,
    "crit_dmg": EffectType.CRIT_DMG,
    "atk_speed": EffectType.ATK_SPEED,
    "rage_regen": EffectType.RAGE_REGEN,
    "healing_effect": EffectType.HEALING_EFFECT,
    "penetration": EffectType.PENETRATION,
}


def _row_value(row, key: str, default=None):
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


def load_set_evolutions(database) -> dict[str, str]:
    """Return all configured T1 -> T2 set mappings."""
    try:
        rows = database.connection.execute(
            "SELECT from_set_id, to_set_id FROM set_evolutions ORDER BY from_set_id, to_set_id"
        ).fetchall()
    except Exception:
        return {}
    return {str(row["from_set_id"]): str(row["to_set_id"]) for row in rows}


def load_set_names(database) -> dict[str, str]:
    return {
        str(row["set_id"]): str(row["set_name"])
        for row in database.connection.execute("SELECT set_id, set_name FROM sets")
    }


def _effect_enabled(row) -> bool:
    """Legacy V1.1/classic-optimizer enable flag."""
    keys = set(row.keys()) if hasattr(row, "keys") else set()
    if "enabled_in_optimizer" in keys and _row_value(row, "enabled_in_optimizer") is not None:
        return bool(_row_value(row, "enabled_in_optimizer"))
    if "enabled_in_v1_1" in keys and _row_value(row, "enabled_in_v1_1") is not None:
        return bool(_row_value(row, "enabled_in_v1_1"))
    return True


def _normalize_effect_type(row) -> EffectType | None:
    raw_effect = str(_row_value(row, "effect_type", "") or "").strip()
    raw_stat = str(_row_value(row, "stat_type", "") or "").strip().lower()

    try:
        return EffectType(raw_effect.upper())
    except ValueError:
        pass

    raw_effect_lower = raw_effect.lower()
    if raw_effect_lower == "stat_mod":
        return _STAT_EFFECT_TYPES.get(raw_stat)
    if raw_effect_lower == "damage_mult":
        return _DAMAGE_STAT_TYPES.get(raw_stat)
    if raw_effect_lower == "extra_damage":
        return EffectType.EXTRA_DAMAGE
    return None


def _normalize_trigger(row, effect_type: EffectType) -> str:
    trigger = str(_row_value(row, "trigger", "always") or "always").strip().lower()
    trigger = _TRIGGER_ALIASES.get(trigger, trigger)
    duration = float(_row_value(row, "duration", 0) or 0)
    condition = _row_value(row, "condition")

    if not condition and trigger in {"passive", "while_deployed"}:
        return "always"
    if not condition and trigger == "on_deploy" and duration <= 0:
        return "always"
    return trigger


def _load_normalized_set_effects(database, *, respect_legacy_enable_flags: bool) -> list[SetEffect]:
    rows = database.connection.execute("SELECT * FROM set_effects ORDER BY set_id, effect_id").fetchall()
    result: list[SetEffect] = []
    for row in rows:
        if respect_legacy_enable_flags and not _effect_enabled(row):
            continue
        effect_type = _normalize_effect_type(row)
        if effect_type is None:
            continue
        trigger = _normalize_trigger(row, effect_type)
        result.append(
            SetEffect(
                set_id=str(_row_value(row, "set_id")),
                effect_id=str(_row_value(row, "effect_id")),
                effect_type=effect_type,
                value=float(_row_value(row, "value", 0) or 0),
                applies_to=str(_row_value(row, "applies_to", "all") or "all"),
                trigger=trigger,
                duration=(None if _row_value(row, "duration") is None else float(_row_value(row, "duration") or 0)),
                max_stacks=max(1, int(_row_value(row, "max_stacks", 1) or 1)),
                stack_rule=str(_row_value(row, "stack_rule", "add") or "add"),
                proc_chance=float(_row_value(row, "proc_chance", 1) or 0),
                internal_cd=float(_row_value(row, "internal_cd", 0) or 0),
                condition=_row_value(row, "condition"),
                approximate=bool(_row_value(row, "approximate", 0)),
                requires_dot=bool(_row_value(row, "requires_dot", 0)),
                enabled_in_v1_1=True,
            )
        )
    return result


def load_legacy_optimizer_set_effects(database) -> list[SetEffect]:
    """Normalize only rows enabled for the historical V1.1 simulator."""
    return _load_normalized_set_effects(database, respect_legacy_enable_flags=True)


def load_optimizer_set_effects(database) -> list[SetEffect]:
    """Return all semantically normalizable current set effects.

    Historical ``enabled_in_optimizer``/``enabled_in_v1_1`` flags described
    implementation gaps in the old simulator; they are not game-state flags.
    The current HeroCore recommendation path needs the complete semantic catalog
    so effects such as Insight's fixed extra damage and Fatality penetration are
    not silently removed before simulation.
    """
    return _load_normalized_set_effects(database, respect_legacy_enable_flags=False)


def load_hero_core_set_effects(database) -> list[SetEffect]:
    """Alias documenting the full-catalog HeroCore intent."""
    return load_optimizer_set_effects(database)


def iter_ascension_variants(
    items: tuple[EquipmentItem, ...],
    evolutions: dict[str, str],
    set_names: dict[str, str] | None = None,
) -> Iterable[tuple[tuple[EquipmentItem, ...], tuple[SetAscension, ...]]]:
    """Yield mechanically distinct current/T2 states for one physical build.

    Items of the same source set are grouped. Because ascension does not alter
    rolled stats, ascending any N pieces of the same source set has identical
    combat math; only the source/target set piece counts matter. Therefore we
    evaluate N=0..K rather than all 2**K permutations.
    """
    set_names = set_names or {}
    groups: dict[tuple[str, str], list[int]] = defaultdict(list)
    for index, item in enumerate(items):
        target = evolutions.get(item.set_id)
        if target:
            groups[(item.set_id, target)].append(index)

    if not groups:
        yield items, ()
        return

    ordered_groups = sorted(groups.items(), key=lambda entry: entry[0])
    ranges = [range(len(indices) + 1) for _, indices in ordered_groups]
    seen_states: set[tuple[str, ...]] = set()

    for counts in product(*ranges):
        variant = list(items)
        ascensions: list[SetAscension] = []
        for ((from_set, to_set), indices), count in zip(ordered_groups, counts):
            for index in indices[:count]:
                original = variant[index]
                variant[index] = replace(original, set_id=to_set)
                ascensions.append(
                    SetAscension(
                        item_id=original.item_id,
                        slot=original.slot.value,
                        from_set_id=from_set,
                        to_set_id=to_set,
                        from_set_name=set_names.get(from_set, from_set),
                        to_set_name=set_names.get(to_set, to_set),
                    )
                )
        state = tuple(item.set_id for item in variant)
        if state in seen_states:
            continue
        seen_states.add(state)
        yield tuple(variant), tuple(ascensions)

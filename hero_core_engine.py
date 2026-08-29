"""Generic event-driven HeroCore simulator.

Hero-specific mechanics live in JSON HeroCore files. The engine implements
shared combat semantics: resources, buffs, summons, multi-target damage and the
normalized equipment-set runtime used by recommendation.
"""

from __future__ import annotations

import ast
import heapq
import json
import math
import random
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from equipment_db import EquipmentDatabase
from equipment_models import EffectType, StatType
from equipment_rules import GameRules
from equipment_set_variants import load_optimizer_set_effects


ROOT = Path(__file__).resolve().parent
DEFAULT_CORE_DIR = ROOT / "data" / "hero_cores"

PANEL_EFFECTS = {
    EffectType.ATK_FLAT,
    EffectType.ATK_PCT,
    EffectType.HP_FLAT,
    EffectType.HP_PCT,
    EffectType.DEF_FLAT,
    EffectType.DEF_PCT,
    EffectType.CRIT_RATE,
    EffectType.CRIT_DMG,
    EffectType.ATK_SPEED,
    EffectType.RAGE_REGEN,
    EffectType.HEALING_EFFECT,
}
DAMAGE_EFFECTS = {
    EffectType.DAMAGE_PCT,
    EffectType.BASIC_DMG,
    EffectType.SKILL_DMG,
    EffectType.ULT_DMG,
    EffectType.SINGLE_DMG,
    EffectType.AOE_DMG,
}
_SUPPORTED_SET_TRIGGERS = {
    "always",
    "on_deploy",
    "on_ult",
    "on_any_ultimate_cast",
    "on_crit",
    "on_basic_crit",
    "on_basic_attack_damage",
    "on_kill",
}


class HeroCoreError(ValueError):
    """Raised when a HeroCore cannot be loaded or executed safely."""


def _namespace(value: Any) -> Any:
    if isinstance(value, dict):
        return SimpleNamespace(**{key: _namespace(item) for key, item in value.items()})
    if isinstance(value, list):
        return [_namespace(item) for item in value]
    return value


class SafeExpression:
    """Small expression evaluator for HeroCore conditions."""

    _ALLOWED = (
        ast.Expression,
        ast.BoolOp,
        ast.And,
        ast.Or,
        ast.UnaryOp,
        ast.Not,
        ast.USub,
        ast.UAdd,
        ast.BinOp,
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.FloorDiv,
        ast.Mod,
        ast.Pow,
        ast.Compare,
        ast.Eq,
        ast.NotEq,
        ast.Lt,
        ast.LtE,
        ast.Gt,
        ast.GtE,
        ast.Name,
        ast.Load,
        ast.Attribute,
        ast.Constant,
    )

    @classmethod
    def compile(cls, expression: str | None) -> ast.Expression | None:
        if not expression:
            return None
        try:
            tree = ast.parse(expression, mode="eval")
        except SyntaxError as error:
            raise HeroCoreError(f"invalid condition expression: {expression}") from error
        for node in ast.walk(tree):
            if not isinstance(node, cls._ALLOWED):
                raise HeroCoreError(f"unsupported condition syntax: {type(node).__name__}")
            if isinstance(node, ast.Name) and node.id not in {
                "state", "resource", "target", "event", "summon", "buff", "true", "false"
            }:
                raise HeroCoreError(f"unsupported condition name: {node.id}")
            if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
                raise HeroCoreError("private attributes are not allowed in HeroCore conditions")
        return tree

    @classmethod
    def evaluate(cls, compiled: ast.Expression | None, context: dict[str, Any]) -> bool:
        if compiled is None:
            return True
        env = {
            "state": _namespace(context.get("state", {})),
            "resource": _namespace(context.get("resource", {})),
            "target": _namespace(context.get("target", {})),
            "event": _namespace(context.get("event", {})),
            "summon": _namespace(context.get("summon", {})),
            "buff": _namespace(context.get("buff", {})),
            "true": True,
            "false": False,
        }
        code = compile(compiled, "<herocore-condition>", "eval")
        return bool(eval(code, {"__builtins__": {}}, env))


def _validate_targeting(spec: dict[str, Any], owner: str) -> None:
    target_cap = spec.get("target_cap")
    if target_cap is not None:
        try:
            target_cap_int = int(target_cap)
        except (TypeError, ValueError) as error:
            raise HeroCoreError(f"{owner} target_cap must be a positive integer or null") from error
        if target_cap_int < 1:
            raise HeroCoreError(f"{owner} target_cap must be at least 1 or null")
    if spec.get("secondary_target_ratio") is not None:
        try:
            ratio = float(spec.get("secondary_target_ratio"))
        except (TypeError, ValueError) as error:
            raise HeroCoreError(f"{owner} secondary_target_ratio must be non-negative") from error
        if ratio < 0:
            raise HeroCoreError(f"{owner} secondary_target_ratio must be non-negative")


def validate_core(core: dict[str, Any]) -> None:
    if str(core.get("schema_version")) != "1.0":
        raise HeroCoreError("HeroCore schema_version must be 1.0")
    hero = core.get("hero") or {}
    if not hero.get("id") or not hero.get("name"):
        raise HeroCoreError("HeroCore hero.id and hero.name are required")
    base = hero.get("base_stats") or {}
    for key in ("atk", "crit_rate", "crit_dmg", "attack_interval"):
        if key not in base:
            raise HeroCoreError(f"HeroCore hero.base_stats.{key} is required")
    skills = core.get("skills") or {}
    for skill_id, skill in skills.items():
        if skill.get("kind") not in {"basic", "skill", "ultimate"}:
            raise HeroCoreError(f"skill {skill_id} has invalid kind")
        if float(skill.get("coefficient", 0)) < 0:
            raise HeroCoreError(f"skill {skill_id} coefficient must be non-negative")
        _validate_targeting(skill, f"skill {skill_id}")
    for trigger in core.get("triggers", []):
        if not trigger.get("event"):
            raise HeroCoreError("every trigger requires an event")
        SafeExpression.compile(trigger.get("condition"))
        for action in trigger.get("actions", []):
            SafeExpression.compile(action.get("condition"))
            if action.get("type") == "deal_damage":
                _validate_targeting(action, f"trigger action {trigger.get('id', trigger['event'])}")
    for summon_id, summon in (core.get("summons") or {}).items():
        attack = summon.get("attack") or {}
        if attack:
            _validate_targeting(attack, f"summon {summon_id} attack")


def load_core(core_id: str, core_dir: Path = DEFAULT_CORE_DIR) -> dict[str, Any]:
    path = Path(core_dir) / f"{core_id.lower()}.json"
    if not path.exists():
        candidates = {item.stem.upper(): item for item in Path(core_dir).glob("*.json")}
        path = candidates.get(core_id.upper())
    if not path or not Path(path).exists():
        raise HeroCoreError(f"unknown HeroCore: {core_id}")
    core = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_core(core)
    return core


def list_cores(core_dir: Path = DEFAULT_CORE_DIR) -> list[dict[str, Any]]:
    result = []
    for path in sorted(Path(core_dir).glob("*.json")):
        core = json.loads(path.read_text(encoding="utf-8"))
        validate_core(core)
        hero = core["hero"]
        result.append({
            "id": hero["id"],
            "name": hero["name"],
            "core_version": core.get("core_version"),
            "game_version": core.get("game_version"),
            "policies": sorted((core.get("policies") or {}).keys()),
            "assumptions": core.get("assumptions", []),
            "validation_required": core.get("validation_required", []),
        })
    return result


@dataclass(order=True)
class ScheduledEvent:
    time: float
    sequence: int
    event_type: str = field(compare=False)
    payload: dict[str, Any] = field(default_factory=dict, compare=False)


@dataclass
class TrialResult:
    total_damage: float
    source_damage: dict[str, float]
    event_counts: dict[str, int]
    panel: dict[str, float]
    active_sets: tuple[str, ...]
    coverage: str
    warnings: list[str]


def _mean_std(values: list[float]) -> tuple[float, float]:
    if not values:
        return 0.0, 0.0
    mean = sum(values) / len(values)
    if len(values) == 1:
        return mean, 0.0
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    return mean, math.sqrt(max(0.0, variance))


class HeroCoreSimulator:
    """Execute a HeroCore without hero-specific branches in the engine."""

    def __init__(
        self,
        core: dict[str, Any],
        *,
        database: EquipmentDatabase | None = None,
        item_ids: list[str] | None = None,
        target: dict[str, Any] | None = None,
        policy: str | None = None,
        seed: int = 1,
        warmup: float = 0.0,
        measurement: float = 60.0,
    ):
        validate_core(core)
        self.core = core
        self.database = database
        self.item_ids = list(item_ids or [])
        target_value = dict(target or {})
        defense = max(0.0, float(target_value.get("defense", 0.0)))
        self.target = {
            "defense": defense,
            # Backward compatibility: until the UI exposes a separate MRES
            # field, target_def is also the magic-resistance fallback.
            "mres": max(0.0, float(target_value.get("mres", defense))),
            "control_immune": True,
            "enemy_count": 1,
            **target_value,
        }
        self.target["defense"] = max(0.0, float(self.target.get("defense", 0.0)))
        self.target["mres"] = max(0.0, float(self.target.get("mres", self.target["defense"])))
        self.target["enemy_count"] = max(1, int(self.target.get("enemy_count", 1)))
        self.policy_name = policy or core.get("default_policy") or next(iter(core.get("policies", {})), "")
        self.policy = (core.get("policies") or {}).get(self.policy_name, {})
        self.rng = random.Random(seed)
        self.warmup = max(0.0, float(warmup))
        self.measurement = max(0.001, float(measurement))
        self.end_time = self.warmup + self.measurement
        self.rules = GameRules.from_mapping(database.load_rules()) if database is not None else GameRules()

        self.sequence = 0
        self.queue: list[ScheduledEvent] = []
        self.now = 0.0
        self.last_resource_update = 0.0
        self.damage_total = 0.0
        self.source_damage: defaultdict[str, float] = defaultdict(float)
        self.event_counts: defaultdict[str, int] = defaultdict(int)
        self.state = {key: spec.get("initial", 0) for key, spec in (core.get("state") or {}).items()}
        self.resources = {
            key: float(spec.get("initial", 0.0))
            for key, spec in (core.get("resources") or {}).items()
        }
        self.buffs: dict[str, float] = {}
        self.skill_ready: dict[str, float] = defaultdict(float)
        self.basic_block_until = 0.0
        self.ultimate_casting = False
        self.summon_serial = 0
        self.summons: dict[int, str] = {}

        # Runtime set state. Duration stacks keep independent expiries so
        # stackable effects such as on-basic-crit buffs do not become permanent
        # or receive impossible full uptime.
        self.set_effect_expiries: defaultdict[str, list[float]] = defaultdict(list)
        self.set_effect_permanent_stacks: defaultdict[str, float] = defaultdict(float)
        self.set_effect_last_trigger: defaultdict[str, float] = defaultdict(lambda: -float("inf"))
        self.set_effect_conditions: dict[str, ast.Expression | None] = {}
        self._static_components: dict[str, float] = {}

        self.compiled_triggers: dict[str, list[tuple[int, dict[str, Any], ast.Expression | None]]] = defaultdict(list)
        for trigger in core.get("triggers", []):
            self.compiled_triggers[trigger["event"]].append((
                int(trigger.get("priority", 100)),
                trigger,
                SafeExpression.compile(trigger.get("condition")),
            ))
        for bucket in self.compiled_triggers.values():
            bucket.sort(key=lambda entry: entry[0])

        self.panel, self.active_sets, self.set_effects, self.warnings = self._build_panel()
        self.coverage = "full" if not self.warnings else "partial"

    def _build_panel(self) -> tuple[dict[str, float], tuple[str, ...], list[Any], list[str]]:
        base = self.core["hero"]["base_stats"]
        stats: defaultdict[str, float] = defaultdict(float)
        active_sets: set[str] = set()
        set_effects: list[Any] = []
        warnings: list[str] = []

        if self.database is not None and self.item_ids:
            items = self.database.load_equipment(self.item_ids)
            if len(items) != len(self.item_ids):
                missing = sorted(set(self.item_ids) - {item.item_id for item in items})
                raise HeroCoreError(f"unavailable equipment item(s): {', '.join(missing)}")
            for item in items:
                for stat in item.stats:
                    stats[stat.stat_type.value] += float(stat.stat_value)

            definitions = self.database.load_sets()
            counts = Counter(item.set_id for item in items)
            active_sets = {
                set_id for set_id, count in counts.items()
                if set_id in definitions and count >= definitions[set_id].required_pieces
            }
            # Always normalize V2.2 semantic set rows, including direct/manual
            # simulation which intentionally does not use the +16/P90 database.
            all_effects = [
                effect for effect in load_optimizer_set_effects(self.database)
                if effect.set_id in active_sets
            ]
            set_effects = all_effects
            for effect in all_effects:
                if effect.requires_dot:
                    warnings.append(f"套装效果 {effect.effect_id} 依赖DOT/灼烧状态，当前木桩HeroCore未建模该状态。")
                    continue
                if effect.trigger not in _SUPPORTED_SET_TRIGGERS:
                    warnings.append(f"套装效果 {effect.effect_id} 的触发器 {effect.trigger} 尚未建模。")
                    continue
                if effect.effect_type not in PANEL_EFFECTS | DAMAGE_EFFECTS | {EffectType.EXTRA_DAMAGE, EffectType.PENETRATION}:
                    warnings.append(f"套装效果 {effect.effect_id} 的效果类型 {effect.effect_type.value} 尚未建模。")
                    continue
                condition = getattr(effect, "condition", None)
                if condition:
                    try:
                        self.set_effect_conditions[effect.effect_id] = SafeExpression.compile(str(condition))
                    except HeroCoreError:
                        self.set_effect_conditions[effect.effect_id] = None
                        warnings.append(f"套装效果 {effect.effect_id} 的条件 {condition!r} 不能由HeroCore安全表达式执行。")
                if getattr(effect, "approximate", False):
                    warnings.append(f"套装效果 {effect.effect_id} 使用近似数据。")
                if effect.trigger == "always" and effect.effect_type in PANEL_EFFECTS:
                    stats[effect.effect_type.value] += float(effect.value)
                elif effect.trigger == "always" and effect.effect_type == EffectType.PENETRATION:
                    stats[EffectType.PENETRATION.value] += float(effect.value)

        base_atk = float(base["atk"])
        base_hp = float(base.get("hp", base.get("max_hp", 0.0)) or 0.0)
        base_def = float(base.get("def", base.get("defense", 0.0)) or 0.0)
        atk_flat = stats[StatType.ATK_FLAT.value]
        atk_pct = stats[StatType.ATK_PCT.value]
        hp_flat = stats[StatType.HP_FLAT.value]
        hp_pct = stats[StatType.HP_PCT.value]
        def_flat = stats[StatType.DEF_FLAT.value]
        def_pct = stats[StatType.DEF_PCT.value]
        raw_crit = float(base.get("crit_rate", 0.0)) + stats[StatType.CRIT_RATE.value]
        crit_rate, overflow = self.rules.crit(raw_crit)
        base_attack_speed = float(base.get("atk_speed", 0.0))

        self._static_components = {
            "base_atk": base_atk,
            "atk_flat": atk_flat,
            "atk_pct": atk_pct,
            "base_crit_rate": float(base.get("crit_rate", 0.0)),
            "crit_rate_bonus": stats[StatType.CRIT_RATE.value],
        }
        panel = {
            "atk": self.rules.compose_attack(base_atk, atk_flat, atk_pct),
            "hp": (base_hp + hp_flat) * (1.0 + hp_pct),
            "defense": (base_def + def_flat) * (1.0 + def_pct),
            "crit_rate": crit_rate,
            "crit_overflow": overflow,
            "crit_dmg": float(base.get("crit_dmg", 1.5)) + stats[StatType.CRIT_DMG.value],
            "atk_speed_base": base_attack_speed,
            "atk_speed": base_attack_speed + stats[StatType.ATK_SPEED.value],
            "rage_regen": float(base.get("rage_regen", 0.0)) + stats[StatType.RAGE_REGEN.value],
            "healing_effect": float(base.get("healing_effect", 0.0)) + stats[StatType.HEALING_EFFECT.value],
            "penetration": max(0.0, stats[EffectType.PENETRATION.value]),
            "attack_interval": float(base["attack_interval"]),
        }
        return panel, tuple(sorted(active_sets)), set_effects, warnings

    def _schedule(self, time_point: float, event_type: str, **payload: Any) -> None:
        if time_point > self.end_time + 1e-9:
            return
        self.sequence += 1
        heapq.heappush(self.queue, ScheduledEvent(float(time_point), self.sequence, event_type, payload))

    def _purge_set_effect_stacks(self, effect_id: str) -> None:
        if effect_id not in self.set_effect_expiries:
            return
        active = [expiry for expiry in self.set_effect_expiries[effect_id] if expiry > self.now]
        if active:
            self.set_effect_expiries[effect_id] = active
        else:
            self.set_effect_expiries.pop(effect_id, None)

    def _active_set_effect_stacks(self, effect: Any) -> float:
        if effect.trigger == "always":
            return 1.0
        self._purge_set_effect_stacks(effect.effect_id)
        return self.set_effect_permanent_stacks.get(effect.effect_id, 0.0) + len(
            self.set_effect_expiries.get(effect.effect_id, [])
        )

    def _effect_condition_satisfied(self, effect: Any, event: dict[str, Any]) -> bool:
        condition = getattr(effect, "condition", None)
        if not condition:
            return True
        if effect.effect_id not in self.set_effect_conditions:
            return False
        compiled = self.set_effect_conditions[effect.effect_id]
        if compiled is None:
            return False
        return SafeExpression.evaluate(compiled, self._condition_context(event))

    def _activate_set_effect(self, effect: Any) -> None:
        maximum = max(1, int(getattr(effect, "max_stacks", 1) or 1))
        duration = float(getattr(effect, "duration", 0.0) or 0.0)
        stack_rule = str(getattr(effect, "stack_rule", "add") or "add").lower()
        if duration > 0:
            self._purge_set_effect_stacks(effect.effect_id)
            expiries = list(self.set_effect_expiries.get(effect.effect_id, []))
            if maximum <= 1:
                expiries = [self.now + duration]
            elif len(expiries) < maximum:
                expiries.append(self.now + duration)
            elif stack_rule in {"refresh", "replace"}:
                expiries = [self.now + duration for _ in expiries]
            else:
                # At cap, refresh the oldest stack rather than silently losing
                # a proc. This preserves the cap while keeping independent
                # stack durations for additive effects.
                expiries.sort()
                expiries[0] = self.now + duration
            self.set_effect_expiries[effect.effect_id] = expiries
        else:
            self.set_effect_permanent_stacks[effect.effect_id] = min(
                float(maximum),
                self.set_effect_permanent_stacks.get(effect.effect_id, 0.0) + 1.0,
            )

    def _trigger_set_effects(self, trigger: str, event: dict[str, Any] | None = None) -> None:
        event = dict(event or {})
        for effect in self.set_effects:
            if effect.requires_dot or effect.trigger != trigger:
                continue
            if effect.effect_type == EffectType.EXTRA_DAMAGE and trigger == "on_basic_attack_damage":
                continue
            if not self._effect_condition_satisfied(effect, event):
                continue
            internal_cd = max(0.0, float(getattr(effect, "internal_cd", 0.0) or 0.0))
            if self.now - self.set_effect_last_trigger[effect.effect_id] < internal_cd - 1e-9:
                continue
            chance = min(1.0, max(0.0, float(getattr(effect, "proc_chance", 1.0) or 0.0)))
            if chance < 1.0 and self.rng.random() >= chance:
                continue
            self.set_effect_last_trigger[effect.effect_id] = self.now
            self._activate_set_effect(effect)

    def _active_buff_modifiers(self) -> dict[str, float]:
        modifiers: defaultdict[str, float] = defaultdict(float)
        for buff_id, expires in list(self.buffs.items()):
            if expires <= self.now:
                self.buffs.pop(buff_id, None)
                continue
            spec = (self.core.get("buffs") or {}).get(buff_id, {})
            for key, value in (spec.get("modifiers") or {}).items():
                modifiers[str(key).lower()] += float(value)
        return modifiers

    def _active_set_modifiers(self) -> dict[str, float]:
        modifiers: defaultdict[str, float] = defaultdict(float)
        effect_keys = {
            EffectType.ATK_FLAT: "atk_flat",
            EffectType.ATK_PCT: "atk_pct",
            EffectType.CRIT_RATE: "crit_rate",
            EffectType.CRIT_DMG: "crit_dmg",
            EffectType.ATK_SPEED: "atk_speed",
            EffectType.RAGE_REGEN: "rage_regen",
            EffectType.PENETRATION: "penetration",
        }
        for effect in self.set_effects:
            if effect.requires_dot or effect.trigger == "always":
                continue
            key = effect_keys.get(effect.effect_type)
            if not key:
                continue
            stacks = self._active_set_effect_stacks(effect)
            if stacks > 0:
                modifiers[key] += float(effect.value) * stacks
        return modifiers

    def _active_combat_modifiers(self) -> dict[str, float]:
        modifiers: defaultdict[str, float] = defaultdict(float)
        for source in (self._active_buff_modifiers(), self._active_set_modifiers()):
            for key, value in source.items():
                modifiers[key] += value
        return modifiers

    def _resource_rate(self, name: str) -> float:
        spec = (self.core.get("resources") or {}).get(name, {})
        base_rate = float(spec.get("auto_per_second", 0.0))
        modifiers = self._active_combat_modifiers()
        regen = self.panel.get("rage_regen", 0.0) + modifiers.get("rage_regen", 0.0)
        return base_rate * (1.0 + max(-0.99, regen))

    def _update_resources(self, now: float) -> None:
        delta = max(0.0, now - self.last_resource_update)
        if delta <= 0:
            return
        for name, spec in (self.core.get("resources") or {}).items():
            rate = self._resource_rate(name)
            maximum = float(spec.get("max", float("inf")))
            self.resources[name] = min(maximum, self.resources.get(name, 0.0) + rate * delta)
        self.last_resource_update = now

    def _condition_context(self, event: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            "state": dict(self.state),
            "resource": dict(self.resources),
            "target": dict(self.target),
            "event": dict(event or {}),
            "summon": {"count": len(self.summons)},
            "buff": {key: self.buffs.get(key, 0.0) > self.now for key in (self.core.get("buffs") or {})},
        }

    @staticmethod
    def _is_single_target_instance(*, target_cap: int | None, tags: set[str]) -> bool:
        if "aoe" in tags:
            return False
        if target_cap is None:
            return True
        try:
            return int(target_cap) <= 1
        except (TypeError, ValueError):
            return False

    def _effect_applies_to(self, effect: Any, *, source: str, tags: set[str], target_cap: int | None) -> bool:
        applies = str(getattr(effect, "applies_to", "all") or "all").lower()
        single = self._is_single_target_instance(target_cap=target_cap, tags=tags)
        if applies in {"", "all", "self", "team", "owner"}:
            return True
        if applies == "basic":
            return source == "basic" or "basic_attack" in tags
        if applies == "single_target_basic":
            return (source == "basic" or "basic_attack" in tags) and single
        if applies in {"skill", "active_skill"}:
            return source == "skill"
        if applies in {"ultimate", "ult"}:
            return source == "ultimate"
        if applies in {"single", "single_target"}:
            return single
        if applies in {"aoe", "area"}:
            return not single
        return True

    def _set_damage_bonus(self, *, tags: set[str], source: str, target_cap: int | None) -> float:
        bonus = 0.0
        single = self._is_single_target_instance(target_cap=target_cap, tags=tags)
        for effect in self.set_effects:
            if effect.requires_dot or effect.effect_type not in DAMAGE_EFFECTS:
                continue
            stacks = self._active_set_effect_stacks(effect)
            if stacks <= 0 or not self._effect_condition_satisfied(effect, {"source": source, "tags": list(tags)}):
                continue
            if not self._effect_applies_to(effect, source=source, tags=tags, target_cap=target_cap):
                continue
            matched = False
            if effect.effect_type == EffectType.DAMAGE_PCT:
                matched = True
            elif effect.effect_type == EffectType.BASIC_DMG:
                matched = source == "basic" or "basic_attack" in tags
            elif effect.effect_type == EffectType.SKILL_DMG:
                matched = source == "skill"
            elif effect.effect_type == EffectType.ULT_DMG:
                matched = source == "ultimate"
            elif effect.effect_type == EffectType.SINGLE_DMG:
                matched = single
            elif effect.effect_type == EffectType.AOE_DMG:
                matched = not single
            if matched:
                bonus += float(effect.value) * stacks
        return bonus

    def _effective_target_count(self, *, target_cap: int | None, tags: set[str]) -> int:
        enemy_count = max(1, int(self.target.get("enemy_count", 1)))
        if target_cap is None:
            return enemy_count if "aoe" in tags else 1
        return min(enemy_count, max(1, int(target_cap)))

    def _target_multiplier(
        self,
        *,
        target_cap: int | None,
        tags: set[str],
        secondary_target_ratio: float = 1.0,
    ) -> float:
        targets = self._effective_target_count(target_cap=target_cap, tags=tags)
        ratio = max(0.0, float(secondary_target_ratio))
        return 1.0 + max(0, targets - 1) * ratio

    def _current_attack(self, modifiers: dict[str, float], atk_multiplier: float = 1.0) -> float:
        base = self._static_components.get("base_atk", self.panel["atk"])
        flat = self._static_components.get("atk_flat", 0.0) + modifiers.get("atk_flat", 0.0)
        pct = self._static_components.get("atk_pct", 0.0) + modifiers.get("atk_pct", 0.0)
        return self.rules.compose_attack(base, flat, pct) * atk_multiplier

    def _current_crit_rate(self, modifiers: dict[str, float]) -> float:
        raw = (
            self._static_components.get("base_crit_rate", 0.0)
            + self._static_components.get("crit_rate_bonus", 0.0)
            + modifiers.get("crit_rate", 0.0)
        )
        return self.rules.crit(raw)[0]

    def _damage_kind(self, tags: set[str]) -> str:
        if "true" in tags or "true_damage" in tags:
            return "true"
        if "magic" in tags:
            return "magic"
        if "physical" in tags:
            return "physical"
        return str((self.core.get("hero") or {}).get("damage_type") or "physical").lower()

    def _mitigation_factor(self, *, tags: set[str], modifiers: dict[str, float]) -> float:
        kind = self._damage_kind(tags)
        if kind == "true":
            return 1.0
        resistance = self.target.get("mres", self.target.get("defense", 0.0)) if kind == "magic" else self.target.get("defense", 0.0)
        resistance = max(0.0, float(resistance))
        defense_ignore = min(1.0, max(0.0, modifiers.get("defense_ignore", 0.0)))
        penetration = min(1.0, max(0.0, self.panel.get("penetration", 0.0) + modifiers.get("penetration", 0.0)))
        effective = resistance * (1.0 - defense_ignore) * (1.0 - penetration)
        return self.rules.defense_multiplier(effective)

    def _set_extra_damage(
        self,
        *,
        source: str,
        tags: set[str],
        target_cap: int | None,
        secondary_target_ratio: float,
        modifiers: dict[str, float],
    ) -> float:
        if source != "basic" and "basic_attack" not in tags:
            return 0.0
        total = 0.0
        targets = self._target_multiplier(
            target_cap=target_cap,
            tags=tags,
            secondary_target_ratio=secondary_target_ratio,
        )
        for effect in self.set_effects:
            if effect.requires_dot or effect.effect_type != EffectType.EXTRA_DAMAGE:
                continue
            if effect.trigger != "on_basic_attack_damage":
                continue
            event = {"source": source, "tags": list(tags), "target_cap": target_cap}
            if not self._effect_condition_satisfied(effect, event):
                continue
            if not self._effect_applies_to(effect, source=source, tags=tags, target_cap=target_cap):
                continue
            internal_cd = max(0.0, float(getattr(effect, "internal_cd", 0.0) or 0.0))
            if self.now - self.set_effect_last_trigger[effect.effect_id] < internal_cd - 1e-9:
                continue
            chance = min(1.0, max(0.0, float(getattr(effect, "proc_chance", 1.0) or 0.0)))
            if chance < 1.0 and self.rng.random() >= chance:
                continue
            self.set_effect_last_trigger[effect.effect_id] = self.now
            value = float(effect.value)
            if abs(value) < 1.0:
                # Current dictionary uses ratio-valued EXTRA_DAMAGE for the
                # max-HP true-damage set. Ratio extras are therefore resolved
                # from the owner's max HP and bypass mitigation.
                total += self.panel.get("hp", 0.0) * value * targets
            else:
                # Fixed extras (e.g. Insight 600) stay non-critical and outside
                # damage multipliers, matching the legacy simulator semantics.
                total += value * targets * self._mitigation_factor(tags=tags, modifiers=modifiers)
        return total

    def _deal_damage(
        self,
        *,
        coefficient: float,
        hit_count: int = 1,
        tags: list[str] | tuple[str, ...] | set[str] = (),
        can_crit: bool = True,
        source: str = "skill",
        atk_multiplier: float = 1.0,
        target_cap: int | None = None,
        secondary_target_ratio: float = 1.0,
    ) -> float:
        tags_set = set(tags)
        modifiers = self._active_combat_modifiers()
        atk = self._current_attack(modifiers, atk_multiplier)
        crit_rate = self._current_crit_rate(modifiers)
        crit_dmg = self.panel["crit_dmg"] + modifiers.get("crit_dmg", 0.0)
        crit_factor = 1.0
        if can_crit:
            crit_factor = (1.0 - crit_rate) + crit_rate * crit_dmg
        mitigation = self._mitigation_factor(tags=tags_set, modifiers=modifiers)
        damage_bonus = (
            modifiers.get("damage_pct", 0.0)
            + self._set_damage_bonus(tags=tags_set, source=source, target_cap=target_cap)
        )
        target_multiplier = self._target_multiplier(
            target_cap=target_cap,
            tags=tags_set,
            secondary_target_ratio=secondary_target_ratio,
        )
        damage = (
            atk
            * float(coefficient)
            * max(1, int(hit_count))
            * target_multiplier
            * crit_factor
            * mitigation
            * (1.0 + damage_bonus)
        )
        damage += self._set_extra_damage(
            source=source,
            tags=tags_set,
            target_cap=target_cap,
            secondary_target_ratio=secondary_target_ratio,
            modifiers=modifiers,
        )
        if self.now >= self.warmup:
            self.damage_total += damage
            self.source_damage[source] += damage

        # Base damage uses exact mathematical crit expectation for stable gear
        # ranking. Crit-triggered set state is sampled separately, then averaged
        # across trials. The triggering hit itself does not retroactively gain
        # the newly created buff.
        if can_crit and crit_rate > 0 and self.rng.random() < crit_rate:
            event = {"source": source, "tags": list(tags_set), "target_cap": target_cap}
            self._trigger_set_effects("on_crit", event)
            if source == "basic" or "basic_attack" in tags_set:
                self._trigger_set_effects("on_basic_crit", event)
        return damage

    def _apply_action(self, action: dict[str, Any], event: dict[str, Any]) -> None:
        compiled = SafeExpression.compile(action.get("condition"))
        if not SafeExpression.evaluate(compiled, self._condition_context(event)):
            return
        action_type = action.get("type")
        if action_type == "add_state":
            name = action["state"]
            value = action.get("value", 1)
            current = self.state.get(name, 0)
            next_value = current + value
            spec = (self.core.get("state") or {}).get(name, {})
            if spec.get("max") is not None:
                next_value = min(next_value, spec["max"])
            if spec.get("min") is not None:
                next_value = max(next_value, spec["min"])
            self.state[name] = next_value
        elif action_type == "set_state":
            self.state[action["state"]] = action.get("value", 0)
        elif action_type == "reset_state":
            name = action["state"]
            self.state[name] = (self.core.get("state") or {}).get(name, {}).get("initial", 0)
        elif action_type == "add_resource":
            name = action["resource"]
            spec = (self.core.get("resources") or {}).get(name, {})
            value = float(action.get("value", 0.0))
            if action.get("scale_with_regen", False):
                modifiers = self._active_combat_modifiers()
                regen = self.panel.get("rage_regen", 0.0) + modifiers.get("rage_regen", 0.0)
                value *= 1.0 + max(-0.99, regen)
            self.resources[name] = min(float(spec.get("max", float("inf"))), self.resources.get(name, 0.0) + value)
        elif action_type == "spend_resource":
            name = action["resource"]
            self.resources[name] = max(0.0, self.resources.get(name, 0.0) - float(action.get("value", 0.0)))
        elif action_type == "apply_buff":
            buff_id = action["buff"]
            spec = (self.core.get("buffs") or {}).get(buff_id)
            if spec is None:
                raise HeroCoreError(f"unknown buff: {buff_id}")
            duration = float(action.get("duration", spec.get("duration", 0.0)))
            self.buffs[buff_id] = max(self.buffs.get(buff_id, 0.0), self.now + duration)
        elif action_type == "set_event_coefficient":
            event["coefficient"] = float(action["value"])
        elif action_type == "deal_damage":
            action_has_tags = "tags" in action
            tags = action.get("tags", event.get("tags", []))
            if "target_cap" in action:
                target_cap = action.get("target_cap")
            elif not action_has_tags:
                target_cap = event.get("target_cap")
            else:
                target_cap = None
            if "secondary_target_ratio" in action:
                secondary_target_ratio = float(action.get("secondary_target_ratio", 1.0))
            elif not action_has_tags:
                secondary_target_ratio = float(event.get("secondary_target_ratio", 1.0))
            else:
                secondary_target_ratio = 1.0
            self._deal_damage(
                coefficient=float(action.get("coefficient", 0.0)),
                hit_count=int(action.get("hit_count", 1)),
                tags=tags,
                can_crit=bool(action.get("can_crit", True)),
                source=str(action.get("source", event.get("source", "followup"))),
                target_cap=target_cap,
                secondary_target_ratio=secondary_target_ratio,
            )
        elif action_type == "summon":
            entity = action["entity"]
            count = max(1, int(action.get("count", 1)))
            for _ in range(count):
                self._create_summon(entity)
        elif action_type == "remove_summon":
            entity = action["entity"]
            for serial, entity_id in list(self.summons.items()):
                if entity_id == entity:
                    self.summons.pop(serial, None)
        elif action_type == "schedule_event":
            self._schedule(
                self.now + float(action.get("delay", 0.0)),
                str(action["event"]),
                **dict(action.get("payload") or {}),
            )
        elif action_type in {"noop", None}:
            return
        else:
            raise HeroCoreError(f"unsupported HeroCore action: {action_type}")

    def _run_triggers(self, event_type: str, event: dict[str, Any]) -> None:
        for _, trigger, compiled in self.compiled_triggers.get(event_type, []):
            if not SafeExpression.evaluate(compiled, self._condition_context(event)):
                continue
            chance = float(trigger.get("chance", 1.0))
            if chance < 1.0 and self.rng.random() >= chance:
                continue
            for action in trigger.get("actions", []):
                self._apply_action(action, event)

    def _attack_interval(self, multiplier: float = 1.0) -> float:
        modifiers = self._active_combat_modifiers()
        speed = self.panel["atk_speed"] + modifiers.get("atk_speed", 0.0)
        return self.rules.attack_interval(
            self.panel["attack_interval"],
            speed,
            base_attack_speed=self.panel["atk_speed_base"],
        ) / max(0.01, multiplier)

    def _create_summon(self, entity: str) -> None:
        spec = (self.core.get("summons") or {}).get(entity)
        if spec is None:
            raise HeroCoreError(f"unknown summon: {entity}")
        maximum = int(spec.get("max_count", 999))
        existing = sum(1 for value in self.summons.values() if value == entity)
        if existing >= maximum:
            return
        self.summon_serial += 1
        serial = self.summon_serial
        self.summons[serial] = entity
        attack = spec.get("attack") or {}
        delay_mode = attack.get("first_attack_delay_mode", "interval")
        delay = 0.0 if delay_mode == "immediate" else self._attack_interval(float(attack.get("speed_multiplier", 1.0)))
        self._schedule(self.now + delay, "SUMMON_ATTACK", serial=serial, entity=entity)

    def _process_summon_attack(self, event: ScheduledEvent) -> None:
        serial = int(event.payload["serial"])
        entity = self.summons.get(serial)
        if not entity:
            return
        spec = self.core["summons"][entity]
        attack = spec["attack"]
        inherit = spec.get("inherit") or {}
        atk_multiplier = float(inherit.get("atk", 1.0))
        hit_count = max(1, int(attack.get("hit_count", 1)))
        for hit_index in range(hit_count):
            self._deal_damage(
                coefficient=float(attack.get("coefficient", 0.0)),
                hit_count=1,
                tags=attack.get("tags", ["summon"]),
                can_crit=bool(attack.get("can_crit", True)),
                source=f"summon:{entity}",
                atk_multiplier=atk_multiplier,
                target_cap=attack.get("target_cap"),
                secondary_target_ratio=float(attack.get("secondary_target_ratio", 1.0)),
            )
            payload = {
                "serial": serial,
                "entity": entity,
                "hit_index": hit_index,
                "target_cap": attack.get("target_cap"),
                "secondary_target_ratio": float(attack.get("secondary_target_ratio", 1.0)),
                "tags": list(attack.get("tags", ["summon"])),
            }
            self._run_triggers("SUMMON_ATTACK", payload)
        self._schedule(
            self.now + self._attack_interval(float(attack.get("speed_multiplier", 1.0))),
            "SUMMON_ATTACK",
            serial=serial,
            entity=entity,
        )

    def _process_basic(self) -> None:
        skill = next((value for value in self.core.get("skills", {}).values() if value.get("kind") == "basic"), None)
        if skill is None:
            return
        if self.now < self.basic_block_until - 1e-9:
            self._schedule(self.basic_block_until, "BASIC_ATTACK_READY")
            return
        event = {
            "skill_id": skill.get("id", "basic"),
            "coefficient": float(skill.get("coefficient", 0.0)),
            "tags": list(skill.get("tags", ["basic_attack"])),
            "source": "basic",
            "target_cap": skill.get("target_cap"),
            "secondary_target_ratio": float(skill.get("secondary_target_ratio", 1.0)),
        }
        self._run_triggers("BASIC_ATTACK_BEFORE_DAMAGE", event)
        hit_count = max(1, int(skill.get("hit_count", 1)))
        for hit_index in range(hit_count):
            hit_event = {**event, "hit_index": hit_index}
            self._deal_damage(
                coefficient=float(event["coefficient"]),
                hit_count=1,
                tags=event["tags"],
                can_crit=bool(skill.get("can_crit", True)),
                source="basic",
                target_cap=event.get("target_cap"),
                secondary_target_ratio=float(event.get("secondary_target_ratio", 1.0)),
            )
            self._run_triggers("BASIC_ATTACK_HIT", hit_event)
        resource_gain = skill.get("resource_gain") or {}
        if resource_gain:
            self._apply_action({
                "type": "add_resource",
                "resource": resource_gain["resource"],
                "value": resource_gain.get("value", 0.0),
                "scale_with_regen": resource_gain.get("scale_with_regen", False),
            }, event)
        self._schedule(self.now + self._attack_interval(), "BASIC_ATTACK_READY")
        self._schedule_policy_check(self.now)

    def _cast_skill(self, skill_id: str) -> None:
        skill = (self.core.get("skills") or {}).get(skill_id)
        if not skill:
            return
        ready_at = self.skill_ready.get(skill_id, 0.0)
        if self.now < ready_at - 1e-9:
            return
        kind = skill["kind"]
        if kind == "ultimate":
            self._cast_ultimate(skill_id, skill)
            return
        event = {
            "skill_id": skill_id,
            "source": "skill",
            "tags": list(skill.get("tags", ["skill"])),
            "target_cap": skill.get("target_cap"),
            "secondary_target_ratio": float(skill.get("secondary_target_ratio", 1.0)),
        }
        self._run_triggers("SKILL_CAST_START", event)
        duration = float(skill.get("duration", skill.get("action_time", 0.0)))
        if skill.get("blocks_basic_attack") and duration > 0:
            self.basic_block_until = max(self.basic_block_until, self.now + duration)
        hit_count = max(1, int(skill.get("hit_count", 1)))
        interval = float(skill.get("hit_interval", 0.0))
        for index in range(hit_count):
            self._schedule(self.now + index * interval, "SKILL_HIT", skill_id=skill_id, hit_index=index)
        self._schedule(self.now + duration, "SKILL_CAST_END", skill_id=skill_id)
        cooldown = float(skill.get("cooldown", 0.0) or 0.0)
        next_ready = self.now + cooldown if cooldown > 0 else self.now + max(duration, 0.01)
        self.skill_ready[skill_id] = next_ready
        if skill.get("auto_cast", True):
            self._schedule(next_ready, "SKILL_READY", skill_id=skill_id)

    def _process_skill_hit(self, skill_id: str, hit_index: int) -> None:
        skill = self.core["skills"][skill_id]
        event = {
            "skill_id": skill_id,
            "hit_index": hit_index,
            "coefficient": float(skill.get("coefficient", 0.0)),
            "source": "skill",
            "tags": list(skill.get("tags", ["skill"])),
            "target_cap": skill.get("target_cap"),
            "secondary_target_ratio": float(skill.get("secondary_target_ratio", 1.0)),
        }
        self._run_triggers("SKILL_BEFORE_DAMAGE", event)
        self._deal_damage(
            coefficient=float(event["coefficient"]),
            hit_count=1,
            tags=event["tags"],
            can_crit=bool(skill.get("can_crit", True)),
            source="skill",
            target_cap=event.get("target_cap"),
            secondary_target_ratio=float(event.get("secondary_target_ratio", 1.0)),
        )
        self._run_triggers("SKILL_HIT", event)

    def _ultimate_policy_satisfied(self, skill: dict[str, Any]) -> bool:
        skill_id = str(skill.get("id") or "")
        if skill_id and self.now < self.skill_ready.get(skill_id, 0.0) - 1e-9:
            return False
        resource = skill.get("resource") or {}
        if resource:
            name = resource["name"]
            if self.resources.get(name, 0.0) + 1e-9 < float(resource.get("cost", 0.0)):
                return False
        condition = self.policy.get("ultimate_when")
        return SafeExpression.evaluate(SafeExpression.compile(condition), self._condition_context())

    def _schedule_policy_check(self, now: float) -> None:
        ult = next(
            ((key, value) for key, value in (self.core.get("skills") or {}).items() if value.get("kind") == "ultimate"),
            None,
        )
        if not ult or self.ultimate_casting:
            return
        skill_id, skill = ult
        ready_at = self.skill_ready.get(skill_id, 0.0)
        if now < ready_at - 1e-9:
            self._schedule(ready_at, "POLICY_CHECK", skill_id=skill_id)
            return
        resource = skill.get("resource") or {}
        if not resource:
            if self._ultimate_policy_satisfied(skill):
                self._schedule(now, "POLICY_CHECK", skill_id=skill_id)
            return
        name = resource["name"]
        cost = float(resource.get("cost", 0.0))
        current = self.resources.get(name, 0.0)
        if current >= cost - 1e-9:
            if self._ultimate_policy_satisfied(skill):
                self._schedule(now, "POLICY_CHECK", skill_id=skill_id)
            return
        rate = self._resource_rate(name)
        if rate > 0:
            self._schedule(now + (cost - current) / rate, "POLICY_CHECK", skill_id=skill_id)

    def _cast_ultimate(self, skill_id: str, skill: dict[str, Any]) -> None:
        if self.ultimate_casting or not self._ultimate_policy_satisfied(skill):
            return
        resource = skill.get("resource") or {}
        if resource:
            self.resources[resource["name"]] -= float(resource.get("cost", 0.0))
        duration = float(skill.get("duration", skill.get("action_time", 0.0)))
        cooldown_raw = skill.get("cooldown")
        if cooldown_raw is not None:
            self.skill_ready[skill_id] = self.now + max(0.01, float(cooldown_raw))
        elif not resource:
            # A resource-less ultimate without a cooldown must not recast at the
            # same timestamp forever. Treat it as a one-shot scripted ultimate.
            self.skill_ready[skill_id] = self.end_time + 1.0

        self.ultimate_casting = True
        if skill.get("blocks_basic_attack"):
            self.basic_block_until = max(self.basic_block_until, self.now + duration)
        event = {
            "skill_id": skill_id,
            "source": "ultimate",
            "tags": list(skill.get("tags", ["ultimate"])),
            "target_cap": skill.get("target_cap"),
            "secondary_target_ratio": float(skill.get("secondary_target_ratio", 1.0)),
        }
        # Equipment effects triggered by opening an ultimate are active for the
        # ultimate's first hit and subsequent actions.
        self._trigger_set_effects("on_ult", event)
        self._trigger_set_effects("on_any_ultimate_cast", event)
        self._run_triggers("ULT_CAST_START", event)
        hit_count = max(1, int(skill.get("hit_count", 1)))
        interval = float(skill.get("hit_interval", duration / hit_count if hit_count else 0.0))
        for index in range(hit_count):
            self._schedule(self.now + index * interval, "ULT_HIT", skill_id=skill_id, hit_index=index)
        self._schedule(self.now + duration, "ULT_CAST_END", skill_id=skill_id)
        self.event_counts["ultimate_cast"] += 1

    def _process_ult_hit(self, skill_id: str, hit_index: int) -> None:
        skill = self.core["skills"][skill_id]
        event = {
            "skill_id": skill_id,
            "hit_index": hit_index,
            "coefficient": float(skill.get("coefficient", 0.0)),
            "source": "ultimate",
            "tags": list(skill.get("tags", ["ultimate"])),
            "target_cap": skill.get("target_cap"),
            "secondary_target_ratio": float(skill.get("secondary_target_ratio", 1.0)),
        }
        self._run_triggers("ULT_BEFORE_DAMAGE", event)
        self._deal_damage(
            coefficient=float(event["coefficient"]),
            hit_count=1,
            tags=event["tags"],
            can_crit=bool(skill.get("can_crit", True)),
            source="ultimate",
            target_cap=event.get("target_cap"),
            secondary_target_ratio=float(event.get("secondary_target_ratio", 1.0)),
        )
        self._run_triggers("ULT_HIT", event)

    def _initialize(self) -> None:
        self._schedule(0.0, "BATTLE_START")
        basic = next((value for value in self.core.get("skills", {}).values() if value.get("kind") == "basic"), None)
        if basic:
            self._schedule(float(basic.get("initial_cooldown", 0.0)), "BASIC_ATTACK_READY")
        for skill_id, skill in (self.core.get("skills") or {}).items():
            if skill.get("kind") == "skill" and skill.get("auto_cast", True):
                initial = float(skill.get("initial_cooldown", skill.get("cooldown", 0.0) or 0.0))
                self.skill_ready[skill_id] = initial
                self._schedule(initial, "SKILL_READY", skill_id=skill_id)
        self._schedule_policy_check(0.0)

    def run(self) -> TrialResult:
        self._initialize()
        while self.queue:
            scheduled = heapq.heappop(self.queue)
            if scheduled.time >= self.end_time - 1e-9:
                break
            self.now = scheduled.time
            self._update_resources(self.now)
            self.event_counts[scheduled.event_type] += 1
            if scheduled.event_type == "BATTLE_START":
                self._trigger_set_effects("on_deploy", {})
                self._run_triggers("BATTLE_START", {})
            elif scheduled.event_type == "BASIC_ATTACK_READY":
                self._process_basic()
            elif scheduled.event_type == "SKILL_READY":
                self._cast_skill(str(scheduled.payload["skill_id"]))
            elif scheduled.event_type == "SKILL_HIT":
                self._process_skill_hit(str(scheduled.payload["skill_id"]), int(scheduled.payload["hit_index"]))
            elif scheduled.event_type == "SKILL_CAST_END":
                self._run_triggers("SKILL_CAST_END", dict(scheduled.payload))
                self._schedule_policy_check(self.now)
            elif scheduled.event_type == "POLICY_CHECK":
                skill_id = str(scheduled.payload["skill_id"])
                skill = self.core["skills"].get(skill_id)
                if skill and skill.get("kind") == "ultimate":
                    self._cast_ultimate(skill_id, skill)
            elif scheduled.event_type == "ULT_HIT":
                self._process_ult_hit(str(scheduled.payload["skill_id"]), int(scheduled.payload["hit_index"]))
            elif scheduled.event_type == "ULT_CAST_END":
                self.ultimate_casting = False
                self._run_triggers("ULT_CAST_END", dict(scheduled.payload))
                self._schedule_policy_check(self.now)
            elif scheduled.event_type == "SUMMON_ATTACK":
                self._process_summon_attack(scheduled)
            else:
                if scheduled.event_type in {"KILL", "ENEMY_KILLED"}:
                    self._trigger_set_effects("on_kill", dict(scheduled.payload))
                self._run_triggers(scheduled.event_type, dict(scheduled.payload))
            self._schedule_policy_check(self.now)

        return TrialResult(
            total_damage=self.damage_total,
            source_damage=dict(self.source_damage),
            event_counts=dict(self.event_counts),
            panel=dict(self.panel),
            active_sets=self.active_sets,
            coverage=self.coverage,
            warnings=list(dict.fromkeys(self.warnings)),
        )


def simulate_average(
    core: dict[str, Any],
    *,
    database: EquipmentDatabase | None = None,
    item_ids: list[str] | None = None,
    target: dict[str, Any] | None = None,
    policy: str | None = None,
    trials: int = 64,
    warmup: float = 120.0,
    measurement: float = 600.0,
    seed: int = 20260828,
) -> dict[str, Any]:
    """Return both strict first-60s and steady-state equivalent-60 damage."""

    trials = max(1, min(int(trials), 4096))
    warmup = max(0.0, float(warmup))
    measurement = max(1.0, float(measurement))
    equivalent_values: list[float] = []
    actual_values: list[float] = []
    source_accumulator: defaultdict[str, float] = defaultdict(float)
    event_accumulator: defaultdict[str, float] = defaultdict(float)
    representative: TrialResult | None = None

    for index in range(trials):
        trial_seed = seed + index
        steady = HeroCoreSimulator(
            core,
            database=database,
            item_ids=item_ids,
            target=target,
            policy=policy,
            seed=trial_seed,
            warmup=warmup,
            measurement=measurement,
        ).run()
        actual = HeroCoreSimulator(
            core,
            database=database,
            item_ids=item_ids,
            target=target,
            policy=policy,
            seed=trial_seed,
            warmup=0.0,
            measurement=60.0,
        ).run()
        equivalent_values.append(steady.total_damage / measurement * 60.0)
        actual_values.append(actual.total_damage)
        for key, value in steady.source_damage.items():
            source_accumulator[key] += value / measurement * 60.0
        for key, value in steady.event_counts.items():
            event_accumulator[key] += value / measurement * 60.0
        representative = steady

    eq_mean, eq_std = _mean_std(equivalent_values)
    actual_mean, actual_std = _mean_std(actual_values)
    representative = representative or HeroCoreSimulator(core).run()
    target_value = target or {}
    defense = max(0.0, float(target_value.get("defense", 0.0)))
    return {
        "hero": {
            "id": core["hero"]["id"],
            "name": core["hero"]["name"],
            "core_version": core.get("core_version"),
            "game_version": core.get("game_version"),
        },
        "policy": policy or core.get("default_policy"),
        "trials": trials,
        "warmup_seconds": warmup,
        "measurement_seconds": measurement,
        "target": {
            "defense": defense,
            "mres": max(0.0, float(target_value.get("mres", defense))),
            "control_immune": bool(target_value.get("control_immune", True)),
            "enemy_count": max(1, int(target_value.get("enemy_count", 1))),
        },
        "equivalent_60s": {
            "mean": eq_mean,
            "stddev": eq_std,
            "min": min(equivalent_values),
            "max": max(equivalent_values),
        },
        "actual_60s": {
            "mean": actual_mean,
            "stddev": actual_std,
            "min": min(actual_values),
            "max": max(actual_values),
        },
        "source_damage_equivalent_60s": {
            key: value / trials for key, value in sorted(source_accumulator.items())
        },
        "event_rate_per_60s": {
            key: value / trials for key, value in sorted(event_accumulator.items())
        },
        "panel": representative.panel,
        "active_sets": representative.active_sets,
        "coverage": representative.coverage,
        "warnings": representative.warnings,
        "assumptions": core.get("assumptions", []),
        "validation_required": core.get("validation_required", []),
    }

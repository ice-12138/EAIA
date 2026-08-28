"""Generic event-driven HeroCore simulator.

Hero-specific mechanics live in JSON HeroCore files.  The engine only knows
about common events, states, resources, buffs, summons and damage actions.
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
from equipment_models import EffectType, EquipmentItem, StatType
from equipment_rules import GameRules


ROOT = Path(__file__).resolve().parent
DEFAULT_CORE_DIR = ROOT / "data" / "hero_cores"

PANEL_EFFECTS = {
    EffectType.ATK_FLAT,
    EffectType.ATK_PCT,
    EffectType.CRIT_RATE,
    EffectType.CRIT_DMG,
    EffectType.ATK_SPEED,
    EffectType.RAGE_REGEN,
}
DAMAGE_EFFECTS = {
    EffectType.DAMAGE_PCT,
    EffectType.BASIC_DMG,
    EffectType.SKILL_DMG,
    EffectType.ULT_DMG,
    EffectType.SINGLE_DMG,
    EffectType.AOE_DMG,
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
    """Small expression evaluator for HeroCore conditions.

    It intentionally supports no function calls, subscripting, imports or
    arbitrary Python execution.
    """

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
    for trigger in core.get("triggers", []):
        if not trigger.get("event"):
            raise HeroCoreError("every trigger requires an event")
        SafeExpression.compile(trigger.get("condition"))
        for action in trigger.get("actions", []):
            SafeExpression.compile(action.get("condition"))


def load_core(core_id: str, core_dir: Path = DEFAULT_CORE_DIR) -> dict[str, Any]:
    path = Path(core_dir) / f"{core_id.lower()}.json"
    if not path.exists():
        candidates = {
            item.stem.upper(): item for item in Path(core_dir).glob("*.json")
        }
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
        self.target = {
            "defense": 0.0,
            "control_immune": True,
            "enemy_count": 1,
            **(target or {}),
        }
        self.policy_name = policy or core.get("default_policy") or next(iter(core.get("policies", {})), "")
        self.policy = (core.get("policies") or {}).get(self.policy_name, {})
        self.rng = random.Random(seed)
        self.warmup = max(0.0, float(warmup))
        self.measurement = max(0.001, float(measurement))
        self.end_time = self.warmup + self.measurement
        self.rules = (
            GameRules.from_mapping(database.load_rules())
            if database is not None
            else GameRules()
        )

        self.sequence = 0
        self.queue: list[ScheduledEvent] = []
        self.now = 0.0
        self.last_resource_update = 0.0
        self.damage_total = 0.0
        self.source_damage: defaultdict[str, float] = defaultdict(float)
        self.event_counts: defaultdict[str, int] = defaultdict(int)
        self.state = {
            key: spec.get("initial", 0)
            for key, spec in (core.get("state") or {}).items()
        }
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
            all_effects = [
                effect for effect in self.database.load_set_effects()
                if effect.set_id in active_sets and not effect.requires_dot
            ]
            set_effects = all_effects
            for effect in all_effects:
                if effect.trigger != "always" and effect.effect_type not in {EffectType.EXTRA_DAMAGE}:
                    warnings.append(
                        f"套装效果 {effect.effect_id} 使用触发型机制，HeroCore v1 仅对基础触发子集建模。"
                    )
                if effect.trigger == "always" and effect.effect_type in PANEL_EFFECTS:
                    stats[effect.effect_type.value] += float(effect.value)

        atk = self.rules.compose_attack(
            float(base["atk"]),
            stats[StatType.ATK_FLAT.value],
            stats[StatType.ATK_PCT.value],
        )
        crit_rate, overflow = self.rules.crit(
            float(base.get("crit_rate", 0.0)) + stats[StatType.CRIT_RATE.value]
        )
        panel = {
            "atk": atk,
            "crit_rate": crit_rate,
            "crit_overflow": overflow,
            "crit_dmg": float(base.get("crit_dmg", 1.5)) + stats[StatType.CRIT_DMG.value],
            "atk_speed": float(base.get("atk_speed", 0.0)) + stats[StatType.ATK_SPEED.value],
            "rage_regen": float(base.get("rage_regen", 0.0)) + stats[StatType.RAGE_REGEN.value],
            "attack_interval": float(base["attack_interval"]),
        }
        return panel, tuple(sorted(active_sets)), set_effects, warnings

    def _schedule(self, time_point: float, event_type: str, **payload: Any) -> None:
        if time_point > self.end_time + 1e-9:
            return
        self.sequence += 1
        heapq.heappush(
            self.queue,
            ScheduledEvent(float(time_point), self.sequence, event_type, payload),
        )

    def _resource_rate(self, name: str) -> float:
        spec = (self.core.get("resources") or {}).get(name, {})
        base_rate = float(spec.get("auto_per_second", 0.0))
        return base_rate * (1.0 + max(-0.99, self.panel.get("rage_regen", 0.0)))

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

    def _active_buff_modifiers(self) -> dict[str, float]:
        modifiers: defaultdict[str, float] = defaultdict(float)
        for buff_id, expires in list(self.buffs.items()):
            if expires <= self.now:
                self.buffs.pop(buff_id, None)
                continue
            spec = (self.core.get("buffs") or {}).get(buff_id, {})
            for key, value in (spec.get("modifiers") or {}).items():
                modifiers[key] += float(value)
        return modifiers

    def _set_damage_bonus(self, tags: set[str]) -> float:
        bonus = 0.0
        for effect in self.set_effects:
            if effect.trigger != "always" or effect.effect_type not in DAMAGE_EFFECTS:
                continue
            if effect.effect_type == EffectType.DAMAGE_PCT:
                bonus += effect.value
            elif effect.effect_type == EffectType.BASIC_DMG and "basic_attack" in tags:
                bonus += effect.value
            elif effect.effect_type == EffectType.SKILL_DMG and "skill" in tags:
                bonus += effect.value
            elif effect.effect_type == EffectType.ULT_DMG and "ultimate" in tags:
                bonus += effect.value
            elif effect.effect_type == EffectType.SINGLE_DMG and self.target.get("enemy_count", 1) <= 1:
                bonus += effect.value
            elif effect.effect_type == EffectType.AOE_DMG and self.target.get("enemy_count", 1) > 1:
                bonus += effect.value
        return bonus

    def _deal_damage(
        self,
        *,
        coefficient: float,
        hit_count: int = 1,
        tags: list[str] | tuple[str, ...] | set[str] = (),
        can_crit: bool = True,
        source: str = "skill",
        atk_multiplier: float = 1.0,
    ) -> float:
        tags_set = set(tags)
        modifiers = self._active_buff_modifiers()
        atk = self.panel["atk"] * (1.0 + modifiers.get("atk_pct", 0.0)) * atk_multiplier
        crit_dmg = self.panel["crit_dmg"] + modifiers.get("crit_dmg", 0.0)
        crit_factor = 1.0
        if can_crit:
            crit_factor = (1.0 - self.panel["crit_rate"]) + self.panel["crit_rate"] * crit_dmg
        defense_ignore = min(1.0, max(0.0, modifiers.get("defense_ignore", 0.0)))
        defense = max(0.0, float(self.target.get("defense", 0.0))) * (1.0 - defense_ignore)
        defense_factor = self.rules.defense_multiplier(defense)
        damage_bonus = modifiers.get("damage_pct", 0.0) + self._set_damage_bonus(tags_set)
        damage = (
            atk
            * float(coefficient)
            * max(1, int(hit_count))
            * crit_factor
            * defense_factor
            * (1.0 + damage_bonus)
        )
        if self.now >= self.warmup:
            self.damage_total += damage
            self.source_damage[source] += damage
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
                value *= 1.0 + max(-0.99, self.panel.get("rage_regen", 0.0))
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
            self._deal_damage(
                coefficient=float(action.get("coefficient", 0.0)),
                hit_count=int(action.get("hit_count", 1)),
                tags=action.get("tags", event.get("tags", [])),
                can_crit=bool(action.get("can_crit", True)),
                source=str(action.get("source", event.get("source", "followup"))),
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
        modifiers = self._active_buff_modifiers()
        speed = self.panel["atk_speed"] + modifiers.get("atk_speed", 0.0)
        return self.rules.attack_interval(
            self.panel["attack_interval"],
            speed,
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
        self._deal_damage(
            coefficient=float(attack.get("coefficient", 0.0)),
            hit_count=int(attack.get("hit_count", 1)),
            tags=attack.get("tags", ["summon"]),
            can_crit=bool(attack.get("can_crit", True)),
            source=f"summon:{entity}",
            atk_multiplier=atk_multiplier,
        )
        payload = {"serial": serial, "entity": entity}
        self._run_triggers("SUMMON_ATTACK", payload)
        self._schedule(
            self.now + self._attack_interval(float(attack.get("speed_multiplier", 1.0))),
            "SUMMON_ATTACK",
            serial=serial,
            entity=entity,
        )

    def _process_basic(self) -> None:
        skill = next(
            (value for value in self.core.get("skills", {}).values() if value.get("kind") == "basic"),
            None,
        )
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
        }
        self._run_triggers("BASIC_ATTACK_BEFORE_DAMAGE", event)
        self._deal_damage(
            coefficient=float(event["coefficient"]),
            hit_count=int(skill.get("hit_count", 1)),
            tags=event["tags"],
            can_crit=bool(skill.get("can_crit", True)),
            source="basic",
        )
        resource_gain = skill.get("resource_gain") or {}
        if resource_gain:
            self._apply_action({
                "type": "add_resource",
                "resource": resource_gain["resource"],
                "value": resource_gain.get("value", 0.0),
                "scale_with_regen": resource_gain.get("scale_with_regen", False),
            }, event)
        self._run_triggers("BASIC_ATTACK_HIT", event)
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
        event = {"skill_id": skill_id, "source": "skill", "tags": list(skill.get("tags", ["skill"]))}
        self._run_triggers("SKILL_CAST_START", event)
        duration = float(skill.get("duration", skill.get("action_time", 0.0)))
        if skill.get("blocks_basic_attack") and duration > 0:
            self.basic_block_until = max(self.basic_block_until, self.now + duration)
        hit_count = max(1, int(skill.get("hit_count", 1)))
        interval = float(skill.get("hit_interval", 0.0))
        for index in range(hit_count):
            self._schedule(self.now + index * interval, "SKILL_HIT", skill_id=skill_id, hit_index=index)
        self._schedule(self.now + duration, "SKILL_CAST_END", skill_id=skill_id)
        cooldown = float(skill.get("cooldown", 0.0))
        self.skill_ready[skill_id] = self.now + cooldown
        if cooldown > 0:
            self._schedule(self.now + cooldown, "SKILL_READY", skill_id=skill_id)

    def _process_skill_hit(self, skill_id: str, hit_index: int) -> None:
        skill = self.core["skills"][skill_id]
        event = {
            "skill_id": skill_id,
            "hit_index": hit_index,
            "coefficient": float(skill.get("coefficient", 0.0)),
            "source": "skill",
            "tags": list(skill.get("tags", ["skill"])),
        }
        self._run_triggers("SKILL_BEFORE_DAMAGE", event)
        self._deal_damage(
            coefficient=float(event["coefficient"]),
            hit_count=1,
            tags=event["tags"],
            can_crit=bool(skill.get("can_crit", True)),
            source="skill",
        )
        self._run_triggers("SKILL_HIT", event)

    def _ultimate_policy_satisfied(self, skill: dict[str, Any]) -> bool:
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
        resource = skill.get("resource") or {}
        if not resource:
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
        self.ultimate_casting = True
        duration = float(skill.get("duration", skill.get("action_time", 0.0)))
        if skill.get("blocks_basic_attack"):
            self.basic_block_until = max(self.basic_block_until, self.now + duration)
        event = {"skill_id": skill_id, "source": "ultimate", "tags": list(skill.get("tags", ["ultimate"]))}
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
        }
        self._run_triggers("ULT_BEFORE_DAMAGE", event)
        self._deal_damage(
            coefficient=float(event["coefficient"]),
            hit_count=1,
            tags=event["tags"],
            can_crit=bool(skill.get("can_crit", True)),
            source="ultimate",
        )
        self._run_triggers("ULT_HIT", event)

    def _initialize(self) -> None:
        self._schedule(0.0, "BATTLE_START")
        basic = next(
            (value for value in self.core.get("skills", {}).values() if value.get("kind") == "basic"),
            None,
        )
        if basic:
            self._schedule(float(basic.get("initial_cooldown", 0.0)), "BASIC_ATTACK_READY")
        for skill_id, skill in (self.core.get("skills") or {}).items():
            if skill.get("kind") == "skill" and skill.get("auto_cast", True):
                initial = float(skill.get("initial_cooldown", skill.get("cooldown", 0.0)))
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

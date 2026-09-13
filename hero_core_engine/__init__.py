"""Extended HeroCore facade with reusable triggered-combat semantics.

The stable engine remains in ../hero_core_engine.py.  This package shadows the
legacy module (the same extension pattern used by hero_core_service/) and adds
only generic mechanics required by event-driven HeroCore JSON files.  No
hero-specific branches live here.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import random
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


_LEGACY_PATH = Path(__file__).resolve().parent.parent / "hero_core_engine.py"
_SPEC = importlib.util.spec_from_file_location("_eaia_hero_core_engine_legacy", _LEGACY_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise ImportError(f"Unable to load legacy HeroCore engine: {_LEGACY_PATH}")
_legacy = importlib.util.module_from_spec(_SPEC)
sys.modules.setdefault(_SPEC.name, _legacy)
_SPEC.loader.exec_module(_legacy)

HeroCoreError = _legacy.HeroCoreError
SafeExpression = _legacy.SafeExpression
ScheduledEvent = _legacy.ScheduledEvent
TrialResult = _legacy.TrialResult
ROOT = _legacy.ROOT
DEFAULT_CORE_DIR = _legacy.DEFAULT_CORE_DIR

_legacy_validate_core = _legacy.validate_core


def _legacy_validation_view(core: dict[str, Any]) -> dict[str, Any]:
    """Map extension-only kinds onto the closest legacy validation shape."""
    normalized = copy.deepcopy(core)
    for skill in (normalized.get("skills") or {}).values():
        if skill.get("kind") == "triggered":
            skill["kind"] = "skill"
            skill["auto_cast"] = False
    return normalized


def validate_core(core: dict[str, Any]) -> None:
    """Validate legacy HeroCore fields plus the generic ``triggered`` skill kind."""
    _legacy_validate_core(_legacy_validation_view(core))
    for skill_id, skill in (core.get("skills") or {}).items():
        if skill.get("kind") == "triggered":
            if float(skill.get("hit_interval", 0.0) or 0.0) != 0.0:
                raise HeroCoreError(
                    f"triggered skill {skill_id} currently requires hit_interval=0"
                )
    for policy_id, policy in (core.get("policies") or {}).items():
        try:
            SafeExpression.compile(policy.get("ultimate_when"))
        except HeroCoreError as error:
            raise HeroCoreError(
                f"policy {policy_id} has invalid ultimate_when expression"
            ) from error


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
        result.append(
            {
                "id": hero["id"],
                "name": hero["name"],
                "core_version": core.get("core_version"),
                "game_version": core.get("game_version"),
                "policies": sorted((core.get("policies") or {}).keys()),
                "assumptions": core.get("assumptions", []),
                "validation_required": core.get("validation_required", []),
            }
        )
    return result


class HeroCoreSimulator(_legacy.HeroCoreSimulator):
    """Legacy simulator plus reusable triggered-skill and state-machine actions."""

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.buff_stacks: defaultdict[str, int] = defaultdict(int)
        self.trigger_last_fired: defaultdict[str, float] = defaultdict(
            lambda: -float("inf")
        )
        self.trigger_eligible_counts: defaultdict[str, int] = defaultdict(int)

    def _active_buff_modifiers(self) -> dict[str, float]:
        modifiers: defaultdict[str, float] = defaultdict(float)
        for buff_id, expires in list(self.buffs.items()):
            if expires <= self.now:
                self.buffs.pop(buff_id, None)
                self.buff_stacks.pop(buff_id, None)
                continue
            spec = (self.core.get("buffs") or {}).get(buff_id, {})
            stacks = max(1, int(self.buff_stacks.get(buff_id, 1)))
            for key, value in (spec.get("modifiers") or {}).items():
                modifiers[str(key).lower()] += float(value) * stacks
        return modifiers

    def _current_attack(
        self, modifiers: dict[str, float], atk_multiplier: float = 1.0
    ) -> float:
        base = self._static_components.get("base_atk", self.panel["atk"])
        base *= 1.0 + modifiers.get("base_atk_pct", 0.0)
        flat = (
            self._static_components.get("atk_flat", 0.0)
            + modifiers.get("atk_flat", 0.0)
        )
        pct = (
            self._static_components.get("atk_pct", 0.0)
            + modifiers.get("atk_pct", 0.0)
        )
        return self.rules.compose_attack(base, flat, pct) * atk_multiplier

    def _mitigation_factor(
        self, *, tags: set[str], modifiers: dict[str, float]
    ) -> float:
        kind = self._damage_kind(tags)
        if kind == "true":
            return 1.0

        if kind == "magic":
            resistance = max(
                0.0, float(self.target.get("mres", self.target.get("defense", 0.0)))
            )
            if "ignore_mres" in tags or "ignore_resistance" in tags:
                resistance = 0.0
            else:
                reduction = min(
                    1.0, max(0.0, modifiers.get("target_mres_reduction", 0.0))
                )
                resistance *= 1.0 - reduction
        else:
            resistance = max(0.0, float(self.target.get("defense", 0.0)))
            if "ignore_defense" in tags or "ignore_resistance" in tags:
                resistance = 0.0
            else:
                reduction = min(
                    1.0, max(0.0, modifiers.get("target_defense_reduction", 0.0))
                )
                resistance *= 1.0 - reduction

        if resistance > 0.0:
            defense_ignore = min(
                1.0, max(0.0, modifiers.get("defense_ignore", 0.0))
            )
            penetration = min(
                1.0,
                max(
                    0.0,
                    self.panel.get("penetration", 0.0)
                    + modifiers.get("penetration", 0.0),
                ),
            )
            resistance *= (1.0 - defense_ignore) * (1.0 - penetration)
        return self.rules.defense_multiplier(resistance)

    def _apply_action(self, action: dict[str, Any], event: dict[str, Any]) -> None:
        action_type = action.get("type")
        if action_type not in {
            "apply_buff",
            "remove_buff",
            "cast_skill",
            "cancel_event_damage",
            "set_event_value",
        } and not (
            action_type == "add_resource" and action.get("max_fraction") is not None
        ):
            super()._apply_action(action, event)
            return

        compiled = SafeExpression.compile(action.get("condition"))
        if not SafeExpression.evaluate(compiled, self._condition_context(event)):
            return

        if action_type == "apply_buff":
            buff_id = str(action["buff"])
            spec = (self.core.get("buffs") or {}).get(buff_id)
            if spec is None:
                raise HeroCoreError(f"unknown buff: {buff_id}")
            raw_duration = action.get("duration", spec.get("duration"))
            duration = (
                self.end_time - self.now + 1.0
                if raw_duration is None
                else max(0.0, float(raw_duration))
            )
            maximum = max(1, int(spec.get("max_stacks", 1) or 1))
            added = max(1, int(action.get("stacks", 1) or 1))
            current = int(self.buff_stacks.get(buff_id, 0))
            self.buff_stacks[buff_id] = (
                min(maximum, current + added) if maximum > 1 else 1
            )
            self.buffs[buff_id] = max(
                self.buffs.get(buff_id, 0.0), self.now + duration
            )
            return

        if action_type == "remove_buff":
            buff_id = str(action["buff"])
            self.buffs.pop(buff_id, None)
            self.buff_stacks.pop(buff_id, None)
            return

        if action_type == "cast_skill":
            skill_id = str(action.get("skill") or action.get("skill_id") or "")
            if not skill_id or skill_id not in (self.core.get("skills") or {}):
                raise HeroCoreError(f"unknown skill in cast_skill action: {skill_id!r}")
            self._cast_skill(skill_id)
            return

        if action_type == "cancel_event_damage":
            event["cancel_damage"] = True
            return

        if action_type == "set_event_value":
            key = str(action.get("key") or "")
            if not key:
                raise HeroCoreError("set_event_value requires key")
            event[key] = action.get("value")
            return

        if action_type == "add_resource" and action.get("max_fraction") is not None:
            resource_name = str(action["resource"])
            resource_spec = (self.core.get("resources") or {}).get(resource_name)
            if resource_spec is None:
                raise HeroCoreError(f"unknown resource: {resource_name}")
            clone = dict(action)
            clone.pop("max_fraction", None)
            clone["value"] = (
                float(resource_spec.get("max", 0.0))
                * float(action.get("max_fraction", 0.0))
            )
            super()._apply_action(clone, event)
            return

    def _run_triggers(self, event_type: str, event: dict[str, Any]) -> None:
        for _, trigger, compiled in self.compiled_triggers.get(event_type, []):
            if not SafeExpression.evaluate(
                compiled, self._condition_context(event)
            ):
                continue

            trigger_key = str(
                trigger.get("id") or f"{event_type}:{id(trigger)}"
            )
            every = max(1, int(trigger.get("every", 1) or 1))
            if every > 1:
                self.trigger_eligible_counts[trigger_key] += 1
                if self.trigger_eligible_counts[trigger_key] % every != 0:
                    continue

            internal_cd = max(
                0.0, float(trigger.get("internal_cooldown", 0.0) or 0.0)
            )
            if (
                self.now - self.trigger_last_fired[trigger_key]
                < internal_cd - 1e-9
            ):
                continue

            chance = min(
                1.0, max(0.0, float(trigger.get("chance", 1.0) or 0.0))
            )
            if chance < 1.0 and self.rng.random() >= chance:
                continue

            # Mark before actions so recursive cast_skill actions cannot recursively
            # re-enter the same internal-cooldown trigger at the same timestamp.
            self.trigger_last_fired[trigger_key] = self.now
            for action in trigger.get("actions", []):
                self._apply_action(action, event)

    def _cast_triggered_skill(self, skill_id: str, skill: dict[str, Any]) -> None:
        ready_at = self.skill_ready.get(skill_id, 0.0)
        if self.now < ready_at - 1e-9:
            return

        event = {
            "skill_id": skill_id,
            "coefficient": float(skill.get("coefficient", 0.0)),
            "source": str(skill.get("source", "skill")),
            "tags": list(skill.get("tags", ["skill"])),
            "target_cap": skill.get("target_cap"),
            "secondary_target_ratio": float(
                skill.get("secondary_target_ratio", 1.0)
            ),
        }
        self._run_triggers("SKILL_CAST_START", event)

        duration = max(
            0.0, float(skill.get("duration", skill.get("action_time", 0.0)) or 0.0)
        )
        if skill.get("blocks_basic_attack") and duration > 0.0:
            self.basic_block_until = max(
                self.basic_block_until, self.now + duration
            )

        hit_count = max(1, int(skill.get("hit_count", 1)))
        for hit_index in range(hit_count):
            hit_event = {**event, "hit_index": hit_index}
            self._run_triggers("SKILL_BEFORE_DAMAGE", hit_event)
            if not hit_event.get("cancel_damage", False):
                self._deal_damage(
                    coefficient=float(hit_event.get("coefficient", 0.0)),
                    hit_count=1,
                    tags=hit_event.get("tags", event["tags"]),
                    can_crit=bool(skill.get("can_crit", True)),
                    source=str(hit_event.get("source", event["source"])),
                    target_cap=hit_event.get("target_cap"),
                    secondary_target_ratio=float(
                        hit_event.get("secondary_target_ratio", 1.0)
                    ),
                )
                self._run_triggers("SKILL_HIT", hit_event)

        cooldown = max(0.0, float(skill.get("cooldown", 0.0) or 0.0))
        if cooldown > 0.0:
            self.skill_ready[skill_id] = self.now + cooldown

        if duration <= 0.0:
            self._run_triggers("SKILL_CAST_END", dict(event))
        else:
            self._schedule(
                self.now + duration, "SKILL_CAST_END", skill_id=skill_id
            )

        self.event_counts["triggered_skill_cast"] += 1
        self.event_counts[f"triggered_skill_cast:{skill_id}"] += 1

    def _cast_skill(self, skill_id: str) -> None:
        skill = (self.core.get("skills") or {}).get(skill_id)
        if not skill:
            return
        if skill.get("kind") == "triggered":
            self._cast_triggered_skill(skill_id, skill)
            return
        super()._cast_skill(skill_id)

    def _process_basic(self) -> None:
        skill = next(
            (
                value
                for value in self.core.get("skills", {}).values()
                if value.get("kind") == "basic"
            ),
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
            "target_cap": skill.get("target_cap"),
            "secondary_target_ratio": float(
                skill.get("secondary_target_ratio", 1.0)
            ),
        }
        self._run_triggers("BASIC_ATTACK_BEFORE_DAMAGE", event)

        if not event.get("cancel_damage", False):
            hit_count = max(1, int(skill.get("hit_count", 1)))
            for hit_index in range(hit_count):
                hit_event = {**event, "hit_index": hit_index}
                self._deal_damage(
                    coefficient=float(hit_event["coefficient"]),
                    hit_count=1,
                    tags=hit_event["tags"],
                    can_crit=bool(skill.get("can_crit", True)),
                    source="basic",
                    target_cap=hit_event.get("target_cap"),
                    secondary_target_ratio=float(
                        hit_event.get("secondary_target_ratio", 1.0)
                    ),
                )
                self._run_triggers("BASIC_ATTACK_HIT", hit_event)

        # A replacement attack still originates from the normal attack timer.
        # Keep the configured basic resource gain; cores that need different
        # semantics can override it in their trigger/state machine.
        resource_gain = skill.get("resource_gain") or {}
        if resource_gain:
            self._apply_action(
                {
                    "type": "add_resource",
                    "resource": resource_gain["resource"],
                    "value": resource_gain.get("value", 0.0),
                    "scale_with_regen": resource_gain.get(
                        "scale_with_regen", False
                    ),
                },
                event,
            )

        self._schedule(
            self.now + self._attack_interval(), "BASIC_ATTACK_READY"
        )
        self._schedule_policy_check(self.now)


# Make legacy functions/classes that resolve their module globals at runtime use
# the extended semantics.  This preserves the historical public API.
_legacy.validate_core = validate_core
_legacy.load_core = load_core
_legacy.list_cores = list_cores
_legacy.HeroCoreSimulator = HeroCoreSimulator

simulate_average = _legacy.simulate_average


def __getattr__(name: str):
    return getattr(_legacy, name)

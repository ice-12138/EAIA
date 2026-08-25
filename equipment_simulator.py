"""Deterministic 60-second direct-damage event simulator for V1.1."""

from __future__ import annotations

import heapq
from collections import defaultdict
from dataclasses import dataclass

from equipment_models import BattleConfig, EquipmentItem, Panel, SimulationResult, Skill, SourceType


@dataclass(order=True, frozen=True)
class CombatEvent:
    time: float
    sequence: int
    skill_id: str


class CombatSimulator:
    def __init__(self, database, rules, panel_calculator):
        self.database = database
        self.rules = rules
        self.panel_calculator = panel_calculator

    @staticmethod
    def _targets(skill: Skill, config: BattleConfig) -> float:
        cap = config.enemy_count if skill.target_cap == "all" else min(config.enemy_count, int(skill.target_cap))
        ratio = config.secondary_target_ratio if config.secondary_target_ratio is not None else skill.secondary_target_ratio
        return 1.0 + max(0, cap - 1) * ratio

    def simulate(self, hero_id: str, items: tuple[EquipmentItem, ...], config: BattleConfig) -> SimulationResult:
        hero = self.database.load_hero(hero_id)
        skills = self.database.load_skills(hero_id)
        panel, active_sets = self.panel_calculator(hero_id, items)
        scenario = self.database.load_scenario("S1") if config.target_def is None else None
        target_def = scenario.target_def if scenario else config.target_def or 0.0
        if not skills:
            raise ValueError(f"Hero {hero_id} has no complete Skills records")
        by_id = {skill.skill_id: skill for skill in skills}
        basic = next((s for s in skills if s.source_type == SourceType.BASIC), None)
        if basic is None:
            raise ValueError(f"Hero {hero_id} has no basic attack skill")
        all_active_effects = [effect for effect in self.database.load_set_effects() if effect.set_id in active_sets]
        active_effects = [effect for effect in all_active_effects if not effect.requires_dot]
        next_allowed = defaultdict(float)
        last_trigger = defaultdict(lambda: -float("inf"))
        active_buffs: dict[str, float] = {}
        uptime_seconds: defaultdict[str, float] = defaultdict(float)
        rage = hero.rage_start
        sequence = 0
        queue: list[CombatEvent] = [CombatEvent(0.0, sequence, basic.skill_id)]
        total = defaultdict(float)
        ult_count = 0
        first_ult = None
        now = 0.0
        crit_factor = (1.0 - panel.crit_rate) + panel.crit_rate * panel.crit_dmg

        while queue:
            event = heapq.heappop(queue)
            now = event.time
            if now >= config.duration:
                break
            skill = by_id[event.skill_id]
            if now < next_allowed[skill.skill_id] - 1e-9:
                continue
            if skill.trigger_event not in {"always", "after_basic", "after_skill", "on_ult"}:
                continue
            if skill.source_type == SourceType.ULTIMATE and rage < skill.rage_cost:
                continue

            if skill.source_type == SourceType.ULTIMATE:
                rage -= skill.rage_cost
                ult_count += 1
                first_ult = now if first_ult is None else first_ult
                for effect in active_effects:
                    if effect.trigger == "on_ult" and effect.duration:
                        active_buffs[effect.effect_id] = now + effect.duration
                        uptime_seconds[effect.effect_id] += min(effect.duration, config.duration - now)

            effective_targets = self._targets(skill, config)
            if skill.direct_damage:
                raw = panel.atk * skill.coefficient * skill.hit_count
                if skill.can_crit:
                    raw *= crit_factor
                source_effect = {
                    SourceType.BASIC.value: "BASIC_DMG",
                    SourceType.SKILL.value: "SKILL_DMG",
                    SourceType.ULTIMATE.value: "ULT_DMG",
                    SourceType.FOLLOWUP.value: "DAMAGE_PCT",
                }.get(skill.source_type.value)
                bonus = sum(effect.value for effect in active_effects if effect.trigger == "always" and effect.effect_type.value == "DAMAGE_PCT")
                if source_effect:
                    bonus += sum(effect.value for effect in active_effects if effect.trigger == "always" and effect.effect_type.value == source_effect)
                for effect in active_effects:
                    if active_buffs.get(effect.effect_id, -1) > now and effect.effect_type.value in {"DAMAGE_PCT", "BASIC_DMG", "SKILL_DMG", "ULT_DMG"} and effect.applies_to in {"all", skill.source_type.value}:
                        bonus += effect.value
                raw *= 1.0 + bonus
                raw *= effective_targets * self.rules.defense_multiplier(target_def)
                total[skill.source_type.value] += raw
            if skill.rage_gain:
                rage = min(hero.rage_max, rage + skill.rage_gain)
            if skill.cooldown is not None:
                next_allowed[skill.skill_id] = now + skill.cooldown
            else:
                next_allowed[skill.skill_id] = now + (self.rules.attack_interval(hero.atk_interval_base, panel.atk_speed) if skill.source_type == SourceType.BASIC else max(skill.action_time, 0.01))

            for followup in skills:
                if followup.trigger_event == ("after_basic" if skill.source_type == SourceType.BASIC else "after_skill" if skill.source_type == SourceType.SKILL else "on_ult") and followup.direct_damage:
                    if now - last_trigger[followup.skill_id] >= followup.internal_cd:
                        last_trigger[followup.skill_id] = now
                        sequence += 1
                        heapq.heappush(queue, CombatEvent(now, sequence, followup.skill_id))

            sequence += 1
            if skill.source_type != SourceType.FOLLOWUP:
                heapq.heappush(queue, CombatEvent(now + max(skill.action_time, 0.01) if skill.source_type != SourceType.BASIC else next_allowed[skill.skill_id], sequence, basic.skill_id))

            # Choose an available skill before the next basic event. The priority field
            # is ordered by the database loader, so the first eligible skill wins.
            for candidate in skills:
                if candidate.source_type in {SourceType.BASIC, SourceType.FOLLOWUP} or candidate.trigger_event != "always":
                    continue
                if now >= next_allowed[candidate.skill_id] and (candidate.source_type != SourceType.ULTIMATE or rage >= candidate.rage_cost):
                    sequence += 1
                    heapq.heappush(queue, CombatEvent(now, sequence, candidate.skill_id))
                    break

        total.setdefault("basic", 0.0)
        total.setdefault("skill", 0.0)
        total.setdefault("ultimate", 0.0)
        total.setdefault("followup", 0.0)
        total.setdefault("dot", 0.0)
        damage = sum(total.values())
        coverage = "partial" if any(not skill.direct_damage for skill in skills) or any(effect.requires_dot for effect in all_active_effects) else "full"
        return SimulationResult(
            tuple(item.item_id for item in items), tuple(item.slot.value for item in items), active_sets,
            config.mode, config.enemy_count, config.duration, panel, damage, damage / config.duration,
            dict(total), ult_count, first_ult,
            {effect_id: min(1.0, seconds / config.duration) for effect_id, seconds in uptime_seconds.items()}, coverage,
        )

"""Deterministic 60-second direct-damage event simulator for V1.1."""

from __future__ import annotations

import heapq
from collections import defaultdict, deque
from dataclasses import dataclass

from equipment_models import BattleConfig, EffectType, EquipmentItem, Panel, SimulationResult, Skill, SourceType


@dataclass(order=True, frozen=True)
class CombatEvent:
    time: float
    sequence: int
    skill_id: str


_DAMAGE_EFFECTS = {
    EffectType.DAMAGE_PCT,
    EffectType.BASIC_DMG,
    EffectType.SKILL_DMG,
    EffectType.ULT_DMG,
    EffectType.SINGLE_DMG,
    EffectType.AOE_DMG,
}
_PANEL_EFFECTS = {
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


class CombatSimulator:
    def __init__(self, database, rules, panel_calculator, set_effects=None):
        self.database = database
        self.rules = rules
        self.panel_calculator = panel_calculator
        self.set_effects = set_effects

    @staticmethod
    def _targets(skill: Skill, config: BattleConfig) -> float:
        cap = config.enemy_count if skill.target_cap == "all" else min(config.enemy_count, int(skill.target_cap))
        ratio = config.secondary_target_ratio if config.secondary_target_ratio is not None else skill.secondary_target_ratio
        return 1.0 + max(0, cap - 1) * ratio

    @staticmethod
    def _is_single_target_skill(skill: Skill) -> bool:
        if skill.target_cap == "all":
            return False
        try:
            return int(skill.target_cap) <= 1
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _effect_matches_skill(effect, skill: Skill) -> bool:
        effect_type = effect.effect_type
        if effect_type == EffectType.DAMAGE_PCT:
            return True
        if effect_type == EffectType.BASIC_DMG:
            return skill.source_type == SourceType.BASIC
        if effect_type == EffectType.SKILL_DMG:
            return skill.source_type == SourceType.SKILL
        if effect_type == EffectType.ULT_DMG:
            return skill.source_type == SourceType.ULTIMATE
        if effect_type == EffectType.SINGLE_DMG:
            return CombatSimulator._is_single_target_skill(skill)
        if effect_type == EffectType.AOE_DMG:
            return not CombatSimulator._is_single_target_skill(skill)
        return False

    @staticmethod
    def _effect_supported(effect) -> bool:
        if effect.requires_dot:
            return False
        if effect.trigger == "always":
            return effect.effect_type in _PANEL_EFFECTS | _DAMAGE_EFFECTS
        if effect.trigger == "on_ult":
            return effect.effect_type in _DAMAGE_EFFECTS | {EffectType.CRIT_DMG}
        if effect.trigger == "on_any_ultimate_cast":
            return effect.effect_type in _DAMAGE_EFFECTS | {EffectType.CRIT_DMG}
        if effect.trigger == "on_crit":
            return (
                (effect.effect_type == EffectType.CRIT_DMG and not effect.duration)
                or (effect.effect_type in _DAMAGE_EFFECTS and bool(effect.duration))
            )
        if effect.trigger == "on_basic_attack_damage":
            return effect.effect_type == EffectType.EXTRA_DAMAGE
        return False

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

        source_effects = self.set_effects if self.set_effects is not None else self.database.load_set_effects()
        all_active_effects = [effect for effect in source_effects if effect.set_id in active_sets]
        active_effects = [effect for effect in all_active_effects if not effect.requires_dot]

        next_allowed = defaultdict(float)
        last_trigger = defaultdict(lambda: -float("inf"))
        active_buffs: dict[str, float] = {}
        stack_counts: defaultdict[str, float] = defaultdict(float)
        # Past crit opportunities are used to compute expected uptime for a
        # duration-based "on crit" proc without random sampling.
        crit_opportunities: deque[float] = deque()
        uptime_seconds: defaultdict[str, float] = defaultdict(float)
        rage = hero.rage_start
        sequence = 0
        queue: list[CombatEvent] = [CombatEvent(0.0, sequence, basic.skill_id)]
        total = defaultdict(float)
        ult_count = 0
        first_ult = None
        now = 0.0

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
                    elif effect.trigger == "on_any_ultimate_cast" and not effect.duration:
                        stack_counts[effect.effect_id] = min(
                            float(effect.max_stacks), stack_counts[effect.effect_id] + 1.0
                        )

            effective_targets = self._targets(skill, config)
            if skill.direct_damage:
                # Dynamic crit-damage effects are computed before this event;
                # this event's own crit opportunity only affects later events.
                dynamic_crit_dmg = 0.0
                for effect in active_effects:
                    if effect.effect_type != EffectType.CRIT_DMG:
                        continue
                    if effect.trigger == "on_crit" and not effect.duration:
                        dynamic_crit_dmg += effect.value * stack_counts[effect.effect_id]
                    elif effect.trigger in {"on_ult", "on_any_ultimate_cast"}:
                        if active_buffs.get(effect.effect_id, -1) > now:
                            dynamic_crit_dmg += effect.value
                        elif effect.trigger == "on_any_ultimate_cast":
                            dynamic_crit_dmg += effect.value * stack_counts[effect.effect_id]

                event_crit_dmg = panel.crit_dmg + dynamic_crit_dmg
                crit_factor = 1.0
                if skill.can_crit:
                    crit_factor = (1.0 - panel.crit_rate) + panel.crit_rate * event_crit_dmg
                raw = panel.atk * skill.coefficient * skill.hit_count * crit_factor

                damage_bonus = 0.0
                for effect in active_effects:
                    if effect.effect_type not in _DAMAGE_EFFECTS or not self._effect_matches_skill(effect, skill):
                        continue
                    if effect.trigger == "always":
                        damage_bonus += effect.value
                    elif effect.trigger == "on_ult" and active_buffs.get(effect.effect_id, -1) > now:
                        damage_bonus += effect.value
                    elif effect.trigger == "on_any_ultimate_cast":
                        damage_bonus += effect.value * stack_counts[effect.effect_id]
                    elif effect.trigger == "on_crit" and effect.duration:
                        cutoff = now - effect.duration
                        while crit_opportunities and crit_opportunities[0] < cutoff:
                            crit_opportunities.popleft()
                        opportunities = len(crit_opportunities)
                        if opportunities and panel.crit_rate > 0:
                            expected_active = 1.0 - (1.0 - panel.crit_rate) ** opportunities
                            damage_bonus += effect.value * expected_active

                raw *= 1.0 + damage_bonus

                # Fixed follow-up damage from sets such as Insight.  It is kept
                # non-critical and outside multiplicative damage bonuses unless
                # the game data later proves otherwise.
                extra_damage = 0.0
                if skill.source_type == SourceType.BASIC and self._is_single_target_skill(skill):
                    extra_damage = sum(
                        effect.value
                        for effect in active_effects
                        if effect.effect_type == EffectType.EXTRA_DAMAGE
                        and effect.trigger == "on_basic_attack_damage"
                    )

                defense_multiplier = self.rules.defense_multiplier(target_def)
                raw = (raw + extra_damage) * effective_targets * defense_multiplier
                total[skill.source_type.value] += raw

                if skill.can_crit and panel.crit_rate > 0:
                    opportunities = max(1, int(skill.hit_count))
                    for _ in range(opportunities):
                        crit_opportunities.append(now)
                    expected_crits = panel.crit_rate * opportunities
                    for effect in active_effects:
                        if effect.trigger == "on_crit" and effect.effect_type == EffectType.CRIT_DMG and not effect.duration:
                            stack_counts[effect.effect_id] = min(
                                float(effect.max_stacks),
                                stack_counts[effect.effect_id] + expected_crits,
                            )

            if skill.rage_gain:
                rage = min(hero.rage_max, rage + skill.rage_gain)
            if skill.cooldown is not None:
                next_allowed[skill.skill_id] = now + skill.cooldown
            else:
                next_allowed[skill.skill_id] = now + (
                    self.rules.attack_interval(hero.atk_interval_base, panel.atk_speed)
                    if skill.source_type == SourceType.BASIC
                    else max(skill.action_time, 0.01)
                )

            for followup in skills:
                trigger = (
                    "after_basic" if skill.source_type == SourceType.BASIC
                    else "after_skill" if skill.source_type == SourceType.SKILL
                    else "on_ult"
                )
                if followup.trigger_event == trigger and followup.direct_damage:
                    if now - last_trigger[followup.skill_id] >= followup.internal_cd:
                        last_trigger[followup.skill_id] = now
                        sequence += 1
                        heapq.heappush(queue, CombatEvent(now, sequence, followup.skill_id))

            sequence += 1
            if skill.source_type != SourceType.FOLLOWUP:
                heapq.heappush(
                    queue,
                    CombatEvent(
                        now + max(skill.action_time, 0.01)
                        if skill.source_type != SourceType.BASIC
                        else next_allowed[skill.skill_id],
                        sequence,
                        basic.skill_id,
                    ),
                )

            # Choose an available skill before the next basic event. The priority field
            # is ordered by the database loader, so the first eligible skill wins.
            for candidate in skills:
                if candidate.source_type in {SourceType.BASIC, SourceType.FOLLOWUP} or candidate.trigger_event != "always":
                    continue
                if now >= next_allowed[candidate.skill_id] and (
                    candidate.source_type != SourceType.ULTIMATE or rage >= candidate.rage_cost
                ):
                    sequence += 1
                    heapq.heappush(queue, CombatEvent(now, sequence, candidate.skill_id))
                    break

        total.setdefault("basic", 0.0)
        total.setdefault("skill", 0.0)
        total.setdefault("ultimate", 0.0)
        total.setdefault("followup", 0.0)
        total.setdefault("dot", 0.0)
        damage = sum(total.values())
        unsupported_effect = any(not self._effect_supported(effect) for effect in all_active_effects)
        coverage = "partial" if (
            any(not skill.direct_damage for skill in skills)
            or any(effect.requires_dot for effect in all_active_effects)
            or unsupported_effect
        ) else "full"
        return SimulationResult(
            tuple(item.item_id for item in items),
            tuple(item.slot.value for item in items),
            active_sets,
            config.mode,
            config.enemy_count,
            config.duration,
            panel,
            damage,
            damage / config.duration,
            dict(total),
            ult_count,
            first_ult,
            {effect_id: min(1.0, seconds / config.duration) for effect_id, seconds in uptime_seconds.items()},
            coverage,
        )

"""Static-panel and V1.1 event-simulation equipment optimizer."""

from __future__ import annotations

import heapq
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from itertools import product

from equipment_db import EquipmentDatabase
from equipment_models import BattleConfig, BuildResult, EquipmentItem, Panel, SimulationResult, StatType
from equipment_simulator import CombatSimulator
from equipment_rules import GameRules


class InsufficientEquipmentError(ValueError):
    pass


@dataclass(frozen=True)
class _ScoredBuild:
    score: float
    result: SimulationResult | BuildResult

    def __lt__(self, other: "_ScoredBuild") -> bool:
        return self.score < other.score


class EquipmentOptimizer:
    def __init__(self, database: EquipmentDatabase, rules: GameRules | None = None):
        self.database = database
        self.rules = rules or GameRules.from_mapping(database.load_rules())

    def _static_effects(self, items: tuple[EquipmentItem, ...]):
        counts = Counter(item.set_id for item in items)
        definitions = self.database.load_sets()
        active = {set_id for set_id, count in counts.items() if set_id in definitions and count >= definitions[set_id].required_pieces}
        effects = [effect for effect in self.database.load_set_effects() if effect.set_id in active and effect.trigger == "always" and not effect.requires_dot]
        return active, effects

    def calculate_panel(self, hero_id: str, items: tuple[EquipmentItem, ...]) -> tuple[Panel, tuple[str, ...]]:
        hero = self.database.load_hero(hero_id)
        active, effects = self._static_effects(items)
        stats = defaultdict(float)
        for item in items:
            for stat in item.stats:
                stats[stat.stat_type.value] += stat.stat_value
        effect_values = defaultdict(float)
        for effect in effects:
            effect_values[effect.effect_type.value] += effect.value

        atk_pct = stats[StatType.ATK_PCT.value] + effect_values["ATK_PCT"]
        atk = self.rules.compose_attack(hero.atk_base, stats[StatType.ATK_FLAT.value], atk_pct)
        hp = (hero.hp_base + stats[StatType.HP_FLAT.value]) * (1.0 + stats[StatType.HP_PCT.value] + effect_values["HP_PCT"])
        defense = (hero.def_base + stats[StatType.DEF_FLAT.value]) * (1.0 + stats[StatType.DEF_PCT.value] + effect_values["DEF_PCT"])
        raw_crit = hero.crit_rate_base + stats[StatType.CRIT_RATE.value] + effect_values["CRIT_RATE"]
        crit, overflow = self.rules.crit(raw_crit)
        panel = Panel(
            atk=atk,
            crit_rate=crit,
            crit_overflow=overflow,
            crit_dmg=hero.crit_dmg_base + stats[StatType.CRIT_DMG.value] + effect_values["CRIT_DMG"],
            atk_speed=hero.atk_speed_base + stats[StatType.ATK_SPEED.value] + effect_values["ATK_SPEED"],
            rage_regen=hero.rage_regen_base + stats[StatType.RAGE_REGEN.value] + effect_values["RAGE_REGEN"],
            hp=hp,
            defense=defense,
            healing_effect=hero.healing_effect_base + stats[StatType.HEALING_EFFECT.value] + effect_values["HEALING_EFFECT"],
        )
        return panel, tuple(sorted(active))

    def build_from_item_ids(self, item_ids: list[str], hero_id: str = "H1", scenario_id: str = "S1") -> BuildResult:
        items = self.database.load_equipment(item_ids)
        if len(items) != len(item_ids):
            raise InsufficientEquipmentError("One or more requested equipment items are unavailable")
        return self._score(hero_id, scenario_id, tuple(items))

    def simulate_build(self, hero_id: str, item_ids: list[str], config: BattleConfig | None = None) -> SimulationResult:
        config = config or BattleConfig()
        items = self.database.load_equipment(item_ids)
        if len(items) != len(item_ids):
            raise InsufficientEquipmentError("One or more requested equipment items are unavailable")
        return CombatSimulator(self.database, self.rules, self.calculate_panel).simulate(hero_id, tuple(items), config)

    def _score(self, hero_id: str, scenario_id: str, items: tuple[EquipmentItem, ...]) -> BuildResult:
        scenario = self.database.load_scenario(scenario_id)
        profile = self.database.load_profile(hero_id, scenario_id)
        panel, active_sets = self.calculate_panel(hero_id, items)
        effects = self.database.load_set_effects()
        general = sum(e.value for e in effects if e.set_id in active_sets and e.effect_type.value == "DAMAGE_PCT")
        source_bonus = {source: general for source in ("basic", "skill", "ultimate")}
        for effect in effects:
            if effect.set_id in active_sets:
                key = {"BASIC_DMG": "basic", "SKILL_DMG": "skill", "ULT_DMG": "ultimate"}.get(effect.effect_type.value)
                if key:
                    source_bonus[key] += effect.value
        crit_factor = (1 - panel.crit_rate) + panel.crit_rate * panel.crit_dmg
        defense_factor = self.rules.defense_multiplier(scenario.target_def)
        target_values = {
            "basic": min(scenario.target_count, profile.expected_targets_basic),
            "skill": min(scenario.target_count, profile.expected_targets_skill),
            "ultimate": min(scenario.target_count, profile.expected_targets_ult),
        }
        shares = {"basic": profile.basic_share, "skill": profile.skill_share, "ultimate": profile.ultimate_share}
        source_damage = {source: panel.atk * share * target_values[source] * crit_factor * (1 + source_bonus[source]) * defense_factor for source, share in shares.items()}
        total = sum(source_damage.values())
        return BuildResult(tuple(item.item_id for item in items), tuple(item.slot.value for item in items), active_sets, panel, total, total / scenario.duration, source_damage)

    def search(self, hero_id: str, mode: str = "single", enemy_count: int = 1, top_k: int = 10) -> list[SimulationResult | BuildResult]:
        if mode.startswith("S"):
            return self._legacy_search(hero_id, mode, top_k)
        items = self.database.load_equipment()
        by_slot = {slot: [item for item in items if item.slot.value == slot] for slot in ("weapon", "armor", "bracelet", "necklace", "ring")}
        if any(not by_slot[slot] for slot in by_slot):
            raise InsufficientEquipmentError("At least one available item is required for every equipment slot")
        left = product(by_slot["weapon"], by_slot["armor"])
        right = tuple(product(by_slot["bracelet"], by_slot["necklace"], by_slot["ring"]))
        heap: list[_ScoredBuild] = []
        for left_items in left:
            for right_items in right:
                build = left_items + right_items
                result = self._simulate_items(hero_id, build, BattleConfig(mode=mode, enemy_count=enemy_count))
                candidate = _ScoredBuild(result.dps, result)
                if len(heap) < top_k:
                    heapq.heappush(heap, candidate)
                elif candidate.score > heap[0].score:
                    heapq.heapreplace(heap, candidate)
        results = [entry.result for entry in sorted(heap, key=lambda entry: entry.score, reverse=True)]
        if results:
            best = results[0].dps
            results = [replace(r, delta_vs_rank1=(best - r.dps) / best if best else 0) for r in results]
        return results

    def _simulate_items(self, hero_id: str, items: tuple[EquipmentItem, ...], config: BattleConfig) -> SimulationResult:
        return CombatSimulator(self.database, self.rules, self.calculate_panel).simulate(hero_id, items, config)

    def _legacy_search(self, hero_id: str, scenario_id: str, top_k: int) -> list[BuildResult]:
        items = self.database.load_equipment()
        by_slot = {slot: [item for item in items if item.slot.value == slot] for slot in ("weapon", "armor", "bracelet", "necklace", "ring")}
        if any(not by_slot[slot] for slot in by_slot):
            raise InsufficientEquipmentError("At least one available item is required for every equipment slot")
        results = [self._score(hero_id, scenario_id, left + right) for left in product(by_slot["weapon"], by_slot["armor"]) for right in product(by_slot["bracelet"], by_slot["necklace"], by_slot["ring"])]
        results.sort(key=lambda result: result.dps, reverse=True)
        results = results[:top_k]
        best = results[0].dps if results else 0
        return [BuildResult(r.item_ids, r.slots, r.active_sets, r.panel, r.total_damage, r.dps, r.source_damage, (best - r.dps) / best if best else 0) for r in results]

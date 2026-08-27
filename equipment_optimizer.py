"""Static-panel and V1.1 event-simulation equipment optimizer."""

from __future__ import annotations

import heapq
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from itertools import product

from equipment_db import EquipmentDatabase
from equipment_models import BattleConfig, BuildResult, EquipmentItem, Panel, SimulationResult, StatType
from equipment_rules import GameRules
from equipment_set_variants import (
    iter_ascension_variants,
    load_optimizer_set_effects,
    load_set_evolutions,
    load_set_names,
)
from equipment_simulator import CombatSimulator


class InsufficientEquipmentError(ValueError):
    pass


@dataclass(frozen=True)
class _ScoredBuild:
    score: float
    result: SimulationResult | BuildResult

    def __lt__(self, other: "_ScoredBuild") -> bool:
        return self.score < other.score


class EquipmentOptimizer:
    """Exact five-slot optimizer with hypothetical T1 -> T2 ascension states.

    A T1 item that appears in ``set_evolutions`` remains unchanged in SQLite.
    During scoring, the physical build is evaluated once with its current set
    identity and again for every mechanically distinct reachable T2 state.  The
    best state is retained for that physical five-item build.  Equal scores
    prefer the current T1 state so the optimizer never recommends a needless
    ascension.
    """

    def __init__(self, database: EquipmentDatabase, rules: GameRules | None = None):
        self.database = database
        self.rules = rules or GameRules.from_mapping(database.load_rules())
        self.refresh_catalog()

    def refresh_catalog(self) -> None:
        """Refresh set/evolution caches after dictionary edits."""
        self._set_definitions = self.database.load_sets()
        self._set_effects = load_optimizer_set_effects(self.database)
        self._set_evolutions = load_set_evolutions(self.database)
        self._set_names = load_set_names(self.database)

    def _static_effects(self, items: tuple[EquipmentItem, ...]):
        counts = Counter(item.set_id for item in items)
        active = {
            set_id
            for set_id, count in counts.items()
            if set_id in self._set_definitions
            and count >= self._set_definitions[set_id].required_pieces
        }
        effects = [
            effect
            for effect in self._set_effects
            if effect.set_id in active and effect.trigger == "always" and not effect.requires_dot
        ]
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

        atk_flat = stats[StatType.ATK_FLAT.value] + effect_values["ATK_FLAT"]
        atk_pct = stats[StatType.ATK_PCT.value] + effect_values["ATK_PCT"]
        atk = self.rules.compose_attack(hero.atk_base, atk_flat, atk_pct)
        hp = (
            hero.hp_base + stats[StatType.HP_FLAT.value] + effect_values["HP_FLAT"]
        ) * (1.0 + stats[StatType.HP_PCT.value] + effect_values["HP_PCT"])
        defense = (
            hero.def_base + stats[StatType.DEF_FLAT.value] + effect_values["DEF_FLAT"]
        ) * (1.0 + stats[StatType.DEF_PCT.value] + effect_values["DEF_PCT"])
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

    def _variant_states(self, items: tuple[EquipmentItem, ...]):
        return iter_ascension_variants(items, self._set_evolutions, self._set_names)

    @staticmethod
    def _best_variant(results: list[SimulationResult | BuildResult]):
        if not results:
            raise ValueError("No equipment calculation variants were produced")
        # On an exact tie, do not spend ascension resources unnecessarily.
        return max(results, key=lambda result: (result.dps, -len(result.ascended_items)))

    def evaluate_build_variants(
        self,
        hero_id: str,
        item_ids: list[str],
        config: BattleConfig | None = None,
    ) -> list[SimulationResult]:
        """Return current-T1 plus every mechanically distinct reachable T2 state."""
        config = config or BattleConfig()
        items = self.database.load_equipment(item_ids)
        if len(items) != len(item_ids):
            raise InsufficientEquipmentError("One or more requested equipment items are unavailable")
        results: list[SimulationResult] = []
        for variant_items, ascensions in self._variant_states(tuple(items)):
            result = self._simulate_items(hero_id, variant_items, config)
            results.append(replace(result, ascended_items=ascensions))
        return sorted(results, key=lambda result: (result.dps, -len(result.ascended_items)), reverse=True)

    def build_from_item_ids(self, item_ids: list[str], hero_id: str = "H1", scenario_id: str = "S1") -> BuildResult:
        items = self.database.load_equipment(item_ids)
        if len(items) != len(item_ids):
            raise InsufficientEquipmentError("One or more requested equipment items are unavailable")
        variants: list[BuildResult] = []
        for variant_items, ascensions in self._variant_states(tuple(items)):
            variants.append(replace(self._score(hero_id, scenario_id, variant_items), ascended_items=ascensions))
        return self._best_variant(variants)

    def simulate_build(self, hero_id: str, item_ids: list[str], config: BattleConfig | None = None) -> SimulationResult:
        """Simulate a physical build and return its best current/ascended state."""
        return self._best_variant(self.evaluate_build_variants(hero_id, item_ids, config))

    def _score(self, hero_id: str, scenario_id: str, items: tuple[EquipmentItem, ...]) -> BuildResult:
        scenario = self.database.load_scenario(scenario_id)
        profile = self.database.load_profile(hero_id, scenario_id)
        panel, active_sets = self.calculate_panel(hero_id, items)
        effects = [
            effect
            for effect in self._set_effects
            if effect.set_id in active_sets and effect.trigger == "always" and not effect.requires_dot
        ]
        general = sum(e.value for e in effects if e.effect_type.value == "DAMAGE_PCT")
        source_bonus = {source: general for source in ("basic", "skill", "ultimate")}
        for effect in effects:
            key = {
                "BASIC_DMG": "basic",
                "SKILL_DMG": "skill",
                "ULT_DMG": "ultimate",
            }.get(effect.effect_type.value)
            if key:
                source_bonus[key] += effect.value
        if scenario.target_mode == "single":
            single = sum(e.value for e in effects if e.effect_type.value == "SINGLE_DMG")
            for source in source_bonus:
                source_bonus[source] += single
        else:
            aoe = sum(e.value for e in effects if e.effect_type.value == "AOE_DMG")
            for source in source_bonus:
                source_bonus[source] += aoe

        crit_factor = (1 - panel.crit_rate) + panel.crit_rate * panel.crit_dmg
        defense_factor = self.rules.defense_multiplier(scenario.target_def)
        target_values = {
            "basic": min(scenario.target_count, profile.expected_targets_basic),
            "skill": min(scenario.target_count, profile.expected_targets_skill),
            "ultimate": min(scenario.target_count, profile.expected_targets_ult),
        }
        shares = {"basic": profile.basic_share, "skill": profile.skill_share, "ultimate": profile.ultimate_share}
        source_damage = {
            source: panel.atk * share * target_values[source] * crit_factor * (1 + source_bonus[source]) * defense_factor
            for source, share in shares.items()
        }
        total = sum(source_damage.values())
        return BuildResult(
            tuple(item.item_id for item in items),
            tuple(item.slot.value for item in items),
            active_sets,
            panel,
            total,
            total / scenario.duration,
            source_damage,
        )

    def search(self, hero_id: str, mode: str = "single", enemy_count: int = 1, top_k: int = 10) -> list[SimulationResult | BuildResult]:
        if mode.startswith("S"):
            return self._legacy_search(hero_id, mode, top_k)
        items = self.database.load_equipment()
        by_slot = {
            slot: [item for item in items if item.slot.value == slot]
            for slot in ("weapon", "armor", "bracelet", "necklace", "ring")
        }
        if any(not by_slot[slot] for slot in by_slot):
            raise InsufficientEquipmentError("At least one available item is required for every equipment slot")
        left = product(by_slot["weapon"], by_slot["armor"])
        right = tuple(product(by_slot["bracelet"], by_slot["necklace"], by_slot["ring"]))
        heap: list[_ScoredBuild] = []
        config = BattleConfig(mode=mode, enemy_count=enemy_count)
        for left_items in left:
            for right_items in right:
                physical_build = left_items + right_items
                variants: list[SimulationResult] = []
                for variant_items, ascensions in self._variant_states(physical_build):
                    result = self._simulate_items(hero_id, variant_items, config)
                    variants.append(replace(result, ascended_items=ascensions))
                result = self._best_variant(variants)
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
        return CombatSimulator(
            self.database,
            self.rules,
            self.calculate_panel,
            set_effects=self._set_effects,
        ).simulate(hero_id, items, config)

    def _legacy_search(self, hero_id: str, scenario_id: str, top_k: int) -> list[BuildResult]:
        items = self.database.load_equipment()
        by_slot = {
            slot: [item for item in items if item.slot.value == slot]
            for slot in ("weapon", "armor", "bracelet", "necklace", "ring")
        }
        if any(not by_slot[slot] for slot in by_slot):
            raise InsufficientEquipmentError("At least one available item is required for every equipment slot")
        results: list[BuildResult] = []
        for left in product(by_slot["weapon"], by_slot["armor"]):
            for right in product(by_slot["bracelet"], by_slot["necklace"], by_slot["ring"]):
                physical_build = left + right
                variants: list[BuildResult] = []
                for variant_items, ascensions in self._variant_states(physical_build):
                    variants.append(replace(self._score(hero_id, scenario_id, variant_items), ascended_items=ascensions))
                results.append(self._best_variant(variants))
        results.sort(key=lambda result: result.dps, reverse=True)
        results = results[:top_k]
        best = results[0].dps if results else 0
        return [replace(r, delta_vs_rank1=(best - r.dps) / best if best else 0) for r in results]

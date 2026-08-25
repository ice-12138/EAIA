"""Typed records shared by the equipment database and optimizer."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Slot(StrEnum):
    WEAPON = "weapon"
    ARMOR = "armor"
    BRACELET = "bracelet"
    NECKLACE = "necklace"
    RING = "ring"


class DamageType(StrEnum):
    PHYSICAL = "physical"
    MAGIC = "magic"
    TRUE = "true"


class MainOutput(StrEnum):
    SINGLE = "single"
    AOE = "aoe"
    MIXED = "mixed"


class SourceType(StrEnum):
    BASIC = "basic"
    SKILL = "skill"
    ULTIMATE = "ultimate"
    FOLLOWUP = "followup"


class StatType(StrEnum):
    ATK_FLAT = "ATK_FLAT"
    ATK_PCT = "ATK_PCT"
    HP_FLAT = "HP_FLAT"
    HP_PCT = "HP_PCT"
    DEF_FLAT = "DEF_FLAT"
    DEF_PCT = "DEF_PCT"
    CRIT_RATE = "CRIT_RATE"
    CRIT_DMG = "CRIT_DMG"
    ATK_SPEED = "ATK_SPEED"
    RAGE_REGEN = "RAGE_REGEN"
    HEALING_EFFECT = "HEALING_EFFECT"


class EffectType(StrEnum):
    ATK_PCT = "ATK_PCT"
    HP_PCT = "HP_PCT"
    DEF_PCT = "DEF_PCT"
    CRIT_RATE = "CRIT_RATE"
    CRIT_DMG = "CRIT_DMG"
    ATK_SPEED = "ATK_SPEED"
    RAGE_REGEN = "RAGE_REGEN"
    HEALING_EFFECT = "HEALING_EFFECT"
    DAMAGE_PCT = "DAMAGE_PCT"
    BASIC_DMG = "BASIC_DMG"
    SKILL_DMG = "SKILL_DMG"
    ULT_DMG = "ULT_DMG"
    PENETRATION = "PENETRATION"


@dataclass(frozen=True)
class Hero:
    hero_id: str
    hero_name: str
    atk_base: float
    crit_rate_base: float
    crit_dmg_base: float
    atk_speed_base: float
    atk_interval_base: float | None
    rage_start: float
    rage_max: float
    damage_type: DamageType
    main_output: MainOutput
    hp_base: float = 0.0
    def_base: float = 0.0
    rage_regen_base: float = 0.0
    healing_effect_base: float = 0.0


@dataclass(frozen=True)
class Skill:
    hero_id: str
    skill_id: str
    skill_name: str
    source_type: SourceType
    scaling_stat: str
    coefficient: float
    hit_count: int
    hit_interval: float
    target_cap: str
    secondary_target_ratio: float
    can_crit: bool
    cooldown: float | None
    action_time: float
    blocks_basic_attack: bool
    affected_by_atk_speed: bool
    rage_cost: float
    rage_gain: float
    initial_cooldown: float
    priority: int
    trigger_event: str
    internal_cd: float
    direct_damage: bool
    notes: str | None


@dataclass(frozen=True)
class BattleConfig:
    mode: str = "single"
    enemy_count: int = 1
    duration: float = 60.0
    target_def: float | None = None
    secondary_target_ratio: float | None = None

    def __post_init__(self):
        if self.mode not in {"single", "aoe"}:
            raise ValueError("mode must be single or aoe")
        if self.enemy_count < 1:
            raise ValueError("enemy_count must be at least 1")
        if self.duration != 60.0:
            raise ValueError("V1.1 battle duration is fixed at 60 seconds")


@dataclass(frozen=True)
class SimulationResult:
    item_ids: tuple[str, ...]
    slots: tuple[str, ...]
    active_sets: tuple[str, ...]
    mode: str
    enemy_count: int
    duration: float
    panel: "Panel"
    total_damage: float
    dps: float
    source_damage: dict[str, float]
    ultimate_count: int
    first_ultimate_time: float | None
    set_uptime: dict[str, float]
    model_coverage: str
    delta_vs_rank1: float = 0.0


@dataclass(frozen=True)
class DamageProfile:
    hero_id: str
    scenario_id: str
    basic_share: float
    skill_share: float
    ultimate_share: float
    expected_targets_basic: float
    expected_targets_skill: float
    expected_targets_ult: float
    ult_uptime_base: float


@dataclass(frozen=True)
class EquipmentStat:
    item_id: str
    stat_source: str
    stat_type: StatType
    stat_value: float
    stat_index: int = 0


@dataclass(frozen=True)
class EquipmentItem:
    item_id: str
    slot: Slot
    set_id: str
    tier: str | None = None
    level: int | None = None
    locked: bool = False
    available: bool = True
    stats: tuple[EquipmentStat, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class SetDefinition:
    set_id: str
    set_name: str
    required_pieces: int
    slot_group: str | None
    output_set: bool


@dataclass(frozen=True)
class SetEffect:
    set_id: str
    effect_id: str
    effect_type: EffectType
    value: float
    applies_to: str
    trigger: str
    duration: float | None
    max_stacks: int
    stack_rule: str
    proc_chance: float
    internal_cd: float
    condition: str | None
    approximate: bool
    requires_dot: bool = False
    enabled_in_v1_1: bool = True


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    scenario_name: str
    duration: float
    target_mode: str
    target_count: int
    target_def: float
    target_mres: float | None
    spawn_pattern: str
    kill_rate_hint: float
    target_hp: float | None
    weight_primary: float
    weight_secondary: float


@dataclass(frozen=True)
class Panel:
    atk: float
    crit_rate: float
    crit_overflow: float
    crit_dmg: float
    atk_speed: float
    rage_regen: float
    hp: float = 0.0
    defense: float = 0.0
    healing_effect: float = 0.0


@dataclass(frozen=True)
class BuildResult:
    item_ids: tuple[str, ...]
    slots: tuple[str, ...]
    active_sets: tuple[str, ...]
    panel: Panel
    total_damage: float
    dps: float
    source_damage: dict[str, float]
    delta_vs_rank1: float = 0.0

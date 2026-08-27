"""Configurable V0.1 game formulas."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GameRules:
    crit_rate_cap: float = 1.0
    defense_constant: float = 100.0
    attack_interval_base: float = 1.0
    # Equipment/sets expose attack speed as panel points (e.g. +30), not a
    # multiplicative ratio.  Keep the conversion configurable until a more
    # precise game formula is measured; 100 points == +1.0 ratio by default.
    attack_speed_points_per_ratio: float = 100.0

    @classmethod
    def from_mapping(cls, values: dict[str, object]) -> "GameRules":
        return cls(
            crit_rate_cap=float(values.get("crit_rate_cap", 1.0)),
            defense_constant=float(values.get("defense_constant", 100.0)),
            attack_interval_base=float(values.get("attack_interval_base", 1.0)),
            attack_speed_points_per_ratio=float(values.get("attack_speed_points_per_ratio", 100.0)),
        )

    def compose_attack(self, base: float, flat: float, pct: float) -> float:
        return (base + flat) * (1.0 + pct)

    def crit(self, raw_rate: float) -> tuple[float, float]:
        return min(raw_rate, self.crit_rate_cap), max(0.0, raw_rate - self.crit_rate_cap)

    def attack_interval(self, base_interval: float | None, attack_speed: float) -> float:
        base = base_interval or self.attack_interval_base
        scale = max(1e-9, self.attack_speed_points_per_ratio)
        return base / max(0.01, 1.0 + attack_speed / scale)

    def defense_multiplier(self, defense: float) -> float:
        return 1.0 if defense <= 0 else self.defense_constant / (self.defense_constant + defense)

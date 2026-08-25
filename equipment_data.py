"""Validation helpers for imported optimizer data."""

from __future__ import annotations

import csv
from pathlib import Path

from equipment_models import EffectType, StatType


class DataValidationError(ValueError):
    pass


SUPPORTED_EFFECTS = {effect.value for effect in EffectType}
SUPPORTED_STATS = {stat.value for stat in StatType}
PERCENTAGE_STATS = {"ATK_PCT", "HP_PCT", "DEF_PCT", "CRIT_RATE", "CRIT_DMG", "RAGE_REGEN"}


def validate_profile_shares(basic: float, skill: float, ultimate: float) -> None:
    if min(basic, skill, ultimate) < 0 or basic + skill + ultimate <= 0:
        raise DataValidationError("Damage profile shares must be non-negative and have a positive total")


def validate_percentage(name: str, value: float, *, allow_overflow: bool = False) -> None:
    if value < 0 or (not allow_overflow and value > 1):
        raise DataValidationError(f"{name} must be stored as a decimal percentage between 0 and 1")


def validate_effect_type(effect_type: str) -> None:
    if effect_type not in SUPPORTED_EFFECTS:
        raise DataValidationError(f"Unsupported set effect: {effect_type}")


def validate_item_row(row: dict[str, object]) -> None:
    required = {"item_id", "slot", "set_id"}
    missing = sorted(required - row.keys())
    if missing:
        raise DataValidationError(f"Equipment row is missing fields: {', '.join(missing)}")
    if row["slot"] not in {"weapon", "armor", "bracelet", "necklace", "ring"}:
        raise DataValidationError(f"Invalid equipment slot: {row['slot']}")


def validate_stat_row(row: dict[str, object]) -> None:
    stat_type = str(row.get("stat_type"))
    if stat_type not in SUPPORTED_STATS:
        raise DataValidationError(f"Invalid equipment stat type: {stat_type}")
    value = row.get("stat_value")
    if value not in (None, "") and stat_type in PERCENTAGE_STATS:
        validate_percentage(stat_type, float(value))
    source = row.get("stat_source")
    if source not in {"main", "sub"}:
        raise DataValidationError(f"Invalid stat source: {source}")
    index = int(row.get("stat_index", 0))
    if index < 0 or index > 4:
        raise DataValidationError("stat_index must be between 0 and 4")


def validate_damage_profile_row(row: dict[str, object]) -> None:
    validate_profile_shares(float(row["basic_share"]), float(row["skill_share"]), float(row["ultimate_share"]))
    validate_percentage("ult_uptime_base", float(row["ult_uptime_base"]))


def import_equipment_csv(database, path: str | Path) -> int:
    rows = list(csv.DictReader(Path(path).open(encoding="utf-8-sig", newline="")))
    for row in rows:
        validate_item_row(row)
    database.connection.executemany(
        "INSERT INTO equipment(item_id, slot, set_id, tier, level, locked, available) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(r["item_id"], r["slot"], r["set_id"], r.get("tier") or None, int(r["level"]) if r.get("level") else None, int(r.get("locked", "0")), int(r.get("available", "1"))) for r in rows],
    )
    database.connection.commit()
    return len(rows)


def import_equipment_stats_csv(database, path: str | Path) -> int:
    rows = list(csv.DictReader(Path(path).open(encoding="utf-8-sig", newline="")))
    for row in rows:
        validate_stat_row(row)
    database.connection.executemany(
        """INSERT INTO equipment_stats(item_id, stat_index, stat_source, stat_type, stat_value, unlock_level, is_unlocked, roll_grade_id, estimate_override, value_confidence) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [(r["item_id"], int(r.get("stat_index", "0")), r["stat_source"], r["stat_type"], float(r["stat_value"]) if r.get("stat_value") not in (None, "") else None, int(r.get("unlock_level", "0")), int(r.get("is_unlocked", "1")), r.get("roll_grade_id") or None, float(r["estimate_override"]) if r.get("estimate_override") else None, float(r.get("value_confidence", "1"))) for r in rows],
    )
    database.connection.commit()
    return len(rows)

"""Validation helpers for imported optimizer data."""

from __future__ import annotations

import csv
from pathlib import Path

from equipment_models import EffectType


class DataValidationError(ValueError):
    pass


SUPPORTED_EFFECTS = {effect.value for effect in EffectType}


def validate_profile_shares(basic: float, skill: float, ultimate: float) -> None:
    if min(basic, skill, ultimate) < 0 or basic + skill + ultimate <= 0:
        raise DataValidationError("Damage profile shares must be non-negative and have a positive total")


def validate_percentage(name: str, value: float, *, allow_overflow: bool = False) -> None:
    if value < 0 or (not allow_overflow and value > 1):
        raise DataValidationError(f"{name} must be stored as a decimal percentage between 0 and 1")


def validate_effect_type(effect_type: str) -> None:
    if effect_type not in SUPPORTED_EFFECTS:
        raise DataValidationError(f"Unsupported V0.1 set effect: {effect_type}")


def validate_item_row(row: dict[str, object]) -> None:
    required = {"item_id", "slot", "set_id"}
    missing = sorted(required - row.keys())
    if missing:
        raise DataValidationError(f"Equipment row is missing fields: {', '.join(missing)}")
    if row["slot"] not in {"weapon", "armor", "bracelet", "necklace", "ring"}:
        raise DataValidationError(f"Invalid equipment slot: {row['slot']}")


def validate_stat_row(row: dict[str, object]) -> None:
    if row.get("stat_type") not in {"ATK_FLAT", "ATK_PCT", "CRIT_RATE", "CRIT_DMG", "ATK_SPEED", "RAGE_REGEN"}:
        raise DataValidationError(f"Invalid equipment stat type: {row.get('stat_type')}")
    if row.get("stat_type") in {"ATK_PCT", "CRIT_RATE", "CRIT_DMG"}:
        validate_percentage(str(row["stat_type"]), float(row["stat_value"]))


def validate_damage_profile_row(row: dict[str, object]) -> None:
    validate_profile_shares(float(row["basic_share"]), float(row["skill_share"]), float(row["ultimate_share"]))
    validate_percentage("ult_uptime_base", float(row["ult_uptime_base"]))


def import_equipment_csv(database, path: str | Path) -> int:
    """Import Equipment rows with columns matching the documented long-form entity."""
    rows = list(csv.DictReader(Path(path).open(encoding="utf-8-sig", newline="")))
    for row in rows:
        validate_item_row(row)
    database.connection.executemany(
        "INSERT INTO equipment(item_id, slot, set_id, tier, level, locked, available) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(r["item_id"], r["slot"], r["set_id"], r.get("tier") or None, int(r["level"]) if r.get("level") else None,
          int(r.get("locked", "0")), int(r.get("available", "1"))) for r in rows],
    )
    database.connection.commit()
    return len(rows)


def import_equipment_stats_csv(database, path: str | Path) -> int:
    """Import EquipmentStats rows with columns item_id, stat_source, stat_type, stat_value."""
    rows = list(csv.DictReader(Path(path).open(encoding="utf-8-sig", newline="")))
    for row in rows:
        validate_stat_row(row)
    database.connection.executemany(
        "INSERT INTO equipment_stats(item_id, stat_source, stat_type, stat_value) VALUES (?, ?, ?, ?)",
        [(r["item_id"], r["stat_source"], r["stat_type"], float(r["stat_value"])) for r in rows],
    )
    database.connection.commit()
    return len(rows)

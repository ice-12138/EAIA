"""Backend service facade for HeroCore catalog and simulations."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from equipment_db import EquipmentDatabase
from hero_core_engine import HeroCoreError, list_cores, load_core, simulate_average


def hero_core_catalog() -> dict[str, Any]:
    return {"hero_cores": list_cores()}


def hero_core_detail(core_id: str) -> dict[str, Any]:
    return load_core(core_id)


def simulate_hero_core(database_path: str | Path, payload: dict[str, Any]) -> dict[str, Any]:
    core_id = str(payload.get("hero_core_id") or "SUN_WUKONG")
    core = load_core(core_id)
    item_ids = [str(value) for value in (payload.get("item_ids") or []) if value]
    if len(item_ids) > 5 or len(set(item_ids)) != len(item_ids):
        raise HeroCoreError("item_ids must contain at most five unique equipment ids")

    target_def = float(payload.get("target_def", 0.0))
    if target_def < 0:
        raise HeroCoreError("target_def must be non-negative")
    enemy_count = int(payload.get("enemy_count", 1))
    if enemy_count < 1:
        raise HeroCoreError("enemy_count must be at least 1")

    trials = int(payload.get("trials", 64))
    warmup = float(payload.get("warmup", 120.0))
    measurement = float(payload.get("measurement", 600.0))
    if warmup < 0 or measurement <= 0:
        raise HeroCoreError("warmup must be non-negative and measurement must be positive")

    database = EquipmentDatabase(database_path)
    try:
        database.initialize()
        if item_ids:
            items = database.load_equipment(item_ids)
            slots = [item.slot.value for item in items]
            if len(items) != len(item_ids):
                raise HeroCoreError("one or more equipment items are unavailable")
            if len(set(slots)) != len(slots):
                raise HeroCoreError("a build may contain at most one item per equipment slot")
        return simulate_average(
            core,
            database=database,
            item_ids=item_ids,
            target={
                "defense": target_def,
                "control_immune": bool(payload.get("control_immune", True)),
                "enemy_count": enemy_count,
            },
            policy=str(payload.get("policy") or core.get("default_policy") or ""),
            trials=trials,
            warmup=warmup,
            measurement=measurement,
            seed=int(payload.get("seed", 20260828)),
        )
    finally:
        database.close()

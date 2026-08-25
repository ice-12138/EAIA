"""Deterministic randomized equipment validation for the EAIA exact Top-K search.

Official basic-attack coefficients/target caps are read from official_skill_catalog.
Hero base stats are normalized test values, not claimed game stats.
"""

from __future__ import annotations

import argparse
import itertools
import json
import random
import tempfile
from pathlib import Path

from equipment_db import EquipmentDatabase
from equipment_models import BattleConfig
from equipment_optimizer import EquipmentOptimizer
from official_hero_data import load_optimizer_usable_official_basics, seed_official_hero_catalog

SLOTS = ("weapon", "armor", "bracelet", "necklace", "ring")
# ATK_SPEED is intentionally excluded: official public skill text exposes attack-speed
# buffs but not the global panel-points -> attack-interval conversion formula.
RANDOM_STATS = (
    ("ATK_FLAT", 30.0, 220.0),
    ("ATK_PCT", 0.03, 0.35),
    ("CRIT_RATE", 0.02, 0.24),
    ("CRIT_DMG", 0.04, 0.40),
    ("HP_PCT", 0.03, 0.30),
    ("DEF_PCT", 0.03, 0.30),
)


def _insert_context(db: EquipmentDatabase, official_basic) -> str:
    hero_id = f"VAL_{official_basic['hero_key']}"
    damage_type = "physical" if official_basic["hero_key"] == "SILAS" else "magic"
    db.connection.execute(
        """INSERT OR REPLACE INTO heroes(
           hero_id,hero_name,atk_base,crit_rate_base,crit_dmg_base,atk_speed_base,
           atk_interval_base,rage_start,rage_max,damage_type,main_output,
           hp_base,def_base,rage_regen_base,healing_effect_base,notes
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            hero_id, f"{official_basic['hero_name']}·官方技能验证夹具",
            1000.0, 0.05, 1.50, 0.0, 1.0, 0.0, 0.0, damage_type,
            "aoe" if int(official_basic["target_cap"]) > 1 else "single",
            10000.0, 500.0, 0.0, 0.0,
            "NORMALIZED TEST BASE STATS; coefficient/target cap only are official.",
        ),
    )
    db.connection.execute(
        """INSERT OR REPLACE INTO skills(
           hero_id,skill_id,skill_name,source_type,scaling_stat,coefficient,
           hit_count,target_cap,can_crit,cooldown,action_time,rage_cost,rage_gain,
           conditions,hit_interval,secondary_target_ratio,blocks_basic_attack,
           affected_by_atk_speed,initial_cooldown,priority,trigger_event,internal_cd,
           direct_damage,notes
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            hero_id, "BASIC", official_basic["skill_name"], "basic", "ATK",
            float(official_basic["coefficient"]), 1, str(official_basic["target_cap"]), 1,
            None, 0.0, 0.0, 0.0, None, 0.0, 1.0, 0, 1, 0.0, 0,
            "always", 0.0, 1, f"Official source: {official_basic['source_url']}",
        ),
    )
    db.connection.execute(
        """INSERT OR REPLACE INTO scenarios(
           scenario_id,scenario_name,duration,target_mode,target_count,target_def,
           target_mres,spawn_pattern,kill_rate_hint,target_hp,weight_primary,weight_secondary
        ) VALUES ('S1','Validation Dummy',60,'single',1,0,0,'stationary',0,NULL,1,1)"""
    )
    db.connection.execute(
        """INSERT OR REPLACE INTO sets(set_id,set_name,required_pieces,slot_group,output_set)
           VALUES ('VAL_NONE','Validation No Set',99,NULL,0)"""
    )
    db.connection.commit()
    return hero_id


def _generate_random_equipment(db: EquipmentDatabase, rng: random.Random, items_per_slot: int) -> int:
    for slot in SLOTS:
        for item_index in range(items_per_slot):
            item_id = f"VAL_{slot.upper()}_{item_index:02d}"
            db.connection.execute(
                """INSERT OR REPLACE INTO equipment(
                   item_id,slot,set_id,tier,level,locked,available,slot_id,
                   quality_id,enhancement_level,item_locked,source
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (item_id, slot, "VAL_NONE", "mythic_red", 16, 0, 1, slot,
                 "mythic_red", 16, 0, "random_validation"),
            )
            for stat_index, (stat_type, low, high) in enumerate(rng.sample(RANDOM_STATS, 4), 1):
                db.connection.execute(
                    """INSERT OR REPLACE INTO equipment_stats(
                       item_id,stat_index,stat_source,stat_type,stat_value,
                       unlock_level,is_unlocked,value_confidence
                    ) VALUES (?,?,?,?,?,0,1,1)""",
                    (item_id, stat_index, "sub", stat_type, round(rng.uniform(low, high), 6)),
                )
    db.connection.commit()
    return items_per_slot * len(SLOTS)


def _exhaustive(db, optimizer, hero_id: str, top_k: int):
    equipment = db.load_equipment()
    by_slot = {
        slot: [item for item in equipment if item.item_id.startswith("VAL_") and item.slot.value == slot]
        for slot in SLOTS
    }
    scored = []
    for combo in itertools.product(*(by_slot[slot] for slot in SLOTS)):
        ids = [item.item_id for item in combo]
        result = optimizer.simulate_build(
            hero_id, ids, BattleConfig(mode="single", enemy_count=1, target_def=0.0)
        )
        scored.append((result.dps, tuple(ids)))
    scored.sort(key=lambda row: (-row[0], row[1]))
    return scored[:top_k], by_slot


def _replace_index4(db, item_id: str, stat_type: str, value: float):
    db.connection.execute(
        """INSERT OR REPLACE INTO equipment_stats(
           item_id,stat_index,stat_source,stat_type,stat_value,
           unlock_level,is_unlocked,value_confidence
        ) VALUES (?,4,'sub',?,?,0,1,1)""",
        (item_id, stat_type, value),
    )
    db.connection.commit()


def _monotonic_checks(db, optimizer, hero_id: str, by_slot) -> dict[str, bool]:
    ids = [by_slot[slot][0].item_id for slot in SLOTS]
    weapon = ids[0]
    original = db.connection.execute(
        """SELECT stat_source,stat_type,stat_value,unlock_level,is_unlocked,value_confidence
           FROM equipment_stats WHERE item_id=? AND stat_index=4""", (weapon,)
    ).fetchone()

    _replace_index4(db, weapon, "HP_PCT", 0.50)
    hp_only = optimizer.simulate_build(hero_id, ids, BattleConfig(target_def=0.0)).dps
    _replace_index4(db, weapon, "ATK_PCT", 0.50)
    atk_bonus = optimizer.simulate_build(hero_id, ids, BattleConfig(target_def=0.0)).dps

    db.connection.execute("DELETE FROM equipment_stats WHERE item_id=? AND stat_index=4", (weapon,))
    if original:
        db.connection.execute(
            """INSERT INTO equipment_stats(
               item_id,stat_index,stat_source,stat_type,stat_value,
               unlock_level,is_unlocked,value_confidence
            ) VALUES (?,4,?,?,?,?,?,?)""",
            (weapon, original["stat_source"], original["stat_type"], original["stat_value"],
             original["unlock_level"], original["is_unlocked"], original["value_confidence"]),
        )
    db.connection.commit()

    effective_a, overflow_a = optimizer.rules.crit(1.0)
    effective_b, overflow_b = optimizer.rules.crit(1.5)
    return {
        "attack_bonus_beats_non_dps_hp": atk_bonus > hp_only,
        "crit_cap_is_one": abs(effective_a - 1.0) < 1e-12,
        "crit_overflow_does_not_raise_effective_rate": abs(effective_a - effective_b) < 1e-12,
        "crit_overflow_is_reported": overflow_b > overflow_a,
    }


def validate_random_builds(seed: int = 20260825, items_per_slot: int = 4, top_k: int = 10) -> dict:
    if items_per_slot < 2:
        raise ValueError("items_per_slot must be >= 2")
    with tempfile.TemporaryDirectory() as directory:
        db = EquipmentDatabase(Path(directory) / "validation.db")
        try:
            db.initialize()
            seed_official_hero_catalog(db.connection)
            basics = load_optimizer_usable_official_basics(db.connection)
            official = next((row for row in basics if row["hero_key"] == "MORRIGAN"), None)
            if official is None:
                raise RuntimeError("Morrigan official numeric basic attack was not seeded")
            hero_id = _insert_context(db, official)
            item_count = _generate_random_equipment(db, random.Random(seed), items_per_slot)
            optimizer = EquipmentOptimizer(db)

            actual = optimizer.search(hero_id, mode="single", enemy_count=1, top_k=top_k)
            expected, by_slot = _exhaustive(db, optimizer, hero_id, top_k)
            actual_pairs = [(round(row.dps, 10), tuple(row.item_ids)) for row in actual]
            expected_pairs = [(round(dps, 10), ids) for dps, ids in expected]
            top_k_match = actual_pairs == expected_pairs

            best_ids = list(actual[0].item_ids)
            single = optimizer.simulate_build(
                hero_id, best_ids, BattleConfig(mode="single", enemy_count=1, target_def=0.0)
            )
            aoe = optimizer.simulate_build(
                hero_id, best_ids, BattleConfig(mode="aoe", enemy_count=3, target_def=0.0)
            )
            target_cap = int(official["target_cap"])
            expected_ratio = float(min(3, target_cap))
            aoe_ratio = aoe.dps / single.dps if single.dps else 0.0
            monotonic = _monotonic_checks(db, optimizer, hero_id, by_slot)
            all_ok = (
                top_k_match
                and abs(aoe_ratio - expected_ratio) < 1e-9
                and all(monotonic.values())
            )
            return {
                "seed": seed,
                "official_validation_hero": official["hero_name"],
                "official_basic_coefficient": official["coefficient"],
                "official_target_cap": target_cap,
                "items_per_slot": items_per_slot,
                "random_items": item_count,
                "combinations": items_per_slot ** len(SLOTS),
                "top_k": top_k,
                "top_k_exact_match": top_k_match,
                "best_build": best_ids,
                "best_dps_normalized": actual[0].dps,
                "aoe_ratio": aoe_ratio,
                "aoe_ratio_expected": expected_ratio,
                "aoe_target_scaling_ok": abs(aoe_ratio - expected_ratio) < 1e-9,
                "monotonic_checks": monotonic,
                "attack_speed_formula_calibrated": False,
                "attack_speed_validation_note": (
                    "Official public text exposes attack-speed buffs but not the global "
                    "panel-points-to-interval conversion; ATK_SPEED is excluded from this "
                    "numerical correctness run."
                ),
                "all_checks_passed": all_ok,
            }
        finally:
            db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate optimizer using deterministic random Mythic gear")
    parser.add_argument("--seed", type=int, default=20260825)
    parser.add_argument("--items-per-slot", type=int, default=4)
    parser.add_argument("--top-k", type=int, default=10)
    args = parser.parse_args()
    result = validate_random_builds(args.seed, args.items_per_slot, args.top_k)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["all_checks_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

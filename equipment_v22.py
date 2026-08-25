"""V2.2 equipment dictionary schema and seed data support."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path


V22_SCHEMA = """
CREATE TABLE IF NOT EXISTS equipment_categories (
 category_id TEXT PRIMARY KEY, category_name TEXT NOT NULL UNIQUE, description TEXT, sort_order INTEGER
);
CREATE TABLE IF NOT EXISTS equipment_slots (
 slot_id TEXT PRIMARY KEY, slot_name TEXT NOT NULL UNIQUE, slot_group TEXT NOT NULL CHECK(slot_group IN ('left','right')),
 set_piece_group INTEGER, sort_order INTEGER, notes TEXT
);
CREATE TABLE IF NOT EXISTS set_tiers (
 set_tier_id TEXT PRIMARY KEY, set_tier_name TEXT NOT NULL UNIQUE, tier_rank INTEGER NOT NULL, notes TEXT
);
CREATE TABLE IF NOT EXISTS gear_qualities (
 quality_id TEXT PRIMARY KEY, quality_name TEXT NOT NULL UNIQUE, quality_rank INTEGER NOT NULL,
 max_enhancement_level INTEGER, has_special_roll_rule INTEGER NOT NULL DEFAULT 0 CHECK(has_special_roll_rule IN (0,1)), notes TEXT
);
CREATE TABLE IF NOT EXISTS stat_roll_grades (
 roll_grade_id TEXT PRIMARY KEY, roll_grade_name TEXT NOT NULL UNIQUE, grade_rank INTEGER NOT NULL,
 is_max_grade INTEGER NOT NULL DEFAULT 0 CHECK(is_max_grade IN (0,1)), notes TEXT
);
CREATE TABLE IF NOT EXISTS stat_definitions (
 stat_type TEXT PRIMARY KEY, stat_name TEXT NOT NULL UNIQUE, stat_family TEXT, unit_type TEXT, stack_mode TEXT,
 can_main_stat INTEGER NOT NULL DEFAULT 0 CHECK(can_main_stat IN (0,1)), can_sub_stat INTEGER NOT NULL DEFAULT 0 CHECK(can_sub_stat IN (0,1)),
 ocr_priority INTEGER NOT NULL DEFAULT 0, description TEXT, active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1))
);
CREATE TABLE IF NOT EXISTS stat_category_map (
 stat_type TEXT NOT NULL REFERENCES stat_definitions(stat_type), category_id TEXT NOT NULL REFERENCES equipment_categories(category_id),
 relevance_weight REAL NOT NULL DEFAULT 1.0, notes TEXT, PRIMARY KEY(stat_type, category_id)
);
CREATE TABLE IF NOT EXISTS stat_slot_rules (
 slot_id TEXT NOT NULL REFERENCES equipment_slots(slot_id), stat_source TEXT NOT NULL CHECK(stat_source IN ('main','sub')),
 stat_type TEXT NOT NULL REFERENCES stat_definitions(stat_type), allowed INTEGER NOT NULL DEFAULT 1 CHECK(allowed IN (0,1)),
 version TEXT, notes TEXT, PRIMARY KEY(slot_id, stat_source, stat_type)
);
CREATE TABLE IF NOT EXISTS stat_value_ranges (
 range_id INTEGER PRIMARY KEY AUTOINCREMENT, stat_type TEXT NOT NULL REFERENCES stat_definitions(stat_type), stat_source TEXT NOT NULL CHECK(stat_source IN ('main','sub')),
 quality_id TEXT REFERENCES gear_qualities(quality_id), roll_grade_id TEXT REFERENCES stat_roll_grades(roll_grade_id), slot_id TEXT REFERENCES equipment_slots(slot_id),
 set_tier_id TEXT REFERENCES set_tiers(set_tier_id), min_value REAL, max_value REAL, mean_value REAL, median_value REAL,
 observed_min REAL, observed_max REAL, sample_count INTEGER NOT NULL DEFAULT 0, distribution_type TEXT, data_source TEXT, game_version TEXT,
 confidence REAL CHECK(confidence IS NULL OR confidence BETWEEN 0 AND 1), range_status TEXT, notes TEXT
);
CREATE TABLE IF NOT EXISTS stat_roll_probabilities (
 probability_id INTEGER PRIMARY KEY AUTOINCREMENT, quality_id TEXT NOT NULL REFERENCES gear_qualities(quality_id), stat_type TEXT REFERENCES stat_definitions(stat_type),
 set_tier_id TEXT REFERENCES set_tiers(set_tier_id), roll_grade_id TEXT NOT NULL REFERENCES stat_roll_grades(roll_grade_id), probability REAL NOT NULL CHECK(probability BETWEEN 0 AND 1),
 sample_count INTEGER NOT NULL DEFAULT 0, data_source TEXT, game_version TEXT, confidence REAL CHECK(confidence IS NULL OR confidence BETWEEN 0 AND 1), enabled_in_optimizer INTEGER NOT NULL DEFAULT 0, notes TEXT
);
CREATE TABLE IF NOT EXISTS main_stat_max_values (
 quality_id TEXT NOT NULL REFERENCES gear_qualities(quality_id), slot_scope TEXT NOT NULL, stat_type TEXT NOT NULL REFERENCES stat_definitions(stat_type),
 max_enhancement_level INTEGER NOT NULL, max_value_at_level_cap REAL, observed_max REAL, confirmation_count INTEGER NOT NULL DEFAULT 0,
 value_status TEXT NOT NULL CHECK(value_status IN ('unknown','provisional','verified','conflict')), data_source TEXT, game_version TEXT,
 confidence REAL CHECK(confidence IS NULL OR confidence BETWEEN 0 AND 1), notes TEXT, PRIMARY KEY(quality_id, slot_scope, stat_type)
);
CREATE TABLE IF NOT EXISTS set_evolutions (
 from_set_id TEXT NOT NULL, to_set_id TEXT NOT NULL, material_type TEXT, notes TEXT, PRIMARY KEY(from_set_id, to_set_id)
);
CREATE TABLE IF NOT EXISTS special_effect_definitions (
 special_effect_id TEXT PRIMARY KEY, special_type TEXT NOT NULL, special_name TEXT NOT NULL, category_id TEXT REFERENCES equipment_categories(category_id),
 effect_type TEXT, stat_type TEXT REFERENCES stat_definitions(stat_type), value_min REAL, value_max REAL, known_value TEXT, trigger TEXT, duration REAL, condition TEXT, game_version TEXT, notes TEXT
);
CREATE TABLE IF NOT EXISTS equipment_special_effects (
 item_id TEXT NOT NULL REFERENCES equipment(item_id) ON DELETE CASCADE, special_effect_id TEXT NOT NULL REFERENCES special_effect_definitions(special_effect_id),
 rolled_value REAL, target_hero_id TEXT, target_faction_id TEXT, PRIMARY KEY(item_id, special_effect_id)
);
CREATE TABLE IF NOT EXISTS ocr_aliases (
 alias_id INTEGER PRIMARY KEY, entity_type TEXT NOT NULL, entity_key TEXT NOT NULL, canonical_text TEXT NOT NULL, alias_text TEXT NOT NULL,
 normalized_alias TEXT, locale TEXT NOT NULL DEFAULT 'zh-CN', priority INTEGER NOT NULL DEFAULT 0, source TEXT, active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)), notes TEXT
);
CREATE TABLE IF NOT EXISTS ocr_import_queue (
 import_id INTEGER PRIMARY KEY AUTOINCREMENT, source_ref TEXT, raw_ocr_text TEXT, parsed_json TEXT, overall_confidence REAL,
 validation_status TEXT NOT NULL DEFAULT 'pending' CHECK(validation_status IN ('pending','pass','warning','reject')), linked_item_id TEXT REFERENCES equipment(item_id),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, reviewed_at TEXT, notes TEXT
);
CREATE TABLE IF NOT EXISTS ocr_correction_log (
 correction_id INTEGER PRIMARY KEY AUTOINCREMENT, import_id INTEGER NOT NULL REFERENCES ocr_import_queue(import_id), entity_type TEXT, raw_text TEXT,
 predicted_key TEXT, corrected_key TEXT NOT NULL, confidence_before REAL, user_confirmed INTEGER NOT NULL DEFAULT 1 CHECK(user_confirmed IN (0,1)),
 promote_to_alias INTEGER NOT NULL DEFAULT 0 CHECK(promote_to_alias IN (0,1)), created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


V22_ALTER_COLUMNS = {
    "heroes": {
        "hero_class": "TEXT", "hp_base": "REAL", "def_base": "REAL", "rage_regen_base": "REAL NOT NULL DEFAULT 0",
        "healing_effect_base": "REAL NOT NULL DEFAULT 0", "notes": "TEXT",
    },
    "sets": {
        "set_tier_id": "TEXT", "category_id": "TEXT", "active": "INTEGER NOT NULL DEFAULT 1", "game_version": "TEXT", "notes": "TEXT",
    },
    "set_effects": {
        "effect_category_id": "TEXT", "stat_type": "TEXT", "mechanic_class": "TEXT", "game_version": "TEXT", "notes": "TEXT",
        "enabled_in_optimizer": "INTEGER NOT NULL DEFAULT 1",
    },
    "equipment": {
        "slot_id": "TEXT", "quality_id": "TEXT", "enhancement_level": "INTEGER NOT NULL DEFAULT 0", "item_locked": "INTEGER NOT NULL DEFAULT 0",
        "equipped_hero_id": "TEXT", "source": "TEXT", "created_at": "TEXT", "updated_at": "TEXT", "notes": "TEXT", "is_ancient": "INTEGER NOT NULL DEFAULT 0",
    },
    "equipment_stats": {
        "stat_index": "INTEGER NOT NULL DEFAULT 0", "unlock_level": "INTEGER NOT NULL DEFAULT 0", "is_unlocked": "INTEGER NOT NULL DEFAULT 1",
        "roll_grade_id": "TEXT", "estimate_override": "REAL", "value_confidence": "REAL NOT NULL DEFAULT 1.0", "notes": "TEXT",
    },
    "scenarios": {
        "target_count_default": "INTEGER", "target_count_user_input": "INTEGER NOT NULL DEFAULT 0", "targets_stationary": "INTEGER NOT NULL DEFAULT 1", "targets_immortal": "INTEGER NOT NULL DEFAULT 1",
    },
    "game_rules": {"game_version": "TEXT", "source": "TEXT", "confidence": "REAL", "updated_at": "TEXT"},
    "main_stat_max_values": {"max_enhancement_level": "INTEGER", "observed_max": "REAL", "confidence": "REAL", "notes": "TEXT"},
    "stat_value_ranges": {
        "stat_source": "TEXT", "quality_id": "TEXT", "slot_id": "TEXT", "set_tier_id": "TEXT", "min_value": "REAL",
        "max_value": "REAL", "mean_value": "REAL", "median_value": "REAL", "distribution_type": "TEXT", "game_version": "TEXT",
        "confidence": "REAL", "notes": "TEXT",
    },
}


def ensure_v22_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(V22_SCHEMA)
    for table, columns in V22_ALTER_COLUMNS.items():
        existing = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        for name, declaration in columns.items():
            if name not in existing:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")
    connection.executescript("""
    CREATE VIEW IF NOT EXISTS v_set_catalog AS
      SELECT s.set_id, s.set_name, s.set_tier_id, s.required_pieces, s.slot_group, s.category_id,
             e.effect_id, e.effect_type, e.stat_type, e.value, e.applies_to, e.trigger, e.enabled_in_optimizer
      FROM sets s LEFT JOIN set_effects e ON e.set_id = s.set_id;
    DROP VIEW IF EXISTS v_equipment_full;
    CREATE VIEW v_equipment_full AS
      SELECT e.item_id, COALESCE(e.slot_id, e.slot) AS slot_id, e.set_id, COALESCE(e.quality_id, e.tier) AS quality_id,
             e.level, e.enhancement_level, e.available, s.set_name, s.category_id, es.stat_index, es.stat_source,
             COALESCE(es.stat_type, '') AS stat_type, es.stat_value, es.is_unlocked, es.estimate_override, es.value_confidence,
             er.main_stat_name, er.main_stat_value, er.sub_stat_1_name, er.sub_stat_1_value,
             er.sub_stat_2_name, er.sub_stat_2_value, er.sub_stat_3_name, er.sub_stat_3_value,
             er.sub_stat_4_name, er.sub_stat_4_value
      FROM equipment e LEFT JOIN sets s ON s.set_id = e.set_id
      LEFT JOIN equipment_stats es ON es.item_id = e.item_id
      LEFT JOIN equipment_recognition er ON er.item_id = e.item_id;
    """)


def seed_v22_defaults(connection: sqlite3.Connection, data_path: str | Path | None = None) -> None:
    """Seed packaged V2.2 data exported from the design document."""
    path = Path(data_path) if data_path else Path(__file__).with_name("equipment_v22_seed.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    tables = {
        "equipment_categories": data.get("equipment_categories", []), "equipment_slots": data.get("equipment_slots", []),
        "set_tiers": data.get("set_tiers", []), "gear_qualities": data.get("gear_qualities", []), "stat_roll_grades": data.get("stat_roll_grades", []),
        "stat_definitions": data.get("stat_definitions", []), "stat_category_map": data.get("stat_category_map", []), "stat_slot_rules": data.get("stat_slot_rules", []),
        "stat_value_ranges": data.get("stat_value_ranges", []), "main_stat_max_values": data.get("main_stat_max_values", []),
        "sets": data.get("sets", []), "set_effects": data.get("set_effects", []), "ocr_aliases": data.get("ocr_aliases", []),
        "set_evolutions": data.get("set_evolutions", []), "special_effect_definitions": data.get("special_effect_definitions", []),
    }
    for table, rows in tables.items():
        for row in rows:
            available = {info[1] for info in connection.execute(f"PRAGMA table_info({table})")}
            row = {key: value for key, value in row.items() if key in available}
            if not row:
                continue
            if table == "main_stat_max_values":
                row.setdefault("updated_at", "2026-08-25")
                row.setdefault("data_source", "dictionary_v22")
            if table == "stat_value_ranges":
                row.setdefault("updated_at", "2026-08-25")
                row.setdefault("range_status", "unknown")
                row.setdefault("data_source", "pending_measurement")
            columns = list(row)
            values = [None if value in ("", "NULL", "null") else value for value in row.values()]
            placeholders = ",".join("?" for _ in columns)
            quoted_columns = ",".join('"' + column.replace('"', '""') + '"' for column in columns)
            connection.execute(f"INSERT OR IGNORE INTO {table} ({quoted_columns}) VALUES ({placeholders})", values)
            if table == "main_stat_max_values" and row.get("max_value_at_level_cap") is not None:
                connection.execute(
                    "UPDATE main_stat_max_values SET max_value_at_level_cap=? WHERE quality_id=? AND slot_scope=? AND stat_type=? AND max_value_at_level_cap IS NULL",
                    (row["max_value_at_level_cap"], row.get("quality_id"), row.get("slot_scope"), row.get("stat_type")),
                )
    connection.executemany(
        "INSERT OR IGNORE INTO game_rules(rule_key, rule_value, value_type, description, game_version, source) VALUES (?, ?, ?, ?, ?, ?)",
        [("max_hero_level", "60", "number", "当前英雄等级上限", "CN-2026-08", "manual"),
         ("hero_level_mode", "fixed_max", "string", "装备优化只计算满级英雄", "CN-2026-08", "manual"),
         ("use_darkfall", "false", "boolean", "V2.2暂不计算暗陨宝石", "CN-2026-08", "manual")],
    )

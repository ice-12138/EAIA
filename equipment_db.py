"""SQLite persistence for equipment optimizer inputs."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from equipment_models import DamageProfile, DamageType, EffectType, EquipmentItem, EquipmentStat, MainOutput, SetDefinition, SetEffect, Skill, Slot, SourceType, StatType

STAT_TYPES = tuple(stat.value for stat in StatType)
STAT_TYPE_SQL = ",".join(f"'{value}'" for value in STAT_TYPES)


def _field_raw_text(field: object) -> str:
    if isinstance(field, dict):
        return str(field.get("raw_text") or "")
    return str(field or "")


def _field_numeric_value(field: object) -> float | None:
    if isinstance(field, (int, float)):
        return float(field)
    if not isinstance(field, dict):
        return None
    value = field.get("value")
    return None if value is None else float(value)


def _record_stat_type(field: object) -> str | None:
    raw = _field_raw_text(field).upper()
    if "暴击率" in raw or "CRIT_RATE" in raw: return "CRIT_RATE"
    if "暴击伤害" in raw or "CRIT_DMG" in raw: return "CRIT_DMG"
    if "攻速" in raw or "攻击速度" in raw or "ATK_SPEED" in raw: return "ATK_SPEED"
    if "怒气" in raw or "能量回复" in raw or "RAGE_REGEN" in raw: return "RAGE_REGEN"
    if "治疗效果" in raw or "治疗加成" in raw or "HEALING_EFFECT" in raw: return "HEALING_EFFECT"
    if "生命加成" in raw or "HP_PCT" in raw: return "HP_PCT"
    if "生命值" in raw or "HP_FLAT" in raw: return "HP_FLAT"
    if "防御加成" in raw or "DEF_PCT" in raw: return "DEF_PCT"
    if "防御" in raw or "DEF_FLAT" in raw: return "DEF_FLAT"
    if "攻击" in raw and ("%" in raw or "加成" in raw or "ATK_PCT" in raw): return "ATK_PCT"
    if "攻击" in raw or "ATK_FLAT" in raw: return "ATK_FLAT"
    return None


def _quality_id(field: object) -> str | None:
    raw = _field_raw_text(field).strip().lower()
    explicit = field.get("quality_id") or field.get("canonical_id") if isinstance(field, dict) else None
    if explicit: return str(explicit)
    mapping = ((("红色", "神话", "mythic", "red"), "mythic_red"), (("金色", "传奇", "legendary", "gold"), "legendary_gold"), (("紫色", "史诗", "epic", "purple"), "epic_purple"), (("蓝色", "稀有", "rare", "blue"), "rare_blue"))
    for aliases, canonical in mapping:
        if any(alias in raw for alias in aliases): return canonical
    return None


EQUIPMENT_STATS_SCHEMA = f"""
CREATE TABLE equipment_stats (
 item_id TEXT NOT NULL REFERENCES equipment(item_id) ON DELETE CASCADE,
 stat_index INTEGER NOT NULL DEFAULT 0 CHECK(stat_index BETWEEN 0 AND 4),
 stat_source TEXT NOT NULL CHECK(stat_source IN ('main','sub')),
 stat_type TEXT NOT NULL CHECK(stat_type IN ({STAT_TYPE_SQL})),
 stat_value REAL,
 unlock_level INTEGER NOT NULL DEFAULT 0,
 is_unlocked INTEGER NOT NULL DEFAULT 1 CHECK(is_unlocked IN (0,1)),
 roll_grade_id TEXT,
 estimate_override REAL,
 value_confidence REAL NOT NULL DEFAULT 1.0 CHECK(value_confidence BETWEEN 0 AND 1),
 notes TEXT,
 PRIMARY KEY(item_id, stat_index)
);
"""

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS heroes (hero_id TEXT PRIMARY KEY, hero_name TEXT NOT NULL, atk_base REAL NOT NULL CHECK(atk_base >= 0), crit_rate_base REAL NOT NULL CHECK(crit_rate_base BETWEEN 0 AND 1), crit_dmg_base REAL NOT NULL CHECK(crit_dmg_base >= 1), atk_speed_base REAL NOT NULL, atk_interval_base REAL, rage_start REAL NOT NULL DEFAULT 0, rage_max REAL NOT NULL DEFAULT 0, damage_type TEXT NOT NULL CHECK(damage_type IN ('physical','magic','true')), main_output TEXT NOT NULL CHECK(main_output IN ('single','aoe','mixed')));
CREATE TABLE IF NOT EXISTS skills (hero_id TEXT NOT NULL REFERENCES heroes(hero_id), skill_id TEXT NOT NULL, skill_name TEXT NOT NULL, source_type TEXT NOT NULL CHECK(source_type IN ('basic','skill','ultimate','followup')), scaling_stat TEXT NOT NULL, coefficient REAL NOT NULL CHECK(coefficient >= 0), hit_count INTEGER NOT NULL CHECK(hit_count > 0), target_cap TEXT NOT NULL, can_crit INTEGER NOT NULL CHECK(can_crit IN (0,1)), cooldown REAL, action_time REAL, rage_cost REAL, rage_gain REAL, conditions TEXT, hit_interval REAL NOT NULL DEFAULT 0, secondary_target_ratio REAL NOT NULL DEFAULT 1, blocks_basic_attack INTEGER NOT NULL DEFAULT 0, affected_by_atk_speed INTEGER NOT NULL DEFAULT 0, initial_cooldown REAL NOT NULL DEFAULT 0, priority INTEGER NOT NULL DEFAULT 0, trigger_event TEXT NOT NULL DEFAULT 'always', internal_cd REAL NOT NULL DEFAULT 0, direct_damage INTEGER NOT NULL DEFAULT 1, notes TEXT, PRIMARY KEY(hero_id, skill_id));
CREATE TABLE IF NOT EXISTS hero_damage_profiles (hero_id TEXT NOT NULL REFERENCES heroes(hero_id), scenario_id TEXT NOT NULL REFERENCES scenarios(scenario_id), basic_share REAL NOT NULL CHECK(basic_share >= 0), skill_share REAL NOT NULL CHECK(skill_share >= 0), ultimate_share REAL NOT NULL CHECK(ultimate_share >= 0), expected_targets_basic REAL NOT NULL CHECK(expected_targets_basic >= 0), expected_targets_skill REAL NOT NULL CHECK(expected_targets_skill >= 0), expected_targets_ult REAL NOT NULL CHECK(expected_targets_ult >= 0), ult_uptime_base REAL NOT NULL CHECK(ult_uptime_base BETWEEN 0 AND 1), PRIMARY KEY(hero_id, scenario_id), CHECK(basic_share + skill_share + ultimate_share > 0));
CREATE TABLE IF NOT EXISTS sets (set_id TEXT PRIMARY KEY, set_name TEXT NOT NULL, required_pieces INTEGER NOT NULL CHECK(required_pieces > 0), slot_group TEXT, output_set INTEGER NOT NULL DEFAULT 0 CHECK(output_set IN (0,1)));
CREATE TABLE IF NOT EXISTS equipment (item_id TEXT PRIMARY KEY, slot TEXT NOT NULL CHECK(slot IN ('weapon','armor','bracelet','necklace','ring')), set_id TEXT NOT NULL REFERENCES sets(set_id), tier TEXT, level INTEGER, locked INTEGER NOT NULL DEFAULT 0 CHECK(locked IN (0,1)), available INTEGER NOT NULL DEFAULT 1 CHECK(available IN (0,1)));
CREATE TABLE IF NOT EXISTS equipment_stats (item_id TEXT NOT NULL REFERENCES equipment(item_id) ON DELETE CASCADE, stat_index INTEGER NOT NULL DEFAULT 0, stat_source TEXT NOT NULL CHECK(stat_source IN ('main','sub')), stat_type TEXT NOT NULL CHECK(stat_type IN ({STAT_TYPE_SQL})), stat_value REAL, PRIMARY KEY(item_id, stat_index));
CREATE TABLE IF NOT EXISTS set_effects (set_id TEXT NOT NULL REFERENCES sets(set_id) ON DELETE CASCADE, effect_id TEXT NOT NULL, effect_type TEXT NOT NULL, value REAL NOT NULL, applies_to TEXT NOT NULL, trigger TEXT NOT NULL, duration REAL, max_stacks INTEGER NOT NULL DEFAULT 1, stack_rule TEXT NOT NULL DEFAULT 'add', proc_chance REAL NOT NULL DEFAULT 1 CHECK(proc_chance BETWEEN 0 AND 1), internal_cd REAL NOT NULL DEFAULT 0, condition TEXT, approximate INTEGER NOT NULL DEFAULT 0 CHECK(approximate IN (0,1)), requires_dot INTEGER NOT NULL DEFAULT 0 CHECK(requires_dot IN (0,1)), enabled_in_v1_1 INTEGER NOT NULL DEFAULT 1 CHECK(enabled_in_v1_1 IN (0,1)), PRIMARY KEY(set_id, effect_id));
CREATE TABLE IF NOT EXISTS scenarios (scenario_id TEXT PRIMARY KEY, scenario_name TEXT NOT NULL, duration REAL NOT NULL CHECK(duration > 0), target_mode TEXT NOT NULL CHECK(target_mode IN ('single','aoe')), target_count INTEGER NOT NULL CHECK(target_count > 0), target_def REAL NOT NULL DEFAULT 0, target_mres REAL, spawn_pattern TEXT NOT NULL, kill_rate_hint REAL NOT NULL DEFAULT 0, target_hp REAL, weight_primary REAL NOT NULL DEFAULT 1, weight_secondary REAL NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS game_rules (rule_key TEXT PRIMARY KEY, rule_value TEXT NOT NULL, value_type TEXT NOT NULL CHECK(value_type IN ('number','boolean','string','json')), description TEXT);
CREATE TABLE IF NOT EXISTS equipment_recognition (item_id TEXT PRIMARY KEY REFERENCES equipment(item_id) ON DELETE CASCADE, profile TEXT NOT NULL CHECK(profile IN ('exclusive','general')), fully_unlocked INTEGER NOT NULL CHECK(fully_unlocked IN (0,1)), quality_text TEXT, slot_text TEXT, primary_text TEXT, main_stat_name TEXT, main_stat_value REAL, sub_stat_1_name TEXT, sub_stat_1_value REAL, sub_stat_2_name TEXT, sub_stat_2_value REAL, sub_stat_3_name TEXT, sub_stat_3_value REAL, sub_stat_4_name TEXT, sub_stat_4_value REAL, set_name_text TEXT, raw_result TEXT NOT NULL, source_screenshot TEXT, recognized_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS main_stat_max_values (quality_id TEXT NOT NULL, slot_scope TEXT NOT NULL CHECK(slot_scope IN ('weapon','armor','right')), stat_type TEXT NOT NULL, max_value_at_level_cap REAL, observed_value REAL, confirmation_count INTEGER NOT NULL DEFAULT 0 CHECK(confirmation_count >= 0), value_status TEXT NOT NULL CHECK(value_status IN ('unknown','provisional','verified','conflict')), conflict_value REAL, data_source TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(quality_id, slot_scope, stat_type));
CREATE TABLE IF NOT EXISTS stat_value_ranges (stat_type TEXT NOT NULL, roll_grade_id TEXT NOT NULL, observed_min REAL, observed_max REAL, verified_min REAL, verified_max REAL, sample_count INTEGER NOT NULL DEFAULT 0 CHECK(sample_count >= 0), range_status TEXT NOT NULL CHECK(range_status IN ('unknown','provisional','verified')), data_source TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(stat_type, roll_grade_id));
CREATE TABLE IF NOT EXISTS sub_stat_observations (item_id TEXT NOT NULL REFERENCES equipment(item_id) ON DELETE CASCADE, stat_type TEXT NOT NULL, roll_grade_id TEXT NOT NULL, stat_value REAL NOT NULL, data_source TEXT NOT NULL, observed_at TEXT NOT NULL, PRIMARY KEY(item_id, stat_type, roll_grade_id));
CREATE TABLE IF NOT EXISTS ocr_import_queue (queue_id INTEGER PRIMARY KEY AUTOINCREMENT, item_id TEXT, stat_type TEXT, roll_grade_id TEXT, stat_value REAL, data_source TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL);
"""


class EquipmentDatabase:
    def __init__(self, path: str | Path = "data/equipment.db"):
        self.path = Path(path); self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path); self.connection.execute("PRAGMA foreign_keys=ON"); self.connection.row_factory = sqlite3.Row

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True); self.connection.executescript(SCHEMA); self._ensure_equipment_stats_schema(); self._migrate_v11()
        from equipment_v22 import ensure_v22_schema, seed_v22_defaults
        ensure_v22_schema(self.connection); seed_v22_defaults(self.connection); self._ensure_effective_stat_view()
        defaults = [("crit_rate_cap", "1.0", "number", "暴击率上限"), ("attack_pct_mode", '"base_plus_flat"', "string", "攻击百分比合成模式"), ("defense_formula", '"linear"', "string", "防御减伤公式"), ("defense_constant", "100.0", "number", "线性防御公式常数"), ("attack_interval_base", "1.0", "number", "默认攻击间隔")]
        self.connection.executemany("INSERT OR IGNORE INTO game_rules(rule_key, rule_value, value_type, description) VALUES (?, ?, ?, ?)", defaults); self.connection.commit()

    def _ensure_equipment_stats_schema(self) -> None:
        info = self.connection.execute("PRAGMA table_info(equipment_stats)").fetchall(); names = {row[1] for row in info}; pk = [row[1] for row in sorted((row for row in info if row[5]), key=lambda row: row[5])]
        required = {"stat_index", "unlock_level", "is_unlocked", "roll_grade_id", "estimate_override", "value_confidence", "notes"}
        if pk == ["item_id", "stat_index"] and required <= names: return
        rows = self.connection.execute("SELECT rowid AS _rowid, * FROM equipment_stats ORDER BY item_id, rowid").fetchall(); self.connection.execute("ALTER TABLE equipment_stats RENAME TO equipment_stats_legacy"); self.connection.executescript(EQUIPMENT_STATS_SCHEMA)
        next_sub_index: dict[str, int] = {}; used: dict[str, set[int]] = {}
        for row in rows:
            keys = set(row.keys()); item_id = row["item_id"]; source = row["stat_source"]; used.setdefault(item_id, set()); candidate = row["stat_index"] if "stat_index" in keys else None
            if candidate is None or int(candidate) in used[item_id] or int(candidate) < 0 or int(candidate) > 4:
                if source == "main" and 0 not in used[item_id]: candidate = 0
                else:
                    candidate = next_sub_index.get(item_id, 1)
                    while candidate in used[item_id] and candidate <= 4: candidate += 1
                    next_sub_index[item_id] = candidate + 1
            candidate = int(candidate)
            if candidate > 4: continue
            used[item_id].add(candidate)
            self.connection.execute("""INSERT INTO equipment_stats(item_id, stat_index, stat_source, stat_type, stat_value, unlock_level, is_unlocked, roll_grade_id, estimate_override, value_confidence, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (item_id, candidate, source, row["stat_type"], row["stat_value"], row["unlock_level"] if "unlock_level" in keys else 0, row["is_unlocked"] if "is_unlocked" in keys else 1, row["roll_grade_id"] if "roll_grade_id" in keys else None, row["estimate_override"] if "estimate_override" in keys else None, row["value_confidence"] if "value_confidence" in keys else 1.0, row["notes"] if "notes" in keys else None))
        self.connection.execute("DROP TABLE equipment_stats_legacy")

    def _migrate_v11(self) -> None:
        columns = {"skills": {"hit_interval": "REAL NOT NULL DEFAULT 0", "secondary_target_ratio": "REAL NOT NULL DEFAULT 1", "blocks_basic_attack": "INTEGER NOT NULL DEFAULT 0", "affected_by_atk_speed": "INTEGER NOT NULL DEFAULT 0", "initial_cooldown": "REAL NOT NULL DEFAULT 0", "priority": "INTEGER NOT NULL DEFAULT 0", "trigger_event": "TEXT NOT NULL DEFAULT 'always'", "internal_cd": "REAL NOT NULL DEFAULT 0", "direct_damage": "INTEGER NOT NULL DEFAULT 1", "notes": "TEXT"}, "set_effects": {"requires_dot": "INTEGER NOT NULL DEFAULT 0", "enabled_in_v1_1": "INTEGER NOT NULL DEFAULT 1"}}
        for table, additions in columns.items():
            existing = {row[1] for row in self.connection.execute(f"PRAGMA table_info({table})")}
            for name, declaration in additions.items():
                if name not in existing: self.connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")
        learning_columns = {"equipment_recognition": {"main_stat_name": "TEXT", "main_stat_value": "REAL", "sub_stat_1_name": "TEXT", "sub_stat_1_value": "REAL", "sub_stat_2_name": "TEXT", "sub_stat_2_value": "REAL", "sub_stat_3_name": "TEXT", "sub_stat_3_value": "REAL", "sub_stat_4_name": "TEXT", "sub_stat_4_value": "REAL"}, "stat_value_ranges": {"stat_source": "TEXT NOT NULL DEFAULT 'sub'", "quality_id": "TEXT", "slot_id": "TEXT", "set_tier_id": "TEXT", "min_value": "REAL", "max_value": "REAL", "mean_value": "REAL", "median_value": "REAL", "distribution_type": "TEXT", "game_version": "TEXT", "confidence": "REAL", "notes": "TEXT"}, "main_stat_max_values": {"max_enhancement_level": "INTEGER NOT NULL DEFAULT 16", "observed_max": "REAL", "game_version": "TEXT", "confidence": "REAL", "notes": "TEXT", "observed_value": "REAL", "conflict_value": "REAL", "updated_at": "TEXT"}}
        for table, additions in learning_columns.items():
            existing = {row[1] for row in self.connection.execute(f"PRAGMA table_info({table})")}
            for name, declaration in additions.items():
                legacy_name = {"main_stat_name": "主词条", "main_stat_value": "主词条数值", "sub_stat_1_name": "副词条1", "sub_stat_1_value": "副词条1数值", "sub_stat_2_name": "副词条2", "sub_stat_2_value": "副词条2数值", "sub_stat_3_name": "副词条3", "sub_stat_3_value": "副词条3数值", "sub_stat_4_name": "副词条4", "sub_stat_4_value": "副词条4数值"}.get(name)
                if name not in existing and legacy_name in existing: self.connection.execute(f'ALTER TABLE {table} RENAME COLUMN "{legacy_name}" TO {name}'); existing.add(name)
                if name not in existing: self.connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")
        from equipment_persistence import _recognition_attributes
        for row in self.connection.execute("SELECT item_id, raw_result FROM equipment_recognition").fetchall():
            parsed = _recognition_attributes(row["raw_result"]); self.connection.execute("""UPDATE equipment_recognition SET main_stat_name=?, main_stat_value=?, sub_stat_1_name=?, sub_stat_1_value=?, sub_stat_2_name=?, sub_stat_2_value=?, sub_stat_3_name=?, sub_stat_3_value=?, sub_stat_4_name=?, sub_stat_4_value=? WHERE item_id=?""", (*parsed, row["item_id"]))
        self.connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_stat_value_ranges_key ON stat_value_ranges(stat_type, roll_grade_id)")

    def _ensure_effective_stat_view(self) -> None:
        self.connection.execute("DROP VIEW IF EXISTS v_equipment_stat_effective")
        self.connection.execute("""CREATE VIEW v_equipment_stat_effective AS SELECT es.item_id, es.stat_index, es.stat_source, es.stat_type, es.stat_value AS actual_value, es.is_unlocked, es.roll_grade_id, es.estimate_override, es.value_confidence, CASE WHEN es.is_unlocked=1 THEN es.stat_value WHEN es.estimate_override IS NOT NULL THEN es.estimate_override ELSE COALESCE((SELECT r.mean_value FROM stat_value_ranges r WHERE UPPER(r.stat_type)=UPPER(es.stat_type) AND (r.roll_grade_id=es.roll_grade_id OR es.roll_grade_id IS NULL) AND r.mean_value IS NOT NULL ORDER BY r.sample_count DESC LIMIT 1),(SELECT (r.min_value+r.max_value)/2.0 FROM stat_value_ranges r WHERE UPPER(r.stat_type)=UPPER(es.stat_type) AND (r.roll_grade_id=es.roll_grade_id OR es.roll_grade_id IS NULL) AND r.min_value IS NOT NULL AND r.max_value IS NOT NULL ORDER BY r.sample_count DESC LIMIT 1)) END AS effective_value, CASE WHEN es.is_unlocked=1 THEN 'actual' WHEN es.estimate_override IS NOT NULL THEN 'override' ELSE 'estimated' END AS effective_source FROM equipment_stats es""")

    def close(self) -> None: self.connection.close()

    def find_upgrade_match(self, record: dict) -> str | None:
        from equipment_persistence import is_upgrade_of
        rows = self.connection.execute("SELECT item_id, raw_result FROM equipment_recognition ORDER BY recognized_at DESC").fetchall()
        for row in rows:
            if row["item_id"] == record.get("item_id"): continue
            try: previous = json.loads(row["raw_result"])
            except (TypeError, json.JSONDecodeError): continue
            if is_upgrade_of(previous, record): return row["item_id"]
        return None

    def upsert_recognized_equipment(self, record: dict, *, source_screenshot: str | Path | None = None) -> dict:
        from equipment_persistence import build_database_rows
        detected_item_id = str(record["item_id"])
        already_recognized = self.connection.execute(
            "SELECT 1 FROM equipment_recognition WHERE item_id=?", (detected_item_id,)
        ).fetchone() is not None
        matched_item_id = self.find_upgrade_match(record); stored_record = dict(record)
        if matched_item_id is not None: stored_record["item_id"] = matched_item_id
        item, stats, recognition = build_database_rows(stored_record, source_screenshot=source_screenshot); set_name = recognition[16]
        self.connection.execute("""INSERT INTO sets(set_id,set_name,required_pieces,slot_group,output_set) VALUES (?, ?, 1, NULL, 0) ON CONFLICT(set_id) DO UPDATE SET set_name=excluded.set_name""", (item[2], set_name))
        quality_id = _quality_id(stored_record.get("quality")); enhancement_level = item[4] or 0
        self.connection.execute("""INSERT INTO equipment(item_id,slot,set_id,tier,level,locked,available,slot_id,quality_id,enhancement_level,item_locked,source,updated_at) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, 'ocr', datetime('now')) ON CONFLICT(item_id) DO UPDATE SET slot=excluded.slot,set_id=excluded.set_id,tier=excluded.tier,level=excluded.level,locked=excluded.locked,available=1,slot_id=excluded.slot_id,quality_id=COALESCE(excluded.quality_id,equipment.quality_id),enhancement_level=excluded.enhancement_level,item_locked=excluded.item_locked,source='ocr',updated_at=datetime('now')""", (*item[:5], 0, item[1], quality_id, enhancement_level, 0))
        self.connection.execute("DELETE FROM equipment_stats WHERE item_id=?", (item[0],)); self.connection.executemany("""INSERT INTO equipment_stats(item_id,stat_index,stat_source,stat_type,stat_value,unlock_level,is_unlocked) VALUES (?, ?, ?, ?, ?, ?, ?)""", stats)
        self.connection.execute("""INSERT INTO equipment_recognition(item_id,profile,fully_unlocked,quality_text,slot_text,primary_text,main_stat_name,main_stat_value,sub_stat_1_name,sub_stat_1_value,sub_stat_2_name,sub_stat_2_value,sub_stat_3_name,sub_stat_3_value,sub_stat_4_name,sub_stat_4_value,set_name_text,raw_result,source_screenshot,recognized_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now')) ON CONFLICT(item_id) DO UPDATE SET profile=excluded.profile,fully_unlocked=excluded.fully_unlocked,quality_text=excluded.quality_text,slot_text=excluded.slot_text,primary_text=excluded.primary_text,main_stat_name=excluded.main_stat_name,main_stat_value=excluded.main_stat_value,sub_stat_1_name=excluded.sub_stat_1_name,sub_stat_1_value=excluded.sub_stat_1_value,sub_stat_2_name=excluded.sub_stat_2_name,sub_stat_2_value=excluded.sub_stat_2_value,sub_stat_3_name=excluded.sub_stat_3_name,sub_stat_3_value=excluded.sub_stat_3_value,sub_stat_4_name=excluded.sub_stat_4_name,sub_stat_4_value=excluded.sub_stat_4_value,set_name_text=excluded.set_name_text,raw_result=excluded.raw_result,source_screenshot=excluded.source_screenshot,recognized_at=excluded.recognized_at""", recognition)
        self.connection.commit(); self._learn_recognized_stats(stored_record, item_id=item[0])
        matched_upgrade = matched_item_id is not None
        return {"item_id": item[0], "detected_item_id": detected_item_id,
                "matched_upgrade": matched_upgrade, "matched_previous_item_id": matched_item_id,
                "入库类型": "更新" if matched_upgrade else ("重复" if already_recognized else "新入库"),
                "import_action": "updated" if matched_upgrade else ("duplicate" if already_recognized else "created")}

    def _learn_recognized_stats(self, record: dict, *, item_id: str) -> None:
        from main_stat_cap_learner import MainStatCapLearner
        from sub_stat_estimator import SubStatEstimator
        quality = str(record.get("quality_id") or _quality_id(record.get("quality")) or "").lower(); slot = _field_raw_text(record.get("slot")).lower()
        for label, canonical in {"武器":"weapon","护甲":"armor","铠甲":"armor","防具":"armor","手镯":"bracelet","手环":"bracelet","项链":"necklace","戒指":"ring"}.items():
            if label in slot: slot = canonical; break
        level = _field_numeric_value(record.get("enhancement_level", record.get("level"))); primary = record.get("primary") or {}; ptype = _record_stat_type(primary); pvalue = _field_numeric_value(primary)
        if quality == "mythic_red" and level is not None and ptype and pvalue is not None: MainStatCapLearner(self.connection).learn(item_id=item_id, quality_id=quality, slot=slot, stat_type=ptype, enhancement_level=int(level), value=float(pvalue))
        estimator = SubStatEstimator(self.connection)
        for sub in record.get("sub_attributes", []):
            if not isinstance(sub, dict) or sub.get("locked") is True or sub.get("value") in (None, -1): continue
            stype = _record_stat_type(sub); grade = sub.get("roll_grade_id") or sub.get("roll_grade")
            if stype and grade: estimator.observe(item_id=item_id, stat_type=stype, roll_grade_id=str(grade), value=float(sub["value"]), ocr_confidence=float(sub.get("confidence", sub.get("ocr_confidence", 1.0))))

    def seed_minimal_fixture(self) -> None:
        self.connection.execute("INSERT INTO heroes(hero_id,hero_name,atk_base,crit_rate_base,crit_dmg_base,atk_speed_base,atk_interval_base,rage_start,rage_max,damage_type,main_output) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", ("H1","测试英雄",100,0.8,1.5,1,1,0,100,"physical","single")); self.connection.execute("INSERT INTO scenarios(scenario_id,scenario_name,duration,target_mode,target_count,target_def,target_mres,spawn_pattern,kill_rate_hint,target_hp,weight_primary,weight_secondary) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", ("S1","测试场景",10,"single",1,0,0,"static",0,None,1,1)); self.connection.execute("INSERT INTO hero_damage_profiles(hero_id,scenario_id,basic_share,skill_share,ultimate_share,expected_targets_basic,expected_targets_skill,expected_targets_ult,ult_uptime_base) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", ("H1","S1",0.3,0.2,0.5,1,1,1,0.5)); self.connection.execute("INSERT INTO sets(set_id,set_name,required_pieces,slot_group,output_set) VALUES (?, ?, ?, ?, ?)", ("SET_A","测试三件套",3,"right3",1)); self.connection.execute("INSERT INTO sets(set_id,set_name,required_pieces,slot_group,output_set) VALUES (?, ?, ?, ?, ?)", ("SET_B","测试二件套",2,"left2",1)); self.connection.execute("INSERT INTO set_effects(set_id,effect_id,effect_type,value,applies_to,trigger,duration,max_stacks,stack_rule,proc_chance,internal_cd,condition,approximate) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", ("SET_A","atk","ATK_PCT",0.2,"all","always",None,1,"add",1,0,None,0)); self.connection.execute("INSERT INTO set_effects(set_id,effect_id,effect_type,value,applies_to,trigger,duration,max_stacks,stack_rule,proc_chance,internal_cd,condition,approximate,enabled_in_v1_1) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", ("SET_A","ult_buff","DAMAGE_PCT",0.3,"all","on_ult",12.0,1,"refresh",1,0,None,0,1))
        items=[("W1","weapon","SET_B"),("A1","armor","SET_B"),("B1","bracelet","SET_A"),("N1","necklace","SET_A"),("R1","ring","SET_A"),("B2","bracelet","SET_B"),("N2","necklace","SET_B"),("R2","ring","SET_B")]; self.connection.executemany("INSERT INTO equipment(item_id,slot,set_id,available) VALUES (?, ?, ?, 1)", items)
        stats=[("W1",0,"main","ATK_PCT",0.2),("A1",0,"main","CRIT_RATE",0.3),("B1",0,"main","ATK_FLAT",50),("N1",0,"main","CRIT_DMG",0.2),("R1",0,"main","ATK_PCT",0.1),("B2",0,"main","ATK_FLAT",10),("N2",0,"main","ATK_FLAT",10),("R2",0,"main","ATK_FLAT",10)]; self.connection.executemany("INSERT INTO equipment_stats(item_id,stat_index,stat_source,stat_type,stat_value) VALUES (?, ?, ?, ?, ?)", stats); self.connection.commit()

    def seed_full_fixture(self) -> None:
        self.seed_minimal_fixture(); skills=[("H1","BASIC_01","普攻","basic","ATK",1.0,1,0.0,"1",1.0,1,None,0.0,0.0,20.0,0,1,0.0,0,"always",0.0,1,""),("H1","SKILL_01","技能","skill","ATK",1.5,2,0.1,"1",1.0,1,8.0,0.5,0.0,10.0,1,0,0.0,1,"after_basic",0.0,1,""),("H1","ULT_01","终结技","ultimate","ATK",3.0,1,0.0,"all",0.8,1,None,0.0,100.0,0.0,1,1,0.0,2,"always",0.0,1,""),("H1","FOLLOW_01","追击","followup","ATK",0.5,1,0.0,"1",1.0,1,5.0,0.0,0.0,0,1,0.0,0,1,"after_skill",0.0,1,""),("H1","DOT_01","灼烧","skill","ATK",2.0,1,0.0,"1",1.0,0,10.0,0.0,0.0,0,0,0.0,9,1,"always",0.0,0,"DoT excluded")]; self.connection.executemany("INSERT INTO skills(hero_id,skill_id,skill_name,source_type,scaling_stat,coefficient,hit_count,hit_interval,target_cap,secondary_target_ratio,can_crit,cooldown,action_time,rage_cost,rage_gain,blocks_basic_attack,affected_by_atk_speed,initial_cooldown,priority,trigger_event,internal_cd,direct_damage,notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", skills); self.connection.commit()

    def _one(self, sql: str, params: tuple=()) -> sqlite3.Row:
        row=self.connection.execute(sql,params).fetchone()
        if row is None: raise LookupError(f"No database record for query: {sql}")
        return row

    def load_equipment(self, item_ids: list[str] | None=None) -> list[EquipmentItem]:
        query="SELECT * FROM equipment WHERE available=1 AND locked=0"; params: tuple=()
        if item_ids: marks=",".join("?" for _ in item_ids); query+=f" AND item_id IN ({marks})"; params=tuple(item_ids)
        rows=self.connection.execute(query,params).fetchall(); result=[]
        for row in rows:
            stat_rows=self.connection.execute("SELECT * FROM v_equipment_stat_effective WHERE item_id=? ORDER BY stat_index", (row["item_id"],)).fetchall(); stats=tuple(EquipmentStat(r["item_id"],r["stat_source"],StatType(r["stat_type"]),float(r["effective_value"]),int(r["stat_index"])) for r in stat_rows if r["effective_value"] is not None and r["stat_type"] in STAT_TYPES); slot_value=row["slot_id"] if "slot_id" in row.keys() and row["slot_id"] else row["slot"]; tier_value=row["quality_id"] if "quality_id" in row.keys() and row["quality_id"] else row["tier"]; result.append(EquipmentItem(row["item_id"],Slot(slot_value),row["set_id"],tier_value,row["level"],bool(row["locked"]),bool(row["available"]),stats))
        return result

    def load_hero(self, hero_id: str):
        from equipment_models import Hero
        r=self._one("SELECT * FROM heroes WHERE hero_id=?", (hero_id,)); keys=set(r.keys()); return Hero(r["hero_id"],r["hero_name"],r["atk_base"],r["crit_rate_base"],r["crit_dmg_base"],r["atk_speed_base"],r["atk_interval_base"],r["rage_start"],r["rage_max"],DamageType(r["damage_type"]),MainOutput(r["main_output"]),r["hp_base"] if "hp_base" in keys and r["hp_base"] is not None else 0.0,r["def_base"] if "def_base" in keys and r["def_base"] is not None else 0.0,r["rage_regen_base"] if "rage_regen_base" in keys and r["rage_regen_base"] is not None else 0.0,r["healing_effect_base"] if "healing_effect_base" in keys and r["healing_effect_base"] is not None else 0.0)
    def load_profile(self, hero_id: str, scenario_id: str) -> DamageProfile:
        r=self._one("SELECT * FROM hero_damage_profiles WHERE hero_id=? AND scenario_id=?", (hero_id,scenario_id)); return DamageProfile(*(r[k] for k in ("hero_id","scenario_id","basic_share","skill_share","ultimate_share","expected_targets_basic","expected_targets_skill","expected_targets_ult","ult_uptime_base")))
    def load_skills(self, hero_id: str) -> list[Skill]:
        rows=self.connection.execute("SELECT * FROM skills WHERE hero_id=? ORDER BY priority, skill_id", (hero_id,)).fetchall(); return [Skill(r["hero_id"],r["skill_id"],r["skill_name"],SourceType(r["source_type"]),r["scaling_stat"],r["coefficient"],r["hit_count"],r["hit_interval"],r["target_cap"],r["secondary_target_ratio"],bool(r["can_crit"]),r["cooldown"],r["action_time"] or 0.0,bool(r["blocks_basic_attack"]),bool(r["affected_by_atk_speed"]),r["rage_cost"] or 0.0,r["rage_gain"] or 0.0,r["initial_cooldown"] or 0.0,r["priority"],r["trigger_event"],r["internal_cd"] or 0.0,bool(r["direct_damage"]),r["notes"]) for r in rows]
    def load_scenario(self, scenario_id: str):
        from equipment_models import Scenario
        r=self._one("SELECT * FROM scenarios WHERE scenario_id=?", (scenario_id,)); return Scenario(*(r[k] for k in ("scenario_id","scenario_name","duration","target_mode","target_count","target_def","target_mres","spawn_pattern","kill_rate_hint","target_hp","weight_primary","weight_secondary")))
    def load_sets(self) -> dict[str,SetDefinition]: return {r["set_id"]:SetDefinition(r["set_id"],r["set_name"],r["required_pieces"],r["slot_group"],bool(r["output_set"])) for r in self.connection.execute("SELECT * FROM sets")}
    def load_set_effects(self) -> list[SetEffect]:
        supported=",".join(f"'{effect.value}'" for effect in EffectType); return [SetEffect(r["set_id"],r["effect_id"],EffectType(r["effect_type"]),r["value"],r["applies_to"],r["trigger"],r["duration"],r["max_stacks"],r["stack_rule"],r["proc_chance"],r["internal_cd"],r["condition"],bool(r["approximate"]),bool(r["requires_dot"]),bool(r["enabled_in_v1_1"])) for r in self.connection.execute(f"SELECT * FROM set_effects WHERE enabled_in_v1_1=1 AND effect_type IN ({supported})")]
    def load_rules(self) -> dict[str,object]:
        result={}
        for r in self.connection.execute("SELECT * FROM game_rules"):
            if r["value_type"]=="number": result[r["rule_key"]]=float(r["rule_value"])
            elif r["value_type"]=="boolean": result[r["rule_key"]]=r["rule_value"].lower()=="true"
            elif r["value_type"]=="json": result[r["rule_key"]]=json.loads(r["rule_value"])
            else: result[r["rule_key"]]=r["rule_value"].strip('"')
        return result

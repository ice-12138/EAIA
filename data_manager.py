"""Allowlisted user-facing CRUD for EAIA SQLite data."""
from __future__ import annotations
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Mapping

class DataManagerError(ValueError):
    pass

def f(name, label, type="text", **kw): return {"name":name,"label":label,"type":type,**kw}
def spec(title, group, description, fields): return {"title":title,"group":group,"description":description,"fields":fields}
BOOL="boolean"; NUM="number"; TXT="textarea"
EQUIPMENT_STAT_TYPES = (
    "ATK_FLAT", "ATK_PCT", "HP_FLAT", "HP_PCT", "DEF_FLAT", "DEF_PCT",
    "CRIT_RATE", "CRIT_DMG", "ATK_SPEED", "RAGE_REGEN", "HEALING_EFFECT",
)
# Ancient gear is a prefix/variant of Legendary or Mythic rather than a new
# enhancement curve.  The UI exposes explicit choices while persistence keeps
# the canonical base quality plus equipment.is_ancient so main-stat caps remain
# tied to the verified base-quality data.
ANCIENT_QUALITY_CHOICES = {
    "ancient_legendary_gold": ("legendary_gold", "上古传说"),
    "ancient_mythic_red": ("mythic_red", "上古神话"),
}
ANCIENT_UI_BY_BASE = {base: (virtual, name) for virtual, (base, name) in ANCIENT_QUALITY_CHOICES.items()}
_calculability_cache: dict[Path, dict[str, tuple[bool, str | None]]] = {}
_calculability_cache_lock = threading.RLock()
RESOURCE_SPECS={
"equipment_categories":spec("装备类型","装备字典","输出、防御、治疗、增益等装备大类。",[f("category_id","类型ID"),f("category_name","类型名称"),f("description","说明",TXT),f("sort_order","显示顺序",NUM)]),
"equipment_slots":spec("装备部位","装备字典","武器、护甲、手镯、项链、戒指。",[f("slot_id","部位ID"),f("slot_name","部位名称"),f("slot_group","区域"),f("set_piece_group","套装计件组",NUM),f("sort_order","显示顺序",NUM),f("notes","备注",TXT)]),
"set_tiers":spec("套装层级","装备字典","T1/T2 等套装层级。",[f("set_tier_id","层级ID"),f("set_tier_name","层级名称"),f("tier_rank","层级顺序",NUM),f("notes","备注",TXT)]),
"gear_qualities":spec("装备品质","装备字典","品质与强化上限。",[f("quality_id","品质ID"),f("quality_name","品质名称"),f("quality_rank","品质顺序",NUM),f("max_enhancement_level","最高强化等级",NUM),f("has_special_roll_rule","特殊词条规则",BOOL),f("notes","备注",TXT)]),
"stat_roll_grades":spec("副词条档位","属性规则","副词条颜色/档位。",[f("roll_grade_id","档位ID"),f("roll_grade_name","档位名称"),f("grade_rank","档位顺序",NUM),f("is_max_grade","最高档",BOOL),f("notes","备注",TXT)]),
"stat_definitions":spec("属性词条","属性规则","攻击、暴击、暴伤、攻速等标准属性。",[f("stat_type","属性ID"),f("stat_name","属性名称"),f("stat_family","属性类别"),f("unit_type","单位"),f("stack_mode","叠加方式"),f("can_main_stat","可作主词条",BOOL),f("can_sub_stat","可作副词条",BOOL),f("ocr_priority","OCR优先级",NUM),f("description","说明",TXT),f("active","启用",BOOL)]),
"sets":spec("装备套装","套装规则","套装名称、件数、层级和用途分类。",[
    f("set_id","套装ID",readonly_on_edit=True,readonly_on_create=True), f("set_name","套装名称"),
    f("set_tier_id","套装层级","select",options=[{"value":"inf","label":"inf"},{"value":"T0","label":"T0"},{"value":"T1","label":"T1"},{"value":"T2","label":"T2"},{"value":"T3","label":"T3"}]),
    f("required_pieces","激活件数","select",options=[{"value":2,"label":"2"},{"value":3,"label":"3"}]),
    f("slot_group","部位组","select",options=[{"value":"left","label":"left"},{"value":"right","label":"right"}]),
    f("category_id","装备类型","select",options=[{"value":"output","label":"output"},{"value":"defense","label":"defense"},{"value":"healing","label":"healing"},{"value":"buff","label":"buff"}]),
    f("output_set","输出套装",BOOL), f("active","启用",BOOL,default=True), f("game_version","游戏版本"), f("notes","备注",TXT),
]),
"set_effects":spec("套装效果","套装规则","套装激活后的属性、伤害和触发机制。",[f("set_id","套装ID"),f("effect_id","效果ID"),f("effect_type","效果类型"),f("value","效果数值",NUM),f("applies_to","作用对象"),f("trigger","触发方式"),f("duration","持续时间(s)",NUM),f("max_stacks","最大层数",NUM),f("proc_chance","触发概率",NUM),f("internal_cd","内部CD(s)",NUM),f("condition","触发条件",TXT),f("requires_dot","依赖DoT",BOOL),f("enabled_in_optimizer","优化器启用",BOOL),f("game_version","游戏版本"),f("notes","备注",TXT)]),
"set_evolutions":spec("套装升华","套装规则","T1 到 T2 的升华关系。",[f("from_set_id","原套装"),f("to_set_id","目标套装"),f("material_type","材料类型"),f("notes","备注",TXT)]),
"main_stat_max_values":spec("主词条上限","属性规则","不同品质和部位的满级主词条最大值。",[f("quality_id","品质"),f("slot_scope","部位范围"),f("stat_type","属性"),f("max_enhancement_level","强化上限",NUM),f("max_value_at_level_cap","满级最大值",NUM),f("observed_max","实测最大值",NUM),f("confirmation_count","确认次数",NUM),f("value_status","状态"),f("data_source","数据来源"),f("game_version","游戏版本"),f("confidence","置信度",NUM),f("notes","备注",TXT)]),
"special_effect_definitions":spec("特殊效果","套装规则","专属、异化等特殊装备机制。",[f("special_effect_id","效果ID"),f("special_type","特殊类型"),f("special_name","效果名称"),f("category_id","装备类型"),f("effect_type","效果类型"),f("stat_type","关联属性"),f("value_min","最小值",NUM),f("value_max","最大值",NUM),f("known_value","已知值"),f("trigger","触发方式"),f("duration","持续时间(s)",NUM),f("condition","条件",TXT),f("game_version","游戏版本"),f("notes","备注",TXT)]),
"ocr_aliases":spec("OCR别名","识别规则","常见 OCR 误识别到标准文本的映射。",[f("alias_id","记录ID",NUM,readonly_on_edit=True),f("entity_type","实体类型"),f("entity_key","实体ID"),f("canonical_text","标准文本"),f("alias_text","识别文本"),f("normalized_alias","归一化文本"),f("locale","语言"),f("priority","优先级",NUM),f("source","来源"),f("active","启用",BOOL),f("notes","备注",TXT)]),
"game_rules":spec("游戏计算规则","仿真数据","伤害、暴击、攻速等全局公式参数。",[f("rule_key","规则ID"),f("rule_value","规则值"),f("value_type","值类型"),f("description","规则说明",TXT),f("game_version","游戏版本"),f("source","来源"),f("confidence","置信度",NUM),f("updated_at","更新时间")]),
"scenarios":spec("木桩/战斗场景","仿真数据","单体/群体木桩、防御和目标数量。",[f("scenario_id","场景ID"),f("scenario_name","场景名称"),f("duration","持续时间(s)",NUM),f("target_mode","目标模式"),f("target_count","目标数量",NUM),f("target_def","目标防御",NUM),f("target_mres","目标魔抗",NUM),f("spawn_pattern","刷新模式"),f("target_hp","目标生命",NUM),f("weight_primary","主目标权重",NUM),f("weight_secondary","次目标权重",NUM)]),
"heroes":spec("仿真英雄基础属性","仿真数据","装备优化使用的满级裸装面板。",[f("hero_id","英雄ID"),f("hero_name","英雄名称"),f("hero_class","职业"),f("atk_base","基础攻击",NUM),f("hp_base","基础生命",NUM),f("def_base","基础防御",NUM),f("crit_rate_base","基础暴击率",NUM),f("crit_dmg_base","基础暴伤",NUM),f("atk_speed_base","基础攻速",NUM),f("atk_interval_base","攻击间隔(s)",NUM),f("rage_start","初始怒气",NUM),f("rage_max","怒气上限",NUM),f("rage_regen_base","怒气回复",NUM),f("damage_type","伤害类型"),f("main_output","输出类型"),f("notes","备注",TXT)]),
"skills":spec("仿真技能","仿真数据","通用技能事件模型；复杂英雄优先 HeroCore。",[f("hero_id","英雄ID"),f("skill_id","技能ID"),f("skill_name","技能名称"),f("source_type","技能类型"),f("scaling_stat","缩放属性"),f("coefficient","伤害倍率",NUM),f("hit_count","命中次数",NUM),f("hit_interval","命中间隔(s)",NUM),f("target_cap","目标上限"),f("secondary_target_ratio","次目标倍率",NUM),f("can_crit","可暴击",BOOL),f("cooldown","冷却(s)",NUM),f("action_time","动作时间(s)",NUM),f("blocks_basic_attack","阻塞普攻",BOOL),f("affected_by_atk_speed","受攻速影响",BOOL),f("rage_cost","怒气消耗",NUM),f("rage_gain","怒气获得",NUM),f("initial_cooldown","初始冷却(s)",NUM),f("priority","释放优先级",NUM),f("trigger_event","触发事件"),f("internal_cd","内部CD(s)",NUM),f("direct_damage","直接伤害",BOOL),f("notes","备注",TXT)]),
}

def resource_catalog(): return [{"id":k,**{x:v[x] for x in ("title","group","description")}} for k,v in RESOURCE_SPECS.items()]
@contextmanager
def _connect(db):
    c=sqlite3.connect(db)
    c.row_factory=sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    try:
        yield c
    finally:
        c.close()
def _spec(r):
    if r not in RESOURCE_SPECS: raise DataManagerError(f"不可管理的数据类型: {r}")
    return RESOURCE_SPECS[r]
def _cols(c,t): return {r["name"]:r for r in c.execute(f'PRAGMA table_info("{t}")')}
def _pks(c,r):
    cols=_cols(c,r); keys=[n for n,row in sorted(cols.items(),key=lambda x:x[1]["pk"] or 999) if row["pk"]]
    if not keys: raise DataManagerError(f"{_spec(r)['title']} 没有可安全定位的主键")
    return keys

def list_resource(db,r):
    s=_spec(r)
    with _connect(db) as c:
        cols=_cols(c,r)
        if not cols: raise DataManagerError(f"数据表不存在: {r}")
        fields=[x for x in s["fields"] if x["name"] in cols]
        names=[x["name"] for x in fields]
        rows=[dict(x) for x in c.execute(f'SELECT {",".join(f"\"{n}\"" for n in names)} FROM "{r}"')]
        keys=_pks(c,r)
    return {"resource":r,"title":s["title"],"description":s["description"],"fields":fields,"primary_keys":keys,"rows":rows}

def _norm(c,r,values,creating):
    s=_spec(r); cols=_cols(c,r); allowed={x["name"] for x in s["fields"]}&set(cols); out={}
    for n,v in values.items():
        if n not in allowed: continue
        col=cols[n]
        if v=="" and not col["notnull"]: v=None
        if v is None and creating and col["pk"] and str(col["type"]).upper().startswith("INTEGER"): continue
        out[n]=v
    if not out: raise DataManagerError("没有可写入的字段")
    return out

def _new_set_id(c):
    while True:
        value = f"set_user_{uuid.uuid4().hex[:16]}"
        if not c.execute("SELECT 1 FROM sets WHERE set_id=?", (value,)).fetchone(): return value

def _where(c,r,key):
    pks=_pks(c,r); missing=[x for x in pks if x not in key]
    if missing: raise DataManagerError("缺少主键: "+",".join(missing))
    clauses=[]; params=[]
    for name in pks:
        if key[name] is None: clauses.append(f'"{name}" IS NULL')
        else: clauses.append(f'"{name}"=?'); params.append(key[name])
    return " AND ".join(clauses),params

def create_resource(db,r,values):
    with _connect(db) as c:
        values = dict(values)
        if r == "sets":
            if not str(values.get("set_id") or "").strip(): values["set_id"] = _new_set_id(c)
            values.setdefault("active", 1)
            if values.get("category_id") == "output": values["output_set"] = 1
        v=_norm(c,r,values,True); names=list(v)
        cur=c.execute(f'INSERT INTO "{r}" ({",".join(f"\"{n}\"" for n in names)}) VALUES ({",".join("?" for _ in names)})',[v[n] for n in names]); c.commit()
        pks=_pks(c,r); key={x:v.get(x) for x in pks}
        if len(pks)==1 and key[pks[0]] is None: key[pks[0]]=cur.lastrowid
    return {"ok":True,"key":key}
def update_resource(db,r,key,values):
    with _connect(db) as c:
        values = dict(values)
        if r == "sets":
            if str(key.get("set_id") or "").strip(): values["set_id"] = key["set_id"]
            else: values["set_id"] = _new_set_id(c)
        if r == "sets" and values.get("category_id") == "output": values["output_set"] = 1
        v=_norm(c,r,values,False); where,params=_where(c,r,key)
        cur=c.execute(f'UPDATE "{r}" SET {",".join(f"\"{n}\"=?" for n in v)} WHERE {where}',[*v.values(),*params])
        if not cur.rowcount: raise DataManagerError("记录不存在或已被删除")
        c.commit()
    return {"ok":True}
def delete_resource(db,r,key):
    with _connect(db) as c:
        where,params=_where(c,r,key); cur=c.execute(f'DELETE FROM "{r}" WHERE {where}',params)
        if not cur.rowcount: raise DataManagerError("记录不存在或已被删除")
        c.commit()
    return {"ok":True}

def _calculate_equipment(db, item_ids=None):
    """Calculate projection eligibility for all or selected equipment IDs."""
    from optimizer_projection import OptimizerEquipmentDatabase

    db = Path(db).resolve()
    optimizer = OptimizerEquipmentDatabase(db, percentile=0.60)
    try:
        optimizer.initialize()
        selected = set(item_ids) if item_ids is not None else None
        if selected is None:
            optimizer.load_equipment()
        else:
            for item_id in selected:
                try:
                    optimizer.load_equipment([item_id])
                except Exception:
                    pass
        exclusions = dict(optimizer.projection_exclusions)
    finally:
        optimizer.close()

    with _connect(db) as connection:
        query = "SELECT item_id, available, locked FROM equipment"
        params = ()
        if selected is not None:
            marks = ",".join("?" for _ in selected)
            query += f" WHERE item_id IN ({marks})"
            params = tuple(selected)
        equipment_rows = connection.execute(query, params).fetchall()

    results = {}
    for row in equipment_rows:
        item_id = str(row["item_id"])
        if not bool(row["available"]):
            reason = "available=0: equipment is disabled"
            calculable = False
        elif bool(row["locked"]):
            reason = "locked=1: equipment is excluded from HeroCore"
            calculable = False
        else:
            reason = exclusions.get(item_id)
            calculable = item_id not in exclusions
            if not calculable and reason is None:
                reason = "equipment is outside the HeroCore projection range"
        results[item_id] = (calculable, reason)
    return db, results


def initialize_equipment_calculability(db):
    """Warm the process-local HeroCore eligibility cache once at service start."""
    path, results = _calculate_equipment(db)
    with _calculability_cache_lock:
        _calculability_cache[path] = results


def refresh_equipment_calculability(db, item_id):
    """Refresh one equipment item's HeroCore eligibility after a mutation."""
    path, results = _calculate_equipment(db, [str(item_id)])
    with _calculability_cache_lock:
        cache = _calculability_cache.setdefault(path, {})
        cache.pop(str(item_id), None)
        cache.update(results)


def _annotate_hero_core_calculability(db, rows):
    """Annotate inventory rows from the cached HeroCore projection gate."""
    path = Path(db).resolve()
    with _calculability_cache_lock:
        missing = [row["item_id"] for row in rows if row["item_id"] not in _calculability_cache.get(path, {})]
    if missing:
        # This is only a compatibility fallback for callers that do not start
        # the HTTP service through create_server().
        if len(missing) == len(rows):
            initialize_equipment_calculability(path)
        else:
            for item_id in missing:
                refresh_equipment_calculability(path, item_id)
    with _calculability_cache_lock:
        cached = dict(_calculability_cache.get(path, {}))

    for row in rows:
        item_id = str(row["item_id"])
        calculable, reason = cached.get(item_id, (False, "equipment eligibility has not been calculated"))
        row["hero_core_calculable"] = calculable
        row["hero_core_reason"] = None if calculable else reason
    return rows

def list_equipment(db):
    with _connect(db) as c:
        q="""SELECT e.item_id,COALESCE(e.slot_id,e.slot) slot_id,sl.slot_name,e.set_id,s.set_name,
                    COALESCE(e.quality_id,e.tier) quality_id,q.quality_name,
                    COALESCE(e.enhancement_level,e.level,0) enhancement_level,
                    COALESCE(e.item_locked,e.locked,0) locked,e.available,e.equipped_hero_id,e.source,e.notes,
                    CASE WHEN COALESCE(e.is_ancient,0)=1 OR instr(COALESCE(er.quality_text,''),'上古')>0 THEN 1 ELSE 0 END is_ancient
             FROM equipment e
             LEFT JOIN equipment_slots sl ON sl.slot_id=COALESCE(e.slot_id,e.slot)
             LEFT JOIN sets s ON s.set_id=e.set_id
             LEFT JOIN gear_qualities q ON q.quality_id=COALESCE(e.quality_id,e.tier)
             LEFT JOIN equipment_recognition er ON er.item_id=e.item_id
             ORDER BY e.item_id"""
        items={x["item_id"]:{**dict(x),"stats":[]} for x in c.execute(q)}
        names={str(x["stat_type"]).casefold():x["stat_name"] for x in c.execute("SELECT stat_type,stat_name FROM stat_definitions")}
        for x in c.execute("SELECT item_id,stat_index,stat_source,stat_type,stat_value,unlock_level,is_unlocked,roll_grade_id,estimate_override,value_confidence,notes FROM equipment_stats ORDER BY item_id,stat_index"):
            if x["item_id"] in items:
                st=dict(x)
                raw_type=str(st["stat_type"] or "").strip()
                st["stat_type"]=raw_type.upper()
                st["stat_name"]=names.get(raw_type.casefold(),raw_type)
                # A visible numeric value is the authoritative signal that the
                # stat has already unlocked.  A missing value is treated as
                # locked/unknown even if legacy rows contain an inverted flag.
                st["is_unlocked"]=1 if st["stat_source"]=="main" or st["stat_value"] is not None else 0
                items[x["item_id"]]["stats"].append(st)
        rows=list(items.values())
        for row in rows:
            base_quality=str(row.get("quality_id") or "")
            if row.get("is_ancient") and base_quality in ANCIENT_UI_BY_BASE:
                virtual_id, display_name=ANCIENT_UI_BY_BASE[base_quality]
                row["quality_id"]=virtual_id
                row["quality_name"]=display_name
    return {"rows":_annotate_hero_core_calculability(db,rows)}

def _equipment_values(p):
    item=str(p.get("item_id") or "").strip(); slot=str(p.get("slot_id") or "").strip(); set_id=str(p.get("set_id") or "").strip(); quality=str(p.get("quality_id") or "").strip()
    if not item or not slot or not set_id: raise DataManagerError("装备ID、部位和套装不能为空")
    ancient=bool(p.get("is_ancient",False))
    if quality in ANCIENT_QUALITY_CHOICES:
        quality,_=ANCIENT_QUALITY_CHOICES[quality]
        ancient=True
    level=int(p.get("enhancement_level") or 0); locked=int(bool(p.get("locked",False)))
    return {"item_id":item,"slot":slot,"slot_id":slot,"set_id":set_id,"tier":quality or None,"quality_id":quality or None,"level":level,"enhancement_level":level,"locked":locked,"item_locked":locked,"available":int(bool(p.get("available",True))),"equipped_hero_id":p.get("equipped_hero_id") or None,"source":p.get("source") or "manual","notes":p.get("notes") or None,"is_ancient":int(ancient)}
def _replace_stats(c,item,stats):
    c.execute("DELETE FROM equipment_stats WHERE item_id=?",(item,)); used=set()
    canonical_stats = {value.casefold(): value for value in EQUIPMENT_STAT_TYPES}
    for pos,x in enumerate(stats[:5]):
        if x.get("stat_type") in (None,""): continue
        idx=int(x.get("stat_index",pos))
        if idx in used or not 0<=idx<=4: raise DataManagerError("装备词条位置必须为0-4且不能重复")
        used.add(idx); source=x.get("stat_source") or ("main" if idx==0 else "sub")
        stat_type = str(x["stat_type"]).strip()
        stat_type = canonical_stats.get(stat_type.casefold(), stat_type)
        value=x.get("stat_value")
        if value=="": value=None
        is_unlocked = source=="main" or value is not None
        c.execute("INSERT INTO equipment_stats(item_id,stat_index,stat_source,stat_type,stat_value,unlock_level,is_unlocked,roll_grade_id,estimate_override,value_confidence,notes) VALUES (?,?,?,?,?,?,?,?,?,?,?)",(item,idx,source,stat_type,value,int(x.get("unlock_level") or 0),int(is_unlocked),x.get("roll_grade_id") or None,x.get("estimate_override"),float(x.get("value_confidence",1.0)),x.get("notes") or None))
def save_equipment(db,payload,*,original_item_id=None):
    v=_equipment_values(payload); stats=list(payload.get("stats") or [])
    with _connect(db) as c:
        if original_item_id is None:
            names=list(v); c.execute(f'INSERT INTO equipment ({",".join(f"\"{n}\"" for n in names)}) VALUES ({",".join("?" for _ in names)})',[v[n] for n in names])
        else:
            if v["item_id"]!=original_item_id: raise DataManagerError("装备ID暂不支持直接改名；请新建后删除旧记录")
            names=[n for n in v if n!="item_id"]; cur=c.execute(f'UPDATE equipment SET {",".join(f"\"{n}\"=?" for n in names)} WHERE item_id=?',[v[n] for n in names]+[original_item_id])
            if not cur.rowcount: raise DataManagerError("装备不存在或已被删除")
        _replace_stats(c,v["item_id"],stats); c.commit()
    refresh_equipment_calculability(db, v["item_id"])
    return {"ok":True,"item_id":v["item_id"]}
def delete_equipment(db,item_id):
    if not item_id: raise DataManagerError("缺少装备ID")
    with _connect(db) as c:
        cur=c.execute("DELETE FROM equipment WHERE item_id=?",(item_id,))
        if not cur.rowcount: raise DataManagerError("装备不存在或已被删除")
        c.commit()
    with _calculability_cache_lock:
        cache = _calculability_cache.get(Path(db).resolve())
        if cache is not None:
            cache.pop(item_id, None)
    return {"ok":True}

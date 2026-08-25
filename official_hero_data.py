"""Official hero/skill facts collected from publicly indexed official channels.

The official catalog is intentionally separate from the optimizer's production
``heroes``/``skills`` tables. Many official archive pages expose mechanics or
videos without every numeric coefficient required for exact simulation.
"""

from __future__ import annotations

import json
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS official_hero_catalog (
 hero_key TEXT PRIMARY KEY,
 hero_name TEXT NOT NULL,
 title TEXT,
 faction TEXT,
 role TEXT,
 completeness TEXT NOT NULL CHECK(completeness IN ('numeric_complete','numeric_partial','mechanic_only','identity_only')),
 mechanic_summary TEXT,
 source_url TEXT NOT NULL,
 source_kind TEXT NOT NULL,
 source_date TEXT,
 official_channel TEXT NOT NULL DEFAULT 'TapTap潮汐守望者官方',
 data_version TEXT NOT NULL DEFAULT 'CN-2026-08-25',
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS official_skill_catalog (
 hero_key TEXT NOT NULL REFERENCES official_hero_catalog(hero_key) ON DELETE CASCADE,
 skill_key TEXT NOT NULL,
 skill_name TEXT NOT NULL,
 skill_type TEXT NOT NULL,
 description TEXT,
 coefficient REAL,
 target_cap TEXT,
 duration REAL,
 direct_damage INTEGER CHECK(direct_damage IS NULL OR direct_damage IN (0,1)),
 optimizer_usable INTEGER NOT NULL DEFAULT 0 CHECK(optimizer_usable IN (0,1)),
 source_url TEXT NOT NULL,
 source_date TEXT,
 value_json TEXT,
 notes TEXT,
 PRIMARY KEY(hero_key, skill_key)
);
CREATE INDEX IF NOT EXISTS idx_official_hero_completeness ON official_hero_catalog(completeness);
CREATE INDEX IF NOT EXISTS idx_official_skill_optimizer_usable ON official_skill_catalog(optimizer_usable);
"""

# key, name, title, faction, role, completeness, summary, source_url, source_kind, source_date
HEROES = [
("MORRIGAN","摩瑞甘","黑炎巫","诅咒神教","法师","numeric_partial","群体魔法、诅咒炸弹与持续范围伤害。","https://www.taptap.cn/app/550307/strategy/entity-collection/359824","official_skill_showcase","2024-09-04"),
("HEX","赫克斯","狂乱先知","贯星之箭/炼狱爆破","射手","numeric_partial","魔法普攻、随机卡牌与控制增伤。","https://www.taptap.cn/app/550307/strategy/entity-collection/394670","official_skill_showcase","2024-08-31"),
("VIERNA","维尔娜","黑女王","诅咒神教","法师","numeric_partial","单体魔法普攻与高倍率范围终结技。","https://www.taptap.cn/app/550307/strategy/entity-collection/369089","official_skill_showcase","2024-10-13"),
("ZILITU","兹丽忒","炼狱女王","炼狱爆破","战士","numeric_partial","单体魔法、灼烧与条件真实伤害。","https://www.taptap.cn/app/550307/strategy/entity-collection/360716","official_skill_showcase","2024-08-27"),
("SILAS","西拉斯","盲眼国王","贯星之箭","射手","numeric_partial","单体物理射手，终结技强化攻击并无视防御。","https://www.taptap.cn/app/550307/strategy/entity-collection/369108","official_skill_showcase","2024-10-07"),
("ELOWYN","艾洛温","绿茵圣者","秘法会","医师","numeric_partial","群体治疗、驱散、持续治疗与全场回怒。","https://www.taptap.cn/app/550307/strategy/entity-collection/360737","official_skill_showcase","2024-10-05"),
("DALIA","妲丽亚","死亡之花",None,"法师","numeric_partial","最多10目标的范围魔法普攻与玫瑰引爆机制。","https://www.taptap.cn/app/550307/strategy/entity-collection/360700","official_skill_showcase","2024-11-29"),
("HASU","哈苏","复仇女王",None,"射手","mechanic_only","群攻射手；官方攻略确认减防、隐身与高额AOE。","https://www.taptap.cn/app/550307/strategy/entity-collection/394619","official_archive",None),
("SUN_WUKONG","孙悟空",None,None,"战士","mechanic_only","可召唤最多3个分身，偏单体BOSS输出并带破防/控制机制。","https://www.taptap.cn/app/550307/strategy/entity-collection/391959","official_archive",None),
("BORIS","波瑞斯","冰霜之主",None,None,"identity_only","官方英雄档案与技能展示已公开；索引文本未完整暴露数值。","https://www.taptap.cn/app/550307/strategy/entity-collection/369125","official_archive",None),
("VALDERON","瓦尔德隆","堕圣之王",None,None,"identity_only","官方英雄档案与技能展示已公开。","https://www.taptap.cn/app/550307/strategy/entity-collection/360749","official_archive",None),
("LUCIUS","路修斯",None,None,None,"identity_only","官方英雄档案与技能展示已公开。","https://www.taptap.cn/app/550307/strategy/entity-collection/394584","official_archive",None),
("ANORA","阿诺菈","银色天使",None,None,"mechanic_only","拉波尔分身会守护阿诺菈。","https://www.taptap.cn/moment/631555560038204997","official_archive","2025-01-23"),
("YMIRET","伊米莱特",None,None,None,"identity_only","官方英雄档案与技能展示已公开。","https://www.taptap.cn/app/550307/strategy/entity-collection/386183","official_archive",None),
("ZARIS","扎里斯",None,None,None,"identity_only","官方英雄档案与技能展示已公开。","https://www.taptap.cn/app/550307/strategy/entity-collection/391962","official_archive",None),
("INGRID","英格丽德",None,None,None,"mechanic_only","官方资料提及真实伤害与灼烧。","https://www.taptap.cn/app/550307/strategy/entity-collection/394585","official_archive",None),
("ARES","艾瑞斯",None,None,None,"identity_only","官方英雄档案与技能展示已公开。","https://www.taptap.cn/app/550307/strategy/entity-collection/394593","official_archive",None),
("TALIN","塔琳",None,None,None,"mechanic_only","兼具攻击与熊形态保护、治疗能力。","https://www.taptap.cn/app/550307/strategy/entity-collection/394589","official_archive",None),
("BEELZEBUB","别西卜","腐蝇领主",None,None,"mechanic_only","召唤蝇群，提供辅助、减速与削弱。","https://www.taptap.cn/app/550307/strategy/entity-collection/391967","official_archive","2025-04-18"),
("DELIRIN","德蕾琳","余焰之麟",None,"守护者","mechanic_only","承伤时治疗友军，并具有濒死复生机制。","https://www.taptap.cn/app/550307/strategy/entity-collection/369057","official_archive",None),
("ZENA","泽娜","魔龙之吻","炼狱爆破",None,"mechanic_only","灼烧体系英雄。","https://www.taptap.cn/app/550307/strategy/entity/24745505","official_archive",None),
("ASTLEY","阿斯特莱",None,None,None,"mechanic_only","冲锋/击杀并为友军提供护盾，带控制机制。","https://www.taptap.cn/app/550307/strategy/entity-collection/394631","official_archive",None),
("FENRIS","芬里斯",None,None,None,"mechanic_only","官方资料提及击退与护盾。","https://www.taptap.cn/app/550307/strategy/entity-collection/391932","official_archive",None),
("JIXIN","极心",None,None,None,"identity_only","官方英雄档案与技能展示已公开。","https://www.taptap.cn/app/550307/strategy/entity-collection/369050","official_archive",None),
("DURGA","杜尔伽",None,None,None,"mechanic_only","以友军生命为代价提供增益。","https://www.taptap.cn/app/550307/strategy/entity-collection/392044","official_archive",None),
("YULI","优璃",None,None,None,"identity_only","官方英雄档案与技能展示已公开。","https://www.taptap.cn/app/550307/strategy/entity-collection/394671","official_archive",None),
("LYNX","琳克斯",None,None,None,"mechanic_only","冰霜/冻结相关机制。","https://www.taptap.cn/app/550307/strategy/entity-collection/391929","official_archive",None),
("FERN","弗恩",None,None,None,"mechanic_only","官方英雄档案与技能展示已公开，主题为毒系战士。","https://www.taptap.cn/app/550307/strategy/entity-collection/369111","official_archive",None),
("NUMERA","努梅拉",None,None,None,"mechanic_only","束缚目标并施加毒素。","https://www.taptap.cn/app/550307/strategy/entity-collection/360742","official_archive",None),
("NERISSA","奈理莎",None,None,None,"identity_only","官方英雄档案与技能展示已公开。","https://www.taptap.cn/app/550307/strategy/entity-collection/369127","official_archive",None),
("VALERIA","瓦蕾亚",None,None,None,"mechanic_only","骑乘移动并使用“耀蚀”相关输出机制。","https://www.taptap.cn/app/550307/strategy/entity-collection/377790","official_archive",None),
("MORIDEN","莫里登",None,None,None,"identity_only","官方英雄档案与技能展示已公开。","https://www.taptap.cn/app/550307/strategy/entity-collection/360690","official_archive",None),
("ALRIS","艾尔莉丝",None,None,"医师","mechanic_only","救援/治疗并加速团队怒气恢复。","https://www.taptap.cn/app/550307/strategy/entity-collection/394592","official_archive",None),
("MALVIRA","玛尔蔚拉",None,None,"守护者","mechanic_only","自我保护与狩猎相关守护机制。","https://www.taptap.cn/app/550307/strategy/entity-collection/394615","official_archive",None),
("YANG_JIAN","杨戬",None,None,None,"mechanic_only","哮天犬削甲破防，具有强化与防御机制。","https://www.taptap.cn/app/550307/strategy/entity-collection/386125","official_archive",None),
("KANE","凯恩",None,"至高仲裁者/北境王座","战士","mechanic_only","官方技能展示强调大量部署费用回复。","https://www.taptap.cn/app/550307/strategy/entity-collection/391935","official_archive",None),
("RAVENHOLD","瑞文霍德",None,"北境王座","战术大师","identity_only","官方英雄档案与技能展示已公开。","https://www.taptap.cn/app/550307/strategy/entity-collection/391928","official_archive",None),
("MELISANDRE","梅丽珊卓","红袍女巫",None,None,"identity_only","官方档案与技能展示已公开；公开视频文本未给具体倍率。","https://www.taptap.cn/moment/788050175821086920","official_archive","2026-03-31"),
("ARYA_STARK","艾莉亚·史塔克",None,None,None,"identity_only","官方英雄档案与技能展示已公开。","https://www.taptap.cn/app/550307/strategy/entity-collection/391899","official_archive",None),
("JON_SNOW","琼恩·雪诺",None,None,None,"identity_only","官方英雄档案与技能展示已公开。","https://www.taptap.cn/moment/786985811064129660","official_archive",None),
("GUAN_YU","关羽","武圣","守望者小队/北境王座","战术大师","identity_only","官方英雄角色档案与技能展示已公开。","https://www.taptap.cn/moment/771132015939422685","official_archive","2026-02-12"),
("ALDAYA","阿尔代垭",None,None,None,"identity_only","官方英雄档案与技能展示已公开。","https://www.taptap.cn/moment/663818613903327564","official_archive",None),
("TARULA","塔露拉",None,None,None,"mechanic_only","魔法蝴蝶可治疗并保护友军。","https://www.taptap.cn/app/550307/strategy/entity-collection/392008","official_archive",None),
("PELAGIOS","佩拉吉奥斯","激浪之怒",None,None,"mechanic_only","召唤潮汐造成真实伤害。","https://www.taptap.cn/moment/651096847753940121","official_archive","2025-03-18"),
("CYRUS","居鲁士","征服者","秘法会","领主","identity_only","操控邪灵大军的守护者；官方档案和技能展示已公开。","https://www.taptap.cn/moment/637332318301719624","official_archive","2025-02-08"),
("LUST","拉丝特","毁灭魅魇",None,None,"mechanic_only","长鞭既能伤害敌人也能激励友军。","https://www.taptap.cn/moment/651081074734009416","official_archive","2025-03-18"),
("TWYLA","特薇拉","血色微笑",None,None,"mechanic_only","终结技可逐次提高“亢奋狂锯”触发概率。","https://www.taptap.cn/moment/637330056246460832","official_archive","2025-02-08"),
("ERDOAN","厄铎安","寒陨霜冠","北境王座","战士","identity_only","官方英雄档案与技能展示已公开。","https://www.taptap.cn/moment/764066303861326062","official_archive",None),
("BEATRICE","碧翠丝",None,None,None,"mechanic_only","召唤黑暗构造体的法术机制。","https://www.taptap.cn/app/550307/strategy/entity-collection/392018","official_archive",None),
("HISSERA","希瑟拉",None,None,None,"mechanic_only","召唤单位、成长攻击与无视防御相关机制。","https://www.taptap.cn/app/550307/strategy/entity-collection/358406","official_archive",None),
("LU_BU","吕布","飞将",None,None,"mechanic_only","万人敌状态拥有独立生命；进阶V低生命时缩短裂天戟触发间隔。","https://www.taptap.cn/app/550307/strategy/entity/24745505","official_archive","2025-02-08"),
("EIVOR","艾沃尔",None,None,None,"identity_only","刺客信条联动限定英雄。","https://www.taptap.cn/app/550307","official_app_page","2026-08-20"),
("KASSANDRA","卡珊德拉",None,None,None,"identity_only","刺客信条联动限定英雄。","https://www.taptap.cn/app/550307","official_app_page","2026-08-20"),
("BAYEK","巴耶克",None,None,None,"identity_only","刺客信条联动限定英雄。","https://www.taptap.cn/app/550307","official_app_page","2026-08-20"),
("EZIO","艾吉奥·奥迪托雷",None,None,None,"identity_only","刺客信条联动限定英雄。","https://www.taptap.cn/app/550307","official_app_page","2026-08-20"),
("AQILA","亚琪菈","幻影机械师",None,None,"identity_only","2026年7月官方版本公布的限定英雄。","https://www.taptap.cn/app/550307/topic?group_label_id=3105070&type=group_label","official_app_page","2026-07-08"),
]

# hero_key, skill_key, skill_name, skill_type, description, coefficient, target_cap,
# duration, direct_damage, optimizer_usable, source_url, source_date, value_json
SKILLS = [
("MORRIGAN","basic_magic","魔法攻击","basic","同时攻击3个目标。",0.70,"3",None,1,1,"https://www.taptap.cn/app/550307/strategy/entity-collection/359824","2024-09-04",None),
("MORRIGAN","ultimate_curse_seed","诅咒之种","ultimate","释放诅咒炸弹，最多命中3个目标；公开文本未给伤害倍率。",None,"3",None,1,0,"https://www.taptap.cn/app/550307/strategy/entity-collection/359824","2024-09-04",None),
("MORRIGAN","passive_curse_ritual","诅咒祭祀","passive","炸弹爆炸后生成持续范围伤害区域。",0.10,None,8,1,0,"https://www.taptap.cn/app/550307/strategy/entity-collection/359824","2024-09-04",{"tick_interval_seconds":1,"air_damage_multiplier":0.5}),
("MORRIGAN","passive_curse_contract","诅咒契约","passive","上阵50秒后永久提高伤害。",None,None,None,0,0,"https://www.taptap.cn/app/550307/strategy/entity-collection/359824","2024-09-04",{"trigger_after_seconds":50,"damage_bonus":0.20}),
("MORRIGAN","lord_forbidden_knowledge_3","禁忌学识Ⅲ","lord","提高阵营小队成员基础属性。",None,None,None,0,0,"https://www.taptap.cn/app/550307/strategy/entity-collection/359824","2024-09-04",{"base_stat_bonus":0.15}),
("HEX","basic_magic","魔法攻击","basic","攻击1个目标，优先攻击空中单位。",1.0,"1",None,1,1,"https://www.taptap.cn/app/550307/strategy/entity-collection/394670","2024-08-31",None),
("HEX","ultimate_mad_truth","狂乱真理","ultimate","开启后增伤、提高攻速并缩短攻击间隔，持续30秒。",None,None,30,0,0,"https://www.taptap.cn/app/550307/strategy/entity-collection/394670","2024-08-31",{"damage_bonus":1.0,"attack_speed_flat":100,"attack_interval_multiplier":0.5}),
("HEX","passive_judgement","命运：审判","passive","攻击概率眩晕；对受控目标额外增伤。",None,None,None,0,0,"https://www.taptap.cn/app/550307/strategy/entity-collection/394670","2024-08-31",{"proc_probability":0.15,"stun_seconds":2,"controlled_target_damage_bonus":0.45}),
("VIERNA","basic_magic","魔法攻击","basic","攻击1名敌人；不受攻速加成影响。",1.0,"1",None,1,1,"https://www.taptap.cn/app/550307/strategy/entity-collection/369089","2024-10-13",{"affected_by_attack_speed":false}),
("VIERNA","ultimate_death_grip","死亡之握","ultimate","造成700%范围伤害，随后斩杀低生命普通目标。",7.0,"all",None,1,0,"https://www.taptap.cn/app/550307/strategy/entity-collection/369089","2024-10-13",{"execute_hp_threshold":0.25,"execute_excludes":["elite","boss"]}),
("VIERNA","passive_soul_secret","噬魂秘法","passive","击杀收集魂魄，每层提高攻击。",None,None,None,0,0,"https://www.taptap.cn/app/550307/strategy/entity-collection/369089","2024-10-13",{"max_souls":6,"attack_bonus_per_soul":0.02}),
("ZILITU","basic_magic","魔法攻击","basic","攻击1名敌人；攻击自身阻挡目标时伤害提高。",1.0,"1",None,1,1,"https://www.taptap.cn/app/550307/strategy/entity-collection/360716","2024-08-27",{"blocked_target_damage_bonus":0.20}),
("ZILITU","ultimate_dominion","霸权意志","ultimate","开启后扩大范围并增加300%伤害，持续25秒。",None,None,25,0,0,"https://www.taptap.cn/app/550307/strategy/entity-collection/360716","2024-08-27",{"damage_bonus":3.0,"kill_extension_seconds":1,"applies_burn":true}),
("ZILITU","passive_soul_siphon","灵魂虹吸","passive","攻击生命80%以上目标时追加40%攻击的真实伤害。",0.40,"1",None,1,0,"https://www.taptap.cn/app/550307/strategy/entity-collection/360716","2024-08-27",{"target_hp_above":0.80,"damage_type":"true"}),
("SILAS","basic_physical","物理攻击","basic","攻击1个目标，优先攻击空中单位。",1.0,"1",None,1,1,"https://www.taptap.cn/app/550307/strategy/entity-collection/369108","2024-10-07",None),
("SILAS","ultimate_shadow_form","暗影形态","ultimate","开启后伤害增加60%，攻击无视目标防御，持续15秒。",None,None,15,0,0,"https://www.taptap.cn/app/550307/strategy/entity-collection/369108","2024-10-07",{"damage_bonus":0.60,"ignore_defense":1.0}),
("SILAS","passive_soul_pact","魂契","passive","范围内单位死亡后增伤30%持续10秒，每20秒最多触发一次。",None,None,10,0,0,"https://www.taptap.cn/app/550307/strategy/entity-collection/369108","2024-10-07",{"damage_bonus":0.30,"internal_cd":20}),
("SILAS","passive_cursed_eye","诅咒之眼","passive","持续攻击同一目标5秒后施加8秒印记，每20秒最多标记一次。",None,None,8,0,0,"https://www.taptap.cn/app/550307/strategy/entity-collection/369108","2024-10-07",{"same_target_seconds":5,"internal_cd":20}),
("ELOWYN","basic_group_heal","群体治疗","basic","治疗范围内3名友军，治疗量基于攻击；公开文本未给基础倍率。",None,"3",None,0,0,"https://www.taptap.cn/app/550307/strategy/entity-collection/360737","2024-10-05",None),
("ELOWYN","ultimate_gift_of_nature","自然之礼","ultimate","驱散范围友军减益，并每秒恢复治疗倍率40%的生命，持续10秒。",None,"all",10,0,0,"https://www.taptap.cn/app/550307/strategy/entity-collection/360737","2024-10-05",{"heal_ratio_per_second":0.40}),
("ELOWYN","auto_tree_spirit","森之精灵","auto","树精灵每0.5秒治疗周围友军，持续10秒。",None,None,10,0,0,"https://www.taptap.cn/app/550307/strategy/entity-collection/360737","2024-10-05",{"tick_seconds":0.5,"heal_ratio_per_tick":1.0,"max_summons":1}),
("ELOWYN","auto_natures_favor","自然眷顾","auto","每5秒为全场友军恢复怒气；索引文本尾部截断。",None,"all",None,0,0,"https://www.taptap.cn/app/550307/strategy/entity-collection/360737","2024-10-05",{"interval_seconds":5,"visible_value":0.01,"value_basis":"official_text_truncated"}),
("DALIA","basic_aoe_magic","范围魔法攻击","basic","攻击最多10名敌人。",1.0,"10",None,1,1,"https://www.taptap.cn/app/550307/strategy/entity-collection/360700","2024-11-29",None),
("DALIA","ultimate_shadow_burst","暗影爆发","ultimate","10秒内自身伤害增加20%，普攻优先引爆噩梦玫瑰。",None,None,10,0,0,"https://www.taptap.cn/app/550307/strategy/entity-collection/360700","2024-11-29",{"damage_bonus":0.20}),
("TWYLA","advancement_v_saw","进阶V：亢奋狂锯强化","advancement","每次释放终结技提高触发概率，最多提高15%。",None,None,None,0,0,"https://www.taptap.cn/moment/637330056246460832","2025-02-08",{"proc_bonus_per_ultimate":0.05,"max_proc_bonus":0.15}),
("LU_BU","advancement_v_rift_halberd","进阶V：裂天戟强化","advancement","生命低于65%时裂天戟触发间隔减少2秒。",None,None,None,0,0,"https://www.taptap.cn/app/550307/strategy/entity/24745505","2025-02-08",{"hp_below":0.65,"trigger_interval_reduction_seconds":2}),
]


def ensure_official_hero_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA)


def seed_official_hero_catalog(connection: sqlite3.Connection) -> dict[str, int]:
    """Idempotently write official facts; unknown numeric fields remain NULL."""
    ensure_official_hero_schema(connection)
    connection.executemany(
        """INSERT INTO official_hero_catalog(
           hero_key,hero_name,title,faction,role,completeness,mechanic_summary,
           source_url,source_kind,source_date
        ) VALUES (?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(hero_key) DO UPDATE SET
          hero_name=excluded.hero_name,title=excluded.title,faction=excluded.faction,
          role=excluded.role,completeness=excluded.completeness,
          mechanic_summary=excluded.mechanic_summary,source_url=excluded.source_url,
          source_kind=excluded.source_kind,source_date=excluded.source_date,
          data_version='CN-2026-08-25',updated_at=CURRENT_TIMESTAMP""",
        HEROES,
    )
    connection.executemany(
        """INSERT INTO official_skill_catalog(
           hero_key,skill_key,skill_name,skill_type,description,coefficient,target_cap,
           duration,direct_damage,optimizer_usable,source_url,source_date,value_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(hero_key,skill_key) DO UPDATE SET
          skill_name=excluded.skill_name,skill_type=excluded.skill_type,
          description=excluded.description,coefficient=excluded.coefficient,
          target_cap=excluded.target_cap,duration=excluded.duration,
          direct_damage=excluded.direct_damage,optimizer_usable=excluded.optimizer_usable,
          source_url=excluded.source_url,source_date=excluded.source_date,
          value_json=excluded.value_json""",
        [row[:-1] + (json.dumps(row[-1], ensure_ascii=False, sort_keys=True) if row[-1] is not None else None,) for row in SKILLS],
    )
    connection.commit()
    return official_catalog_counts(connection)


def official_catalog_counts(connection: sqlite3.Connection) -> dict[str, int]:
    counts = {
        "heroes": connection.execute("SELECT COUNT(*) FROM official_hero_catalog").fetchone()[0],
        "skills": connection.execute("SELECT COUNT(*) FROM official_skill_catalog").fetchone()[0],
        "optimizer_usable_skills": connection.execute(
            "SELECT COUNT(*) FROM official_skill_catalog WHERE optimizer_usable=1"
        ).fetchone()[0],
    }
    for completeness, count in connection.execute(
        "SELECT completeness,COUNT(*) FROM official_hero_catalog GROUP BY completeness"
    ):
        counts[str(completeness)] = int(count)
    return counts


def load_optimizer_usable_official_basics(connection: sqlite3.Connection):
    return connection.execute(
        """SELECT h.hero_key,h.hero_name,s.skill_key,s.skill_name,s.coefficient,
                  s.target_cap,s.value_json,s.source_url
           FROM official_hero_catalog h
           JOIN official_skill_catalog s USING(hero_key)
           WHERE s.optimizer_usable=1 AND s.skill_type='basic'
             AND s.coefficient IS NOT NULL AND s.target_cap IS NOT NULL
           ORDER BY h.hero_key"""
    ).fetchall()

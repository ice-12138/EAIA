# 装备数据库与 V1.1 优化器设计

> 本文档原先记录 V0.1 设计；V1.1 修订以 2026-08-25 更新后的方案文档为准。

## 目标

在现有 Python 项目中维护一个独立的装备数据层和 V1.1 配装算法。它从本地 SQLite 读取英雄、完整技能、装备、词条、套装、木桩模式和规则数据，计算穿装后的静态面板，并通过 60 秒事件模拟搜索合法装备组合的 Top-K 结果。本阶段不调用、不修改 OCR 识别流程。

## 范围

本阶段实现文档定义的 V1.1：

- SQLite 数据库初始化、表结构和种子规则。
- 9 类输入实体：Heroes、Skills、HeroDamageProfiles、Equipment、EquipmentStats、Sets、SetEffects、Scenarios、GameRules。
- 百分比字段统一以小数存储；枚举字段在数据库层使用 CHECK 约束，在 Python 层提供清晰校验错误。
- CSV/字典批量导入接口，方便后续把 Excel 转成 CSV 后导入。
- 静态面板：攻击、暴击率上限与溢出、暴伤、攻速、回怒属性。
- 完整技能事件模型：普攻、技能、终结技和 followup 的倍率、段数、CD、施法占用、怒气和触发条件。
- 固定 60 秒事件模拟：支持 single/aoe 与用户指定 enemy_count；DoT 直接伤害标记为不计入模型。
- 左侧 Weapon + Armor、右侧 Bracelet + Necklace + Ring 的合法组合枚举；支持库存可用性和套装件数激活。
- Top-K 结果包含装备 ID、激活套装、关键面板、总伤害、DPS、暴击溢出和伤害来源占比。
- 校验、算法单元测试和一个最小可运行示例。

明确不在本阶段实现：OCR 结果解析与入库、外部 Buff/Debuff、DoT 时间轴、击杀/转火、波次、移动、生存控制、Monte Carlo、并行搜索和 UI。

## 架构

代码按职责拆分：

- `equipment_db.py`：SQLite schema、连接、初始化、事务和基础数据访问。
- `equipment_models.py`：不可变领域对象和枚举，隔离数据库行与算法输入。
- `equipment_rules.py`：可配置的攻击合成、暴击、攻速、伤害减免与来源增伤规则。
- `equipment_optimizer.py`：装备聚合、面板计算、事件模拟调用、合法组合枚举和 Top-K 搜索。
- `equipment_simulator.py`：60 秒直接伤害事件队列、怒气循环、技能调度和模拟结果汇总。
- `equipment_data.py`：批量导入和数据校验，不负责算法。
- `tests/`：数据库约束、规则计算、组合约束和 Top-K 行为测试。

算法数据流为：

```text
SQLite -> repository/loaders -> domain models -> panel calculator
       -> legal build enumeration -> simplified damage scorer -> Top-K report
```

算法使用 `GameRules` 配置读取规则，而不是把暴击上限、攻速映射和防御公式散落在搜索代码中。默认规则仅是可运行的占位规则，必须在实际校准后替换。

## 数据库设计

使用 SQLite 文件 `data/equipment.db`，Python 标准库 `sqlite3` 即可运行，不需要安装 PostgreSQL、MySQL 或 SQLite 服务。数据库使用外键约束、唯一键和 CHECK 约束；初始化脚本可重复执行。

核心表及关键字段：

- `heroes(hero_id, hero_name, atk_base, crit_rate_base, crit_dmg_base, atk_speed_base, atk_interval_base, rage_start, rage_max, damage_type, main_output)`。
- `skills(hero_id, skill_id, skill_name, source_type, scaling_stat, coefficient, hit_count, target_cap, can_crit, cooldown, action_time, rage_cost, rage_gain, conditions)`。
- `hero_damage_profiles(hero_id, scenario_id, basic_share, skill_share, ultimate_share, expected_targets_basic, expected_targets_skill, expected_targets_ult, ult_uptime_base)`。
- `equipment(item_id, slot, set_id, tier, level, locked, available)`。
- `equipment_stats(item_id, stat_source, stat_type, stat_value)`，主键为 `(item_id, stat_source, stat_type)`。
- `sets(set_id, set_name, required_pieces, slot_group, output_set)`。
- `set_effects(set_id, effect_id, effect_type, value, applies_to, trigger, duration, max_stacks, stack_rule, proc_chance, internal_cd, condition, approximate)`。
- `scenarios(scenario_id, scenario_name, duration, target_mode, target_count, target_def, target_mres, spawn_pattern, kill_rate_hint, target_hp, weight_primary, weight_secondary)`。
- `game_rules(rule_key, rule_value, value_type, description)`，规则值以 JSON 文本保存，加载后按 `value_type` 转换。

`equipment.set_id`、技能和简化画像的关联使用外键。初始数据库只插入通用规则默认值和标准场景示例，不插入虚构的英雄或装备事实数据。

## 算法设计

1. 加载指定英雄、完整 Skills、规则和所有 `available=1` 且未锁定装备。
2. 聚合每件装备词条和套装常驻效果，计算穿装面板。
3. 根据 `single/aoe` 与 `enemy_count` 建立固定 60 秒战斗配置。
4. 用事件队列调度普攻、技能、终结技和自身 followup；`direct_damage=FALSE` 的 DoT 不计入总伤害。
5. 生成合法五件套，拒绝重复 item、槽位缺失和不可用装备；用固定大小最小堆维护 Top-K。
6. 按 `TotalDamage60s` 排序，输出各来源伤害、终结技次数、首次开大时间、状态覆盖率和 `model_coverage`。

## 错误处理

- 数据库约束错误转换为 `DataValidationError`，并包含实体和字段信息。
- 缺少英雄或完整 Skills 时立即失败，不使用静默默认值；旧版简化画像只用于兼容接口。
- 槽位数量不足时返回明确的组合搜索错误。
- 未知规则或效果类型拒绝导入；DoT 依赖效果默认不参与 V1.1 模拟，不能悄悄按泛用增伤处理。

## 测试验收

- 初始化后所有 9 张业务表存在，重复初始化不会破坏数据。
- 非法槽位、非法伤害类型、百分比越界、重复词条和悬空外键会被拒绝。
- 暴击率超过上限时，最终暴击率封顶且溢出单独报告。
- 套装件数达到要求时激活静态套装效果，未达到时不激活。
- 搜索结果每项恰有五件装备、五个不同槽位和不同 item_id，且只使用可用装备。
- 构造一个可计算的最小数据集，验证 Top-1 的 DPS 高于其他组合，并验证 Top-K 数量和排序。
- 运行项目既有测试与新增测试。

## 后续扩展边界

后续版本可在同一数据库和领域对象上增加概率精排、Branch-and-Bound、Monte Carlo 和更完整的自身状态叠层。当前 V1.1 不把外部 Buff、DoT、击杀和副本机制写入算法。

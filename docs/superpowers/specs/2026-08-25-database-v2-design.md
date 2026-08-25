# 潮汐守望者装备优化系统数据库 V2.0 改造设计

## 目标

将当前 9 张 SQLite 业务表升级为设计文档 V2.0 定义的 23 张实体表，支持：

- 英雄基础属性、技能级机制和技能伤害组件；
- 套装、装备品质、套装等级、词条质量和词条标准字典；
- 主/副词条槽位合法性、数值范围、概率分布和未解锁词条估计；
- OCR 原始结果暂存、标准化匹配和人工纠错记录；
- 当前 60 秒单体/群体优化器继续读取统一的领域数据。

当前仓库没有 `data/equipment.db` 实例，因此本次以初始化 schema 为主，同时为旧结构保留迁移入口，避免未来已有数据库无法升级。

## 表结构

数据库包含以下 23 张表：

### 标准字典与规则层

1. `equipment_categories(category_id, category_name, description, sort_order)`
2. `equipment_slots(slot_id, slot_name, slot_group, set_piece_group, sort_order, notes)`
3. `set_tiers(set_tier_id, set_tier_name, tier_rank, notes)`
4. `gear_qualities(quality_id, quality_name, quality_rank, has_special_roll_rule, notes)`
5. `stat_roll_grades(roll_grade_id, roll_grade_name, grade_rank, is_max_grade, notes)`
6. `stat_definitions(stat_type, stat_name, stat_family, unit_type, stack_mode, can_main_stat, can_sub_stat, ocr_priority, description, active)`
7. `stat_category_map(stat_type, category_id, relevance_weight, notes)`，主键为 `(stat_type, category_id)`。
8. `stat_slot_rules(slot_id, stat_source, stat_type, allowed, version, notes)`，主键为 `(slot_id, stat_source, stat_type)`。
9. `stat_value_ranges(range_id, stat_type, stat_source, quality_id, roll_grade_id, slot_id, set_tier_id, min_value, max_value, mean_value, median_value, sample_count, distribution_type, data_source, game_version, confidence, notes)`。
10. `stat_roll_probabilities(probability_id, quality_id, stat_type, set_tier_id, roll_grade_id, probability, sample_count, data_source, game_version, confidence, notes)`。

### 英雄与战斗机制层

11. `heroes(hero_id, hero_name, hero_class, hp_base, atk_base, def_base, crit_rate_base, crit_dmg_base, atk_speed_base, atk_interval_base, rage_start, rage_max, rage_regen_base, healing_effect_base, damage_type, main_output, notes)`。
12. `skills(hero_id, skill_id, skill_name, skill_type, cooldown, initial_cooldown, action_time, rage_cost, rage_gain, blocks_basic_attack, affected_by_atk_speed, priority, conditions, mechanic_class, enabled_in_optimizer, notes)`，主键为 `(hero_id, skill_id)`。
13. `skill_components(component_id, hero_id, skill_id, component_index, component_name, source_type, scaling_stat, coefficient, hit_count, hit_interval, target_cap, secondary_target_ratio, can_crit, trigger_event, internal_cd, direct_damage, mechanic_class, enabled_in_optimizer, conditions, notes)`。
14. `hero_damage_profiles(hero_id, scenario_id, basic_share, skill_share, ultimate_share, expected_targets_basic, expected_targets_skill, expected_targets_ult, ult_uptime_base, data_source, notes)`，主键为 `(hero_id, scenario_id)`。

### 套装与装备事实层

15. `sets(set_id, set_name, set_tier_id, required_pieces, slot_group, category_id, active, game_version, notes)`。
16. `set_effects(effect_id, set_id, effect_category_id, effect_type, stat_type, value, applies_to, trigger, duration, max_stacks, stack_rule, proc_chance, internal_cd, condition, mechanic_class, requires_dot, enabled_in_optimizer, approximate, game_version, notes)`。
17. `equipment(item_id, slot_id, set_id, quality_id, level, enhancement_level, item_locked, available, equipped_hero_id, source, created_at, updated_at, notes)`。
18. `equipment_stats(item_id, stat_index, stat_source, stat_type, stat_value, unlock_level, is_unlocked, roll_grade_id, estimate_override, value_confidence, notes)`，主键为 `(item_id, stat_index)`。

### 场景与规则层

19. `scenarios(scenario_id, scenario_name, duration, target_mode, target_count_default, target_count_user_input, target_def, target_mres, targets_stationary, targets_immortal, notes)`。
20. `game_rules(rule_key, rule_value, value_type, description, game_version, source, confidence, updated_at)`。

### OCR 与纠错层

21. `ocr_aliases(alias_id, entity_type, entity_key, canonical_text, alias_text, normalized_alias, locale, priority, source, active, notes)`。
22. `ocr_import_queue(import_id, source_ref, raw_ocr_text, parsed_json, overall_confidence, validation_status, linked_item_id, created_at, reviewed_at, notes)`。
23. `ocr_correction_log(correction_id, import_id, entity_type, raw_text, predicted_key, corrected_key, confidence_before, user_confirmed, promote_to_alias, created_at)`。

所有百分比使用小数，时间使用秒；未知值使用 `NULL`。SQLite 使用 `INTEGER` 表示布尔值，并通过 `CHECK` 约束限制枚举、概率和百分比范围。

## 旧结构调整

- `sets.output_set` 删除，改为 `category_id`，并关联固定四类 `output/defense/healing/buff`。
- `equipment.slot` 改为 `slot_id`，`tier` 拆分为 `set_tier_id` 与 `quality_id`；`locked` 改为 `item_locked`。
- `equipment_stats` 主键改为 `(item_id, stat_index)`，支持同一装备的主词条、副词条、未解锁值、质量档和人工估计值。
- `skills` 只保存技能级属性；原有伤害字段迁移到 `skill_components`。
- `set_effects` 增加效果类别、标准词条关联、机制类型、版本和当前优化器启用状态。
- `scenarios` 删除波次/击杀/权重字段，改为固定 60 秒单体或用户指定目标数的群体木桩字段。
- `game_rules` 增加版本、来源、置信度和更新时间。
- 原 9 张表的 Python 加载器和 dataclass 将同步更新；优化器内部仍可通过兼容适配读取旧版 fixture。

## 数据流与视图

OCR 结果先写入 `ocr_import_queue`，经过别名、槽位规则和数值范围校验后再写入 `equipment` 与 `equipment_stats`；人工修正写入 `ocr_correction_log`，可选择晋升到 `ocr_aliases`。

新增三个只读视图：

- `v_set_catalog`：套装、类别、等级和套装效果；
- `v_equipment_full`：装备、套装、品质和词条完整信息；
- `v_equipment_stat_effective`：输出实际值、估计值、上下界、来源和置信度，优化器优先读取该视图。

未解锁词条的有效值按人工覆盖、概率加权均值、区间均值的优先级计算；原始 `stat_value` 保持 `NULL`，不把估计值写回事实字段。

## 迁移与兼容

初始化必须幂等。对旧数据库执行初始化时：

1. 保留旧表数据到临时迁移表；
2. 创建 V2.0 字典表和新事实表；
3. 将旧 `slot`、`tier`、`locked`、`output_set` 等值映射到标准字典；
4. 将旧技能伤害列迁移为带有 `component_index` 的 `skill_components`；
5. 对无法可靠推断的品质、词条等级、OCR 来源等字段填入 `NULL` 或明确的默认记录，不伪造业务事实；
6. 保留旧读取 API 的兼容适配，直到优化器完成 V2.0 读取切换。

如果旧表没有数据，直接创建 V2.0 表、视图和固定字典记录。固定字典至少包含四个装备类别和两个 60 秒木桩场景。

## 错误处理

- 所有外键关系启用 `PRAGMA foreign_keys=ON`。
- 初始化和迁移使用事务；任一表创建或数据迁移失败时整体回滚。
- 字典 ID、枚举、概率、置信度和数值范围使用数据库约束及 Python 校验双重保护。
- OCR 低置信度结果只能停留在队列表，不得自动写入正式装备事实表。
- `enabled_in_optimizer=FALSE` 的技能和套装效果可以保存，但当前模拟器不得读取。

## 验收测试

- 初始化后恰有 23 张业务表和 3 个视图，重复初始化不报错且不重复插入固定字典。
- 所有主要外键、组合主键、唯一约束和枚举约束可验证。
- 旧 9 表 fixture 可迁移，旧优化器测试仍能运行或通过明确的兼容适配运行。
- `equipment_stats` 能保存未解锁副词条，`v_equipment_stat_effective` 能产生实际值/估计值/上下界。
- OCR 队列、纠错日志和别名的外键链路有效。
- 新旧 schema 均通过完整 `unittest` 测试，初始化临时数据库后可查询全部 23 张表。

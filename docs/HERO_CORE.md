# HeroCore 战斗仿真规范 v1.0

EAIA 的英雄伤害计算采用“固定事件引擎 + 可注入 HeroCore”的结构。新增英雄时不修改 `hero_core_engine.py`，只增加一个符合 Schema 的 HeroCore JSON。

## 1. 总体流程

```text
HeroCore + 装备 + 木桩参数
        ↓
装备静态属性与套装激活
        ↓
初始化 State / Resource / Buff / Summon
        ↓
最小堆事件队列
        ↓
Event → Condition → Action
        ↓
伤害核与状态变更
        ↓
严格前60秒伤害 + 稳态等效60秒伤害 ED60
```

引擎只认识通用概念，不允许出现 `if hero == SUN_WUKONG` 之类的英雄专属分支。

## 2. ED60 与长 CD

装备排序默认使用长期稳定输出折算后的等效 60 秒伤害：

```text
ED60 = measurement_damage / measurement_seconds * 60
```

例如一个技能单次伤害为 `D`、CD 为 120 秒，则长期贡献自然收敛到 `0.5D / 60s`。因此不会出现 60s CD 能打一次、61s CD 在严格 60 秒内为零造成的边界断层。

API 同时返回：

- `actual_60s`：从战斗开始严格截取前 60 秒。
- `equivalent_60s`：预热后长时间统计并归一化为 60 秒，作为装备优化主指标。

## 3. HeroCore 顶层字段

```json
{
  "schema_version": "1.0",
  "core_version": "1.0.0",
  "game_version": "CN-2026-08-28",
  "hero": {},
  "resources": {},
  "state": {},
  "buffs": {},
  "summons": {},
  "skills": {},
  "triggers": [],
  "policies": {},
  "default_policy": "",
  "assumptions": [],
  "validation_required": []
}
```

其中 `assumptions` 与 `validation_required` 用于人工监督。截图或公开资料无法唯一确定的机制不得静默猜测，应明确记录并在前端显示。

## 4. 统一行为模型

每个英雄机制统一拆成：

```text
WHEN Event
IF Condition
DO Action(s)
```

例如孙悟空“每第三次普攻召唤幻象”：

```json
{
  "event": "BASIC_ATTACK_HIT",
  "condition": "state.basic_count % 3 == 0 and state.clone_count < 3",
  "actions": [
    {"type": "summon", "entity": "great_sage_clone", "count": 1},
    {"type": "add_state", "state": "clone_count", "value": 1}
  ]
}
```

当前通用 Action 包括：

- `add_state` / `set_state` / `reset_state`
- `add_resource` / `spend_resource`
- `apply_buff`
- `set_event_coefficient`
- `deal_damage`
- `summon` / `remove_summon`
- `schedule_event`

以后遇到新机制时，优先扩展一个可复用的通用 Action，而不是增加英雄专属代码。

## 5. Condition DSL

条件表达式由受限 AST 解释器执行，不直接 `eval` 任意 Python。允许访问：

- `state.*`
- `resource.*`
- `target.*`
- `event.*`
- `summon.*`
- `buff.*`

允许基础算术、比较、布尔和取模；禁止函数调用、下标访问、导入和私有属性。

## 6. 随机与可复现性

- 暴击使用数学期望，避免装备排序被随机暴击扰动。
- 会改变状态机的随机触发（例如孙悟空“当头一棒”25%）使用 Monte Carlo。
- 同一装备比较使用固定 seed 序列，可降低不同构筑之间的随机比较方差。

## 7. 装备与 HeroCore 解耦

HeroCore 只描述英雄机制。装备仍由现有 SQLite 装备库读取，静态属性、套装激活后注入仿真器。当前 v1 支持常驻面板效果与常驻伤害标签效果；无法完整覆盖的触发型套装会返回 `coverage=partial` 和 `warnings`，不得伪装成完整精确结果。

## 8. 后端 API

- `GET /api/hero-cores`：HeroCore 列表。
- `GET /api/hero-cores/{id}`：完整 HeroCore。
- `POST /api/hero-core/simulate`：执行装备 + HeroCore 仿真。

示例请求：

```json
{
  "hero_core_id": "SUN_WUKONG",
  "item_ids": [],
  "policy": "immediate",
  "target_def": 0,
  "control_immune": true,
  "trials": 64,
  "warmup": 120,
  "measurement": 600,
  "seed": 20260828
}
```

## 9. 孙悟空案例

`data/hero_cores/sun_wukong.json` 已将当前截图与已讨论机制编码为：

- 普攻 115% ATK；
- 当头一棒 25% 触发，主击 200%，木桩免控时追加 200%，并增加破势；
- 身外化身 18 × 100%，持续约 4.5 秒；
- 5 层破势释放终结技后，11 秒 60% 无视防御；
- 每第三次普攻召唤幻象，最多 3 个；
- 3 幻象后每第三次普攻刷新 15 秒 +20% ATK；
- 幻象按 40% ATK 与属性继承模型攻击；
- 支持“怒气满立即释放”与“优先 5 层破势释放”两套策略。

核心中已经列出需要录像/木桩继续校准的字段，包括基础攻击间隔、怒气增长、终结技阻塞普攻、18 段真实时间戳、幻象继承范围和木桩 DEF。

## 10. 新英雄接入原则

新增英雄时只提交新的 `data/hero_cores/<hero>.json` 和对应测试。若现有 Action 无法表达其机制，先判断能否抽象成多个英雄都可复用的通用 Action，再修改引擎。新增 HeroCore 后必须至少验证：Schema、可安全解析、裸装仿真可运行、长 CD 归一化无边界异常、关键触发次数符合技能语义。

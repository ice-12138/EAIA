# HeroCore 英雄数据文件 V2

HeroCore 文件同时承担两类职责：

1. 英雄图鉴展示：英雄身份、满级裸装属性、技能说明、天赋/额外机制。
2. 装备推荐计算：`skills / triggers / buffs / summons / policies` 由统一 HeroCore 引擎执行。

前端“英雄图鉴 → 上传 HeroCore”直接接受 UTF-8 JSON 文件。上传后文件保存在 `data/hero_cores/<hero.id>.json`，并立即出现在英雄图鉴以及 HeroCore 装备推荐的英雄列表中。

## V2 相对旧格式的强制变化

旧文件只描述技能已经不足以用于装备伤害比较。V2 至少需要：

```json
{
  "schema_version": "1.0",
  "hero": {
    "id": "HERO_ID",
    "name": "英雄名称",
    "base_stats": {
      "atk": 0,
      "hp": null,
      "def": null,
      "mres": null,
      "crit_rate": 0.05,
      "crit_dmg": 1.5,
      "atk_speed": 100,
      "rage_regen": 0,
      "attack_interval": 2.0
    }
  },
  "skills": {}
}
```

`hp`、`def`、`mres` 等尚未确认的属性允许为 `null`；`atk`、`crit_rate`、`crit_dmg`、`attack_interval` 是当前引擎的必填计算属性。

## 白字属性与攻击速度语义

`hero.base_stats` 必须记录游戏属性页的真实白字值，不要为了适配计算公式预先做减法或归一化。例如游戏面板显示攻击速度 `100`、攻击间隔 `2.6秒`，应直接写：

```json
{
  "atk_speed": 100,
  "attack_interval": 2.6
}
```

HeroCore 引擎会把 `atk_speed` 视为英雄白字面板基准。装备和 Buff 增加攻速后，运行时先计算：

```text
bonus_attack_speed = current_attack_speed - base_attack_speed
```

再把这个“相对白字基准的攻速变化”交给 `GameRules.attack_interval()`。因此白字攻击速度 100 不会被误算成“额外 +100 攻速”。

当前攻速到隐藏攻击间隔的数学曲线仍是可配置的近似公式，后续可通过录像/实测校准；这次固定的是数据语义和接口契约。旧的两参数 `GameRules.attack_interval(base_interval, bonus_speed)` 调用仍保留原有“bonus_speed 已是额外攻速点”的兼容语义。

推荐同时提供 `codex.skills`，用于英雄图鉴展示完整技能说明；`skills` 则是可执行的引擎定义。复杂被动应继续使用 `triggers + condition + action` 描述，而不是在 Python 中增加英雄专属分支。

完整模板位于 `data/hero_core_template_v2.json`，前端也提供“下载数据模板”按钮。

## 装备推荐

“装备推荐”页面不再使用单独的战斗仿真页面。只有具备 HeroCore 的英雄可以进行精确装备推荐。后端先用普通属性势能裁剪每个部位的候选数，再对候选组合运行 HeroCore 事件仿真；最终 Top-K 仅按照稳定态等效 60 秒伤害（ED60）排序。候选裁剪分数不参与最终排名。

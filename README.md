# EAIA HOScrcpy Screen Capture

通过 HOScrcpy 使用的 HDC 连接，在设备侧生成当前屏幕截图并拉回电脑。程序不读取电脑桌面、不移动鼠标、不模拟键盘或触控，不会抢占焦点。

## 在 EAIA 环境中运行

单次截图：

```powershell
conda run -n EAIA python screen_capture.py
```

连续截图（每 2 秒，共 5 张）：

```powershell
conda run -n EAIA python screen_capture.py --interval 2 --count 5
```

如果电脑连接了多个 HDC 目标，可用 `--serial` 指定，例如 `--serial FMR0223A30001935`。截图默认保存在 `captures` 目录，也可以用 `--output-dir` 指定目录。

运行测试：

```powershell
conda run -n EAIA python -m unittest discover -s tests -v
```

## 装备详情 OCR 流程

`equipment_workflow.py` 已实现单件和全量流程：设备侧点击、详情区域变化确认、详情稳定确认、裁剪增强、OCR 和 `ocr_results.jsonl` 原始结果保存。默认详情区域是从 `screen_20260825_122242_383 拷贝.jpg` 标定的 `(2007,238)-(2567,1133)`。

OCR 引擎通过 `OcrEngine` 接口注入；当前提供 `PaddleOcrV5Mobile` 适配器，明确使用 `PP-OCRv5_mobile_det` 和 `PP-OCRv5_mobile_rec`，保留详情区域中的全部原始文本行，不做分类。PaddleOCR 3.7 还需要 OCR 运行依赖：

```powershell
conda activate EAIA
python -m pip install "paddlex[ocr]"
```

全量识别入口示例：

```python
from pathlib import Path
from equipment_workflow import PaddleOcrV5Mobile, build_hdc_scanner

scanner = build_hdc_scanner(
    hdc=r"D:\DEVECO~2\sdk\default\OPENHA~1\TOOLCH~1\hdc.exe",
    serial="FMR0223A30001935",
    screen_dir=Path("captures"),
    ocr=PaddleOcrV5Mobile(cache_dir=Path(".paddle_home")),
    output_dir=Path("ocr_results"),
)
records = scanner.scan_until_bottom()
print(f"recognized: {len(records)}")
```

`scan_until_bottom()` 会通过 HDC 在手机侧点击和滑动，不会使用 Windows 鼠标；执行前请确认投屏窗口和手机页面处于装备背包页面。模型缓存默认放在项目的 `.paddle_home`，识别结果和裁剪图放在 `ocr_results`。

默认流程不会对点击前的旧详情面板再次执行 OCR，而是使用详情区域图像差异确认切换成功，再对新面板执行 OCR。这样每件装备少一次完整模型推理。当前 EAIA 环境的 Paddle 3.3.1 与 PP-OCRv5 Mobile 组合不兼容 oneDNN，因此保持关闭以避免底层运行时错误。需要旧面板 OCR 做严格对比时，可在 `build_hdc_scanner(..., verify_baseline_ocr=True)` 中显式开启。

## 细化装备信息

`equipment_regions.py` 会读取 `captures/exclusive` 和 `captures/general` 中的红框标注，自动映射回原始屏幕坐标。流程先识别 `专属标识`：OCR 含有“专属”时使用专属区域，否则使用通用区域。细化结果包含品质、部位、主词条、套装名称和最多 4 条副词条；缺少的副词条保留为空，包含“解锁”的副词条记录 `value=-1`，并将 `fully_unlocked` 设为 `false`。

批量入口会在每件装备的粗 OCR 完成后追加 `fine_detail` 字段，细化裁剪图保存在 `fine_ocr_results`，原始完整记录保存在 `ocr_results_run5/ocr_results.jsonl`。

## 装备数据库与 V1.1 配装算法

配装算法是独立模块，当前不接入 OCR 识别任务。数据库使用 Python 标准库内置的 SQLite，不需要安装 PostgreSQL、MySQL 或其他数据库服务。

初始化空数据库：

```powershell
python init_equipment_db.py
```

初始化脚本同时写入 V2.2 装备字典：装备类别、槽位、套装等级、品质、词条字典、槽位规则、48 套标准套装、套装效果、OCR 别名、Mythic +16 主词条标准值、套装升华关系和异化效果字典。当前固定规则为满级英雄 60 级、只计算 Mythic 装备、暂不计算暗陨宝石；玩家具体装备实例仍由 OCR 或手工录入。

默认文件为 `data/equipment.db`，也可以指定路径：

```powershell
python init_equipment_db.py --database data/test-equipment.db
```

当前实现覆盖数据库表结构、完整技能输入、静态面板、60 秒事件驱动直接伤害模拟、单体/多目标木桩和 Top-K 合法组合搜索。使用 `EquipmentOptimizer.search(hero_id, mode, enemy_count, top_k)` 获取 V1.1 结果，其中 `mode` 为 `single` 或 `aoe`，群体模式的 `enemy_count` 由调用方指定。`direct_damage=FALSE` 的 DoT 事件不计入伤害，并将结果标记为 `model_coverage=partial`。

旧版 `HeroDamageProfile` 和 `search(hero_id, scenario_id, top_k=...)` 仍保留兼容读取，但不作为 V1.1 主排序模型。真实英雄、装备、技能和 GameRules 数据需要按方案文档录入；默认规则仅用于最小运行示例，不能视为已校准的游戏公式。

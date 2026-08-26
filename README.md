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

## 监督式快速扫描

`equipment_fast_scan.py` 提供面向“画面稳定且有人监督”场景的快速路径，`run_equipment_scan.py` 默认优先使用该模式。优化点包括：

- 复用扫描器已经取得的当前截图，不再为每件装备额外获取一次点击前基线图；
- 如果目标格已经处于选中状态，直接复用当前详情帧，不再重复点击并等待超时；
- 普通装备点击后默认等待 0.20 秒，只取一张后续帧确认详情区域发生变化；首帧过早时只额外补取一帧；
- 细粒度字段坐标已经由人工标注，因此正常路径只加载 `PP-OCRv5_mobile_rec`，直接对 ROI 做内存批量文本识别，不再对每个固定 ROI 重复执行文本检测；
- 正常路径不把每个字段裁剪图写入磁盘；只有低置信度或字段违反简单领域约束时才懒加载原 `PaddleOcrV5Mobile` 做 detector+recognizer 回退并保存对应 fallback 裁剪图；
- OCR 仍由单工作线程与截图/点击生产流程重叠，避免同一 Paddle 模型被多线程并发调用。

运行：

```powershell
conda activate EAIA
python run_equipment_scan.py
```

正常启用快速模式时会输出：

```text
SCAN_MODE=fast
SCAN_COMPLETE mode=fast records=... elapsed_s=... items_per_s=...
```

如果本地 PaddleOCR 版本无法初始化 `TextRecognition`，入口会打印 `FAST_SCAN_UNAVAILABLE` 并自动回退原 `PaddleOcrV5Mobile` 扫描器。数据标注缺失、数据库错误等非兼容性问题不会被自动隐藏。

当前快速实现仍保留 HDC `snapshot_display + file recv` 作为帧来源，以保证与现有环境直接兼容；但截图调用次数已显著减少。仓库目前没有 HOScrcpy 解码后视频帧的 Python 导出接口，因此尚未把帧来源强行绑定到某个未验证的 HOScrcpy 内部 API。后续如果提供稳定的视频帧回调，可直接替换 `capture` 后端，而 OCR 和扫描快速路径无需重新设计。

## 细化装备信息

`equipment_regions.py` 会读取 `captures/exclusive` 和 `captures/general` 中的红框标注，自动映射回原始屏幕坐标。流程先识别 `专属标识`：OCR 含有“专属”时使用专属区域，否则使用通用区域。细化结果包含品质、部位、主词条、套装名称和最多 4 条副词条；缺少的副词条保留为空，包含“解锁”的副词条记录 `value=-1`，并将 `fully_unlocked` 设为 `false`。

原始兼容流程会把细化裁剪图保存在 `fine_ocr_results`。快速模式的正常识别在内存中完成，只有回退或显式调试时才保存字段裁剪图；完整结果仍写入 `ocr_results_fast/ocr_results.jsonl` 并持久化到装备数据库。

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

### 网页与数据库 API

网页通过 Python API 实时读取 SQLite，不再加载 `frontend/public` 或构建目录中的 JSON 导出文件。先构建前端，再启动 API 和静态文件服务：

```powershell
cd frontend
npm run build
cd ..
conda run -n EAIA python web_api.py
```

服务地址为 `http://127.0.0.1:8000/`，接口包括 `/api/heroes`、`/api/catalog`、`/api/equipment` 和 `/api/health`。开发时可另开终端运行 `npm run dev`；Vite 已将 `/api` 代理到 8000 端口。

当前实现覆盖数据库表结构、完整技能输入、静态面板、60 秒事件驱动直接伤害模拟、单体/多目标木桩和 Top-K 合法组合搜索。使用 `EquipmentOptimizer.search(hero_id, mode, enemy_count, top_k)` 获取 V1.1 结果，其中 `mode` 为 `single` 或 `aoe`，群体模式的 `enemy_count` 由调用方指定。`direct_damage=FALSE` 的 DoT 事件不计入伤害，并将结果标记为 `model_coverage=partial`。

旧版 `HeroDamageProfile` 和 `search(hero_id, scenario_id, top_k=...)` 仍保留兼容读取，但不作为 V1.1 主排序模型。真实英雄、装备、技能和 GameRules 数据需要按方案文档录入；默认规则仅用于最小运行示例，不能视为已校准的游戏公式。

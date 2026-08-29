# 装备扫描：截图与 OCR 完全解耦

## 目标

默认装备扫描改为两个严格分离的阶段：

1. **Capture phase**：只通过 HDC 点击、滑动、做图像变化校验并保存稳定的装备详情截图；不初始化、不调用任何 OCR 模型。
2. **Offline recognition phase**：截图全部结束并释放手机后，才初始化 PP-OCRv5，对本地 session 中的截图进行识别、字段解析和数据库持久化。

因此 OCR 无论运行多久，都不会继续占用手机。即使 OCR 初始化失败、识别过程中断或 EAIA 重启，已经完成的截图 session 仍可重复用于离线识别。

## Session 结构

每次扫描创建独立目录：

```text
captures/equipment_sessions/<session_id>/
├── manifest.json
├── frames/
│   ├── item_000001_r001_c01.jpg
│   ├── item_000002_r001_c02.jpg
│   └── ...
├── ocr_results/
└── fine_ocr/
```

`manifest.json` 记录行列位置、点击位置、稳定帧路径、识别状态以及 `device_released`。采集时 HDC 产生的临时验证帧保存在 `working/`，采集完成后自动清理，只保留最终用于 OCR 的稳定帧。

## 默认运行

```powershell
conda run -n EAIA python run_equipment_scan.py
```

默认行为仍是一键执行完整流程，但实际顺序已经变为：

```text
capture all -> CAPTURE_COMPLETE / phone_released=true -> initialize OCR -> offline OCR -> database
```

看到 `CAPTURE_COMPLETE ... phone_released=true` 后，后续流程不再访问设备。

## 只截图

```powershell
conda run -n EAIA python run_equipment_scan.py --capture-only
```

该模式不会初始化 PaddleOCR。完成后会输出 session 路径，此时可以直接自由使用手机。

## 识别已有截图

```powershell
conda run -n EAIA python run_equipment_scan.py --recognize-session captures/equipment_sessions/<session_id>
```

该命令只读取 session 本地文件，不发送 HDC 截图、点击或滑动命令。已标记为成功识别的帧默认跳过，所以中断后可以继续执行。

## 扫描控制原则

采集阶段不再使用 OCR 文本或 `equipment_signature` 决定是否继续。设备侧控制仅依赖：

- 装备格占用的图像亮度判定；
- 点击前后详情区域图像变化；
- 滑动前后装备网格图像变化；
- 当前选中框的视觉检测。

这保证 OCR 推理速度不会反向阻塞手机扫描状态机。

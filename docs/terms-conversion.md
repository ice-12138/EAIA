# 术语表转换

使用根目录脚本把 Markdown 对照表转换成前端可读取的 JSON：

```powershell
python scripts/convert_terms.py `
  --input 'C:\Users\lenovo\Desktop\潮汐守望者_游戏术语中英对照表_2026-08.md' `
  --output 'frontend\public\terms.json'
```

生成文件包含：

- `terms`：按数据库 ID 索引的中英文显示词
- `aliases`：OCR/玩家别名到标准术语 ID 的映射
- `lookup`：大小写、分隔符归一化后的查询键到标准术语 ID 的映射，例如 `ATK_PCT` 会解析为 `atk_pct`
- `contextRules`：需要结合上下文判断的同名术语
- `sections`：原始 Markdown 表格的结构化记录

网页显示时应使用 `terms[termId]['zh-CN']` 或 `terms[termId]['en-US']`，不要把中文或英文直接写进业务数据。

查询用户输入时，先对输入执行小写和非字母数字字符归一化，再从 `lookup` 取标准 ID；无法解析时显示“未找到术语”，不要自动进行模糊合并。

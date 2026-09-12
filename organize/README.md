# OCR 文本整理模块

> 版本：V0.1（2026-09-12）｜状态：生效

`organize/` 是独立的、确定性的 OCR 文本整理模块。它只修复 Unicode、OCR 空格、结构字段、
断行、分页标记、图片注释和连续空行，不识别实体、不修改实体语义，也不执行脱敏。

## 使用

```powershell
python -m organize --help
python -m organize .\test-artifacts\ocr-outputs\document-<safe-id>.ocr.md
python -m organize .\test-artifacts\ocr-outputs -o .\test-artifacts\organized-outputs
```

默认输出到 `test-artifacts/organized-outputs/`。输入为单文件时，`-o` 可以指定具体 Markdown
文件；输入为目录时，模块会递归处理 `.md`、`.markdown`、`.txt` 和 `.rtf`，输出文件名使用
`document-<safe-id>.organized.md`。

整理完成后再调用 `python -m desensitize mask ...`。如果需要恢复，必须使用脱敏模块生成的加密
mapping 和密码，整理模块本身不保存可恢复映射。

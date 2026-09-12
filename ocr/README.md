# OCR 文档转换模块

> 版本：V0.1（2026-09-12）｜状态：生效

`ocr/` 是独立的文档转换模块，移植自项目外部的 `document-converter` 工具。它使用 Docling
处理 PDF/DOCX/PPTX/XLSX，使用本地 RapidOCR 处理图片和扫描区域，并将项目文档合并为 Markdown。

## 安装

```powershell
python -m pip install -e ".[ocr]"
```

DirectML 和 CUDA 后端分别安装 `.[ocr-directml]`、`.[ocr-cuda]`。OCR 依赖是可选的，不会影响
`organize` 或 `desensitize` 的基础调用。

## 使用

```powershell
python -m ocr --help
python -m ocr --config config/ocr.yaml --root-dir .\test-artifacts\ocr-inputs --no-progress
```

默认配置：

- 输入：`test-artifacts/ocr-inputs/`，一级子目录作为项目；
- 输出：`test-artifacts/ocr-outputs/`；
- 日志：`test-artifacts/logs/ocr.log`；
- 输出文件：`document-<safe-id>.ocr.md`，不会使用项目名或输入文件名。

`--no-ocr` 可验证纯文本/电子文档转换链路；`--resume` 可在长任务中跳过已有安全输出。

## 支持格式

PDF、DOCX、PPTX、XLSX、JPG、JPEG、PNG、BMP、TIFF、GIF、TXT、MD、RTF、HTML、XML、JSON 和 CSV。

OCR 模块只负责转换，不做文本实体识别、白名单判断或脱敏。转换后可将安全命名的 Markdown
交给 `python -m organize`，再交给 `python -m desensitize`。
